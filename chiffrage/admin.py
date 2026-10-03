import datetime

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html
from unfold.decorators import action as unfold_action
from comptes.concurrence import VerrouOptimisteMixin
from comptes.historique import HistoriqueLectureSeule
from unfold.admin import ModelAdmin, TabularInline

from codification.mixins import CodificationInitialeMixin
from codification.models import RegleCodification
from commercial.models import TauxTVA

from .builder_views import (
    contact_associe_adresse_view,
    convertir_en_commande_view,
    devis_builder_view,
    previsualiser_ligne_commande_view,
    previsualiser_ligne_nouveau_devis_view,
    previsualiser_ligne_nouvelle_commande_view,
    previsualiser_ligne_view,
    recalculer_ligne_commande_view,
    recalculer_ligne_view,
    valeurs_defaut_tiers_view,
)
from .models import (
    Commande,
    CommandeLigne,
    CommandeLigneModification,
    CommandeError,
    Devis,
    DevisLigne,
    DevisLigneOperation,
    Livraison,
    LivraisonError,
    LivraisonLigne,
    OperationOF,
    OrdreFabrication,
)
from .documents import DocumentError, generer_pdf_bon_livraison, generer_pdf_devis
from .moteur import ChiffrageError, calculer_devis
from .planning_sync import resynchroniser
from .validation import verifier_validation_devis
from .production import (
    CHAMPS_SUIVIS_COMMANDE_LIGNE,
    enregistrer_modification_ligne,
    lancer_en_production,
    lancer_ligne_en_production,
    reviser_devis,
    synchroniser_lignes_commande,
)
from .widgets import DelaiWidget


def taux_tva_display(obj):
    """Juste le taux (ex. "20%"), sans le libellé du référentiel — utilisé
    dans les colonnes de liste (lecture seule). Le champ éditable, lui,
    utilise TauxTVACompactChoiceField ci-dessous pour le même rendu compact
    jusque dans les options du menu déroulant (gain de place sur la colonne
    des inlines "Lignes de devis" / "Lignes de commande")."""
    if not obj.taux_tva:
        return "—"
    return f"{obj.taux_tva.taux:g}%"


class TauxTVACompactChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f"{obj.taux:g}%"


class DevisLigneForm(forms.ModelForm):
    taux_tva = TauxTVACompactChoiceField(
        queryset=TauxTVA.objects.all(), required=False, label="Taux de TVA"
    )
    # Rempli en JS (devisligne_reorder.js) au glisser-déposer d'une ligne —
    # jamais affiché ni saisi à la main. required=False + repli sur 0 dans
    # clean_ordre() : un POST qui ne le fournit pas (JS désactivé, ou tout
    # code déjà existant qui poste ce formulaire sans le connaître) reste
    # accepté normalement, avec un ordre par défaut plutôt qu'une erreur
    # "champ obligatoire" qui n'a pas lieu d'être pour un simple confort
    # d'affichage.
    ordre = forms.IntegerField(required=False, widget=forms.HiddenInput())

    class Meta:
        model = DevisLigne
        fields = "__all__"

    def clean_ordre(self):
        return self.cleaned_data.get("ordre") or 0


def devis_verrouille(devis):
    """Un devis validé est figé : c'est le prix engagé auprès du client. Pour le
    modifier, il faut d'abord le repasser en brouillon (voir DevisAdminForm)."""
    if devis is None or devis.pk is None:
        return False
    # Relu en base plutôt que sur l'instance : après un POST invalide, le
    # formulaire a déjà réécrit l'instance avec le statut soumis, et le verrou
    # tomberait en plein rendu de la page d'erreur.
    return Devis.objects.filter(pk=devis.pk, statut=Devis.Statut.VALIDE).exists()


class DevisLigneInline(TabularInline):
    model = DevisLigne
    form = DevisLigneForm
    extra = 1
    autocomplete_fields = ["article"]
    readonly_fields = [
        "cout_matiere_calcule",
        "prix_vente_matiere",
        "prix_vente_operations",
        "prix_vente_total",
        "prix_vente_unitaire",
        "prix_vente_ttc",
    ]

    def has_add_permission(self, request, obj=None):
        return not devis_verrouille(obj) and super().has_add_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        return not devis_verrouille(obj) and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return not devis_verrouille(obj) and super().has_delete_permission(request, obj)


class DevisLigneOperationInline(TabularInline):
    model = DevisLigneOperation
    extra = 0
    readonly_fields = ["poste", "ordre", "cout_calcule", "prix_vente"]
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


class DevisAdminForm(forms.ModelForm):
    class Meta:
        model = Devis
        fields = "__all__"
        widgets = {"delai": DelaiWidget()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        champ = self.fields.get("issue")
        if champ is not None:
            champ.required = False  # absent d'un envoi : la valeur actuelle (ou « en attente ») est conservée
        if champ is not None and self.instance.pk:
            if self.instance.issue in (Devis.Issue.ACCEPTE, Devis.Issue.REMPLACE):
                # « Accepté » vient de la création de la commande, « Remplacé » de la révision :
                # jamais saisis à la main.
                champ.disabled = True
            else:
                champ.choices = [c for c in champ.choices if c[0] in (Devis.Issue.EN_ATTENTE, Devis.Issue.REFUSE)]
        elif champ is not None:
            champ.choices = [c for c in champ.choices if c[0] == Devis.Issue.EN_ATTENTE]

    def clean(self):
        cleaned = super().clean()
        # À ce stade l'instance porte encore le statut enregistré en base
        # (construct_instance n'a pas encore tourné).
        if devis_verrouille(self.instance) and cleaned.get("statut") == Devis.Statut.BROUILLON:
            if self.instance.commandes.exists():
                raise forms.ValidationError(
                    "Une commande est déjà issue de ce devis : impossible de le repasser en brouillon."
                )
        return cleaned


class ExpireFilter(admin.SimpleListFilter):
    title = "validité de l'offre"
    parameter_name = "validite"

    def lookups(self, request, model_admin):
        return [("expire", "Expirés"), ("bientot", "Expirent sous 7 jours")]

    def queryset(self, request, queryset):
        aujourdhui = timezone.localdate()
        en_attente = queryset.filter(statut=Devis.Statut.VALIDE, issue=Devis.Issue.EN_ATTENTE, date_validite__isnull=False)
        if self.value() == "expire":
            return en_attente.filter(date_validite__lt=aujourdhui)
        if self.value() == "bientot":
            return en_attente.filter(date_validite__gte=aujourdhui, date_validite__lte=aujourdhui + datetime.timedelta(days=7))
        return queryset


@admin.register(Devis)
class DevisAdmin(VerrouOptimisteMixin, CodificationInitialeMixin, HistoriqueLectureSeule, ModelAdmin):
    codification_entite = RegleCodification.Entite.DEVIS
    form = DevisAdminForm

    list_display = [
        "numero",
        "client",
        "date_creation",
        "statut",
        "issue",
        "date_validite",
        "delai",
        "taux_marge_globale",
        "montant_matiere_ht",
        "montant_operations_ht",
        "montant_total_ht",
        "montant_total_ttc",
    ]
    list_filter = ["statut", "issue", ExpireFilter]
    search_fields = ["numero", "client__raison_sociale"]
    autocomplete_fields = ["client", "adresse_facturation", "adresse_livraison", "contact"]
    readonly_fields = [
        "montant_matiere_ht_display",
        "montant_operations_ht_display",
        "montant_total_ht_display",
        "montant_total_ttc_display",
    ]
    inlines = [DevisLigneInline]
    actions = ["action_recalculer", "action_lancer_en_production"]
    actions_detail = ["action_pdf", "action_reviser"]

    def get_readonly_fields(self, request, obj=None):
        champs = list(super().get_readonly_fields(request, obj))
        if not request.user.has_perm("chiffrage.valider_devis"):
            # Seul un utilisateur habilité valide un devis (ou le repasse en brouillon).
            champs.append("statut")
        if devis_verrouille(obj):
            # Tout sauf le statut, pour pouvoir repasser le devis en brouillon.
            # La réponse du client et la date de validité ne changent pas le prix engagé : elles
            # restent modifiables (prolonger une offre, noter un refus).
            libres = {"statut", "issue", "motif_refus", "date_validite"}
            champs += [f.name for f in Devis._meta.concrete_fields if f.name not in libres]
        return champs

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        devis = form.instance
        if "statut" not in form.changed_data or devis.statut != Devis.Statut.VALIDE:
            return
        # Les lignes viennent d'être enregistrées : c'est maintenant qu'on peut
        # contrôler (et chiffrer) le devis tel qu'il va être validé.
        if request.user.has_perm("chiffrage.valider_devis"):
            raisons = verifier_validation_devis(
                devis, peut_vendre_sous_cout=request.user.has_perm("chiffrage.valider_vente_sous_cout")
            )
        else:
            raisons = ["Vous n'avez pas la permission de valider un devis."]
        if raisons:
            # Via save() (pas update()) pour que l'historique montre la tentative
            # de validation ET son annulation, avec le motif.
            devis.statut = Devis.Statut.BROUILLON
            devis._change_reason = ("Validation refusée : " + " ".join(raisons))[:100]
            devis.save(update_fields=["statut"])
            for raison in raisons:
                messages.error(request, f"{devis} : validation refusée, le devis reste en brouillon. {raison}")

    class Media:
        js = ["chiffrage/devis_admin_live.js", "chiffrage/devisligne_reorder.js"]
        css = {"all": ["chiffrage/devis_admin_live.css"]}

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("lignes__operations", "lignes__taux_tva")

    # Wrappés dans un <span id="..."> (plutôt que les propriétés du modèle
    # directement) pour offrir un point d'accroche stable au JS de recalcul
    # en direct (Unfold ne pose pas de classe `field-<nom>` sur les champs
    # readonly de premier niveau, contrairement à ses tableaux inline).
    @admin.display(description="Montant matière HT")
    def montant_matiere_ht_display(self, obj):
        return format_html('<span id="montant-matiere-ht">{}</span>', obj.montant_matiere_ht)

    @admin.display(description="Montant opérations HT (temps machine / main d'œuvre)")
    def montant_operations_ht_display(self, obj):
        return format_html('<span id="montant-operations-ht">{}</span>', obj.montant_operations_ht)

    @admin.display(description="Montant total HT")
    def montant_total_ht_display(self, obj):
        return format_html('<span id="montant-total-ht">{}</span>', obj.montant_total_ht)

    @admin.display(description="Montant total TTC")
    def montant_total_ttc_display(self, obj):
        return format_html('<span id="montant-total-ttc">{}</span>', obj.montant_total_ttc)

    def response_add(self, request, obj, post_url_continue=None):
        # Bouton "Enregistrer et ouvrir le constructeur" du formulaire d'ajout :
        # le devis (et ses lignes déjà saisies dans l'inline) vient d'être
        # enregistré normalement par la vue d'admin ; on redirige simplement
        # vers le constructeur au lieu de la liste/fiche par défaut.
        if "_construire" in request.POST:
            return HttpResponseRedirect(reverse("admin:chiffrage_devis_builder", args=[obj.pk]))
        return super().response_add(request, obj, post_url_continue)

    def get_urls(self):
        urls = [
            # Chemin fixe (pas de <str:numero>) : utilisé sur le formulaire
            # d'AJOUT d'un devis, qui n'a par définition pas encore de numéro
            # (l'objet Devis n'existe pas encore en base).
            path(
                "nouveau-devis/previsualiser-ligne/",
                self.admin_site.admin_view(previsualiser_ligne_nouveau_devis_view),
                name="chiffrage_devisligne_previsualiser_nouveau_devis",
            ),
            path(
                "tiers/<str:code>/valeurs-defaut/",
                self.admin_site.admin_view(valeurs_defaut_tiers_view),
                name="chiffrage_devis_valeurs_defaut_tiers",
            ),
            path(
                "adresses/<int:adresse_id>/contact-associe/",
                self.admin_site.admin_view(contact_associe_adresse_view),
                name="chiffrage_devis_contact_associe_adresse",
            ),
            path(
                "<str:numero>/constructeur/",
                self.admin_site.admin_view(devis_builder_view),
                name="chiffrage_devis_builder",
            ),
            path(
                "<str:numero>/convertir-commande/",
                self.admin_site.admin_view(convertir_en_commande_view),
                name="chiffrage_devis_convertir_commande",
            ),
            path(
                "<str:numero>/lignes/<int:ligne_id>/recalculer/",
                self.admin_site.admin_view(recalculer_ligne_view),
                name="chiffrage_devisligne_recalculer",
            ),
            path(
                "<str:numero>/lignes/previsualiser/",
                self.admin_site.admin_view(previsualiser_ligne_view),
                name="chiffrage_devisligne_previsualiser",
            ),
        ]
        return urls + super().get_urls()

    @admin.action(description="Recalculer le chiffrage")
    def action_recalculer(self, request, queryset):
        for devis in queryset:
            if devis_verrouille(devis):
                self.message_user(
                    request,
                    f"{devis} : devis validé, donc verrouillé — son chiffrage n'est pas recalculé.",
                    level=messages.WARNING,
                )
                continue
            try:
                calculer_devis(devis)
            except ChiffrageError as exc:
                self.message_user(request, f"{devis} : {exc}", level=messages.ERROR)
            else:
                self.message_user(request, f"{devis} : chiffrage recalculé.", level=messages.SUCCESS)

    @admin.action(description="Lancer en production")
    def action_lancer_en_production(self, request, queryset):
        for devis in queryset:
            try:
                commande = lancer_en_production(devis)
            except ChiffrageError as exc:
                self.message_user(request, f"{devis} : {exc}", level=messages.ERROR)
            else:
                self.message_user(
                    request, f"{devis} : commande {commande} créée.", level=messages.SUCCESS
                )


    @unfold_action(description="PDF du devis", url_path="pdf")
    def action_pdf(self, request, object_id):
        devis = Devis.objects.get(pk=object_id)
        if not self.has_view_permission(request, devis):
            raise PermissionDenied
        try:
            contenu = generer_pdf_devis(devis)
        except DocumentError as exc:
            self.message_user(request, str(exc), level=messages.ERROR)
            return HttpResponseRedirect(reverse("admin:chiffrage_devis_change", args=[devis.pk]))
        reponse = HttpResponse(contenu, content_type="application/pdf")
        reponse["Content-Disposition"] = f'inline; filename="devis-{devis.pk}.pdf"'
        return reponse

    @unfold_action(description="Réviser ce devis", permissions=["reviser"], url_path="reviser")
    def action_reviser(self, request, object_id):
        devis = Devis.objects.get(pk=object_id)
        try:
            revision = reviser_devis(devis)
        except ChiffrageError as exc:
            self.message_user(request, str(exc), level=messages.ERROR)
            return HttpResponseRedirect(reverse("admin:chiffrage_devis_change", args=[devis.pk]))
        self.message_user(
            request,
            f"Révision {revision} créée en brouillon ; {devis} est marqué « remplacé ». "
            "Modifiez-la, chiffrez-la puis validez-la.",
            level=messages.SUCCESS,
        )
        return HttpResponseRedirect(reverse("admin:chiffrage_devis_change", args=[revision.pk]))

    def has_reviser_permission(self, request, obj=None):
        return request.user.has_perm("chiffrage.change_devis")


@admin.register(DevisLigne)
class DevisLigneAdmin(HistoriqueLectureSeule, ModelAdmin):
    form = DevisLigneForm
    list_display = [
        "devis",
        "article",
        "quantite",
        "cout_matiere_calcule",
        "prix_vente_matiere",
        "prix_vente_operations",
        "prix_vente_total",
        "prix_vente_unitaire",
        "taux_tva_display",
        "prix_vente_ttc",
    ]
    search_fields = ["devis__numero", "article__reference"]
    autocomplete_fields = ["devis", "article"]
    inlines = [DevisLigneOperationInline]

    def has_change_permission(self, request, obj=None):
        return not (obj is not None and devis_verrouille(obj.devis)) and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return not (obj is not None and devis_verrouille(obj.devis)) and super().has_delete_permission(request, obj)

    @admin.display(description="Taux de TVA")
    def taux_tva_display(self, obj):
        return taux_tva_display(obj)


class CommandeLigneForm(forms.ModelForm):
    taux_tva = TauxTVACompactChoiceField(
        queryset=TauxTVA.objects.all(), required=False, label="Taux de TVA"
    )

    class Meta:
        model = CommandeLigne
        fields = "__all__"


def date_livraison_possible_display(obj):
    # Unfold n'applique le format localisé (jj/mm/aaaa) qu'aux vrais champs
    # de modèle : une propriété readonly comme date_livraison_possible passe
    # par str(date) (linebreaksbr), donc en ISO (aaaa-mm-jj) sans ce
    # contournement — on formate nous-mêmes pour rester cohérent avec les
    # autres dates affichées sur l'écran.
    date = obj.date_livraison_possible
    return date.strftime("%d/%m/%Y") if date else "—"


def _logger_modifications_ligne(form, utilisateur):
    for champ in CHAMPS_SUIVIS_COMMANDE_LIGNE:
        if champ not in form.changed_data:
            continue
        enregistrer_modification_ligne(
            form.instance, champ, form.initial.get(champ), form.cleaned_data.get(champ), utilisateur
        )


def _avertir_si_augmentation_apres_of(request, ligne, ancienne_quantite):
    if ancienne_quantite is None or ligne.quantite_commandee <= ancienne_quantite:
        return
    if OrdreFabrication.objects.filter(commande=ligne.commande, article=ligne.article).exists():
        messages.warning(
            request,
            f"« {ligne.article} » : quantité augmentée alors qu'un ordre de fabrication existe déjà pour "
            "cette commande — il ne sera pas recalculé. Ajoutez plutôt une nouvelle ligne pour la quantité "
            "supplémentaire (elle pourra être lancée en production séparément).",
        )


class CommandeLigneModificationInline(TabularInline):
    model = CommandeLigneModification
    extra = 0
    can_delete = False
    fields = ["champ", "ancienne_valeur", "nouvelle_valeur", "utilisateur", "date_modification"]
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


class CommandeLigneInline(TabularInline):
    model = CommandeLigne
    form = CommandeLigneForm
    extra = 0
    can_delete = False
    autocomplete_fields = ["article"]
    fields = [
        "article",
        "designation",
        "quantite_commandee",
        "prix_vente_unitaire",
        "taux_tva",
        "montant_ht",
        "montant_ttc",
        "date_livraison_prevue",
        "date_livraison_possible_display",
        "statut_approvisionnement",
        "quantite_livree",
        "reliquat",
        "entierement_livree",
        "quantite_facturee",
        "reste_a_facturer",
    ]
    readonly_fields = [
        "montant_ht",
        "montant_ttc",
        "date_livraison_possible_display",
        "statut_approvisionnement",
        "quantite_livree",
        "reliquat",
        "entierement_livree",
        "quantite_facturee",
        "reste_a_facturer",
    ]

    @admin.display(description="Date de livraison possible (appro)")
    def date_livraison_possible_display(self, obj):
        return date_livraison_possible_display(obj)


@admin.register(Commande)
class CommandeAdmin(VerrouOptimisteMixin, CodificationInitialeMixin, HistoriqueLectureSeule, ModelAdmin):
    codification_entite = RegleCodification.Entite.COMMANDE

    list_display = ["numero", "client", "reference_client", "devis", "date_commande", "statut", "devise"]
    list_filter = ["statut"]
    search_fields = ["numero", "reference_client", "client__raison_sociale", "devis__numero"]
    autocomplete_fields = ["devis", "client", "adresse_facturation", "adresse_livraison", "devise"]
    # Le statut ne se saisit pas : « soldée » est déduit des livraisons, « annulée »
    # passe par l'action dédiée (qui refuse une commande déjà livrée).
    readonly_fields = ["statut"]
    inlines = [CommandeLigneInline]
    actions = ["action_synchroniser_lignes", "action_annuler"]

    class Media:
        js = ["chiffrage/commande_admin_live.js"]

    def get_urls(self):
        urls = [
            # Chemin fixe : formulaire d'AJOUT (pas encore de numéro).
            path(
                "nouvelle-commande/previsualiser-ligne/",
                self.admin_site.admin_view(previsualiser_ligne_nouvelle_commande_view),
                name="chiffrage_commandeligne_previsualiser_nouvelle_commande",
            ),
            path(
                "<str:numero>/lignes/<int:ligne_id>/recalculer/",
                self.admin_site.admin_view(recalculer_ligne_commande_view),
                name="chiffrage_commandeligne_recalculer",
            ),
            path(
                "<str:numero>/lignes/previsualiser/",
                self.admin_site.admin_view(previsualiser_ligne_commande_view),
                name="chiffrage_commandeligne_previsualiser",
            ),
        ]
        return urls + super().get_urls()

    @admin.action(description="Synchroniser les lignes depuis le devis")
    def action_synchroniser_lignes(self, request, queryset):
        # Filet de sécurité : recrée les lignes de commande manquantes par
        # rapport au devis d'origine (ex. commande créée avant l'ajout de ce
        # mécanisme) et relie les lignes existantes à leur ligne de devis
        # quand ce n'est pas encore fait (pour afficher prix/TVA).
        total_creees = 0
        for commande in queryset:
            total_creees += len(synchroniser_lignes_commande(commande))
        self.message_user(
            request, f"{total_creees} ligne(s) de commande recréée(s).", level=messages.SUCCESS
        )

    @admin.action(description="Annuler la commande")
    def action_annuler(self, request, queryset):
        if not request.user.has_perm("chiffrage.annuler_commande"):
            self.message_user(request, "Vous n'avez pas la permission d'annuler une commande.", level=messages.ERROR)
            return
        for commande in queryset:
            try:
                commande.annuler()
            except CommandeError as exc:
                self.message_user(request, str(exc), level=messages.ERROR)
                continue
            nb_of = commande.ordres_fabrication.count()
            suite = f" {nb_of} ordre(s) de fabrication restent à arrêter dans le planning." if nb_of else ""
            self.message_user(request, f"{commande} : commande annulée.{suite}", level=messages.SUCCESS)

    def save_formset(self, request, form, formset, change):
        if formset.model is not CommandeLigne:
            return super().save_formset(request, form, formset, change)

        # form.initial (pas form.instance : _post_clean() a déjà réécrit
        # l'instance avec les valeurs soumises au moment de la validation du
        # formset, bien avant save_formset) donne la valeur telle qu'elle
        # était en base au moment de l'affichage du formulaire.
        anciennes_quantites = {f.instance.pk: f.initial.get("quantite_commandee") for f in formset.forms if f.instance.pk}
        super().save_formset(request, form, formset, change)
        for f in formset.forms:
            if not f.has_changed() or f.cleaned_data.get("DELETE"):
                continue
            _logger_modifications_ligne(f, request.user)
            _avertir_si_augmentation_apres_of(request, f.instance, anciennes_quantites.get(f.instance.pk))


@admin.register(CommandeLigne)
class CommandeLigneAdmin(ModelAdmin):
    form = CommandeLigneForm
    list_display = [
        "commande",
        "article",
        "designation",
        "quantite_commandee",
        "prix_vente_unitaire",
        "taux_tva_display",
        "date_livraison_prevue",
        "date_livraison_possible_display",
        "quantite_livree",
        "reliquat",
        "entierement_livree",
    ]
    list_filter = ["commande"]
    search_fields = ["commande__numero", "article__reference", "designation"]
    autocomplete_fields = ["commande", "article"]
    inlines = [CommandeLigneModificationInline]
    actions = ["action_lancer_en_production"]
    readonly_fields = [
        "devis_ligne",
        "quantite_livree",
        "montant_ht",
        "montant_ttc",
        "date_livraison_possible_display",
        "statut_approvisionnement",
    ]

    @admin.display(description="Taux de TVA")
    def taux_tva_display(self, obj):
        return taux_tva_display(obj)

    @admin.display(description="Date de livraison possible (appro)")
    def date_livraison_possible_display(self, obj):
        return date_livraison_possible_display(obj)

    @admin.action(description="Lancer cette ligne en production (OF)")
    def action_lancer_en_production(self, request, queryset):
        for ligne in queryset:
            try:
                of = lancer_ligne_en_production(ligne)
            except ChiffrageError as exc:
                self.message_user(request, f"{ligne} : {exc}", level=messages.ERROR)
            else:
                self.message_user(request, f"{ligne} : ordre de fabrication {of} créé.", level=messages.SUCCESS)

    def save_model(self, request, obj, form, change):
        ancienne_quantite = None
        if change:
            ancienne_quantite = CommandeLigne.objects.get(pk=obj.pk).quantite_commandee
        super().save_model(request, obj, form, change)
        if change:
            _logger_modifications_ligne(form, request.user)
            _avertir_si_augmentation_apres_of(request, obj, ancienne_quantite)


@admin.register(CommandeLigneModification)
class CommandeLigneModificationAdmin(ModelAdmin):
    list_display = ["commande_ligne", "champ", "ancienne_valeur", "nouvelle_valeur", "utilisateur", "date_modification"]
    list_filter = ["champ"]
    search_fields = ["commande_ligne__commande__numero", "commande_ligne__article__reference"]
    readonly_fields = ["commande_ligne", "champ", "ancienne_valeur", "nouvelle_valeur", "utilisateur", "date_modification"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class LivraisonLigneInline(TabularInline):
    """Lignes immuables : on en ajoute (livraison saisie en plusieurs fois) mais on ne
    modifie ni ne supprime une ligne enregistrée — on annule la livraison."""

    model = LivraisonLigne
    extra = 1
    autocomplete_fields = ["commande_ligne", "lot"]

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_add_permission(self, request, obj=None):
        return (obj is None or obj.statut == Livraison.Statut.VALIDEE) and super().has_add_permission(request, obj)


@admin.register(Livraison)
class LivraisonAdmin(VerrouOptimisteMixin, CodificationInitialeMixin, HistoriqueLectureSeule, ModelAdmin):
    codification_entite = RegleCodification.Entite.LIVRAISON

    list_display = ["numero", "commande", "date_livraison", "statut"]
    list_filter = ["statut"]
    search_fields = ["numero", "commande__numero"]
    autocomplete_fields = ["commande"]
    inlines = [LivraisonLigneInline]
    actions = ["action_annuler"]
    actions_detail = ["action_pdf"]

    @unfold_action(description="Bon de livraison (PDF)", url_path="pdf")
    def action_pdf(self, request, object_id):
        livraison = Livraison.objects.get(pk=object_id)
        if not self.has_view_permission(request, livraison):
            raise PermissionDenied
        try:
            contenu = generer_pdf_bon_livraison(livraison)
        except DocumentError as exc:
            self.message_user(request, str(exc), level=messages.ERROR)
            return HttpResponseRedirect(reverse("admin:chiffrage_livraison_change", args=[livraison.pk]))
        reponse = HttpResponse(contenu, content_type="application/pdf")
        reponse["Content-Disposition"] = f'inline; filename="bl-{livraison.pk}.pdf"'
        return reponse

    def get_readonly_fields(self, request, obj=None):
        champs = ["statut", "date_annulation", "motif_annulation", "utilisateur_annulation"]
        if obj is not None and obj.pk:
            champs += ["numero", "commande"]
        return champs

    def has_delete_permission(self, request, obj=None):
        return False

    def save_formset(self, request, form, formset, change):
        try:
            super().save_formset(request, form, formset, change)
        except LivraisonError as exc:
            self.message_user(request, str(exc), level=messages.ERROR)

    def get_urls(self):
        urls = [
            path(
                "<str:numero>/annuler/",
                self.admin_site.admin_view(self.annuler_view),
                name="chiffrage_livraison_annuler",
            ),
        ]
        return urls + super().get_urls()

    @admin.action(description="Annuler la livraison", permissions=["annuler"])
    def action_annuler(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, "Sélectionnez une seule livraison à annuler.", level=messages.ERROR)
            return None
        return HttpResponseRedirect(reverse("admin:chiffrage_livraison_annuler", args=[queryset.get().pk]))

    def has_annuler_permission(self, request):
        return request.user.has_perm("chiffrage.annuler_livraison")

    def annuler_view(self, request, numero):
        livraison = Livraison.objects.get(pk=numero)
        retour = reverse("admin:chiffrage_livraison_changelist")
        if not request.user.has_perm("chiffrage.annuler_livraison"):
            self.message_user(request, "Vous n'avez pas la permission d'annuler une livraison.", level=messages.ERROR)
            return HttpResponseRedirect(retour)
        if request.method == "POST":
            try:
                livraison.annuler(utilisateur=request.user, motif=request.POST.get("motif", "").strip())
            except LivraisonError as exc:
                self.message_user(request, str(exc), level=messages.ERROR)
            else:
                self.message_user(request, f"Livraison {livraison} annulée : quantités livrées et stock rétablis.", level=messages.SUCCESS)
            return HttpResponseRedirect(retour)
        return TemplateResponse(
            request,
            "admin/confirmer_avec_motif.html",
            {
                **self.admin_site.each_context(request),
                "title": "Annuler une livraison",
                "explication": (
                    f"La livraison {livraison} sera annulée : le cumul livré de ses lignes et le stock "
                    "(sorties contre-passées) sont rétablis, la commande est rouverte. "
                    "La livraison reste consultable."
                ),
                "avertissement": "",
                "bouton": "Annuler la livraison",
                "retour": retour,
            },
        )


@admin.register(LivraisonLigne)
class LivraisonLigneAdmin(ModelAdmin):
    list_display = ["livraison", "commande_ligne", "quantite_livree"]
    search_fields = ["livraison__numero", "commande_ligne__article__reference"]
    autocomplete_fields = ["livraison", "commande_ligne"]

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class OperationOFInline(TabularInline):
    model = OperationOF
    extra = 0


@admin.register(OrdreFabrication)
class OrdreFabricationAdmin(CodificationInitialeMixin, ModelAdmin):
    codification_entite = RegleCodification.Entite.ORDRE_FABRICATION

    list_display = [
        "numero",
        "commande",
        "article",
        "quantite",
        "statut",
        "statut_synchro",
        "nombre_tentatives",
        "erreur_courte",
    ]
    list_filter = ["statut_synchro"]
    readonly_fields = ["statut_synchro", "nombre_tentatives", "date_derniere_tentative", "derniere_erreur", "prochaine_tentative"]
    search_fields = ["numero", "commande__numero", "article__reference"]
    autocomplete_fields = ["commande", "article"]
    inlines = [OperationOFInline]
    actions = ["action_resynchroniser"]

    @admin.display(description="Dernière erreur")
    def erreur_courte(self, obj):
        erreur = obj.derniere_erreur
        return (erreur[:60] + "…") if len(erreur) > 60 else (erreur or "—")

    @admin.action(description="Resynchroniser avec le planning atelier")
    def action_resynchroniser(self, request, queryset):
        for of in queryset:
            reussite = resynchroniser(of)
            niveau = messages.SUCCESS if reussite else messages.WARNING
            statut = "synchronisé" if reussite else f"toujours en échec ({of.statut_synchro})"
            self.message_user(request, f"{of} : {statut}.", level=niveau)


@admin.register(OperationOF)
class OperationOFAdmin(ModelAdmin):
    list_display = ["ordre_fabrication", "ordre", "poste", "temps_prevu", "temps_reel", "statut"]
    search_fields = ["ordre_fabrication__numero", "poste__nom"]
    autocomplete_fields = ["ordre_fabrication", "poste"]
