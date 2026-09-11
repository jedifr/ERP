from django import forms
from django.contrib import admin, messages
from django.http import HttpResponseRedirect
from django.urls import path, reverse
from django.utils.html import format_html
from unfold.admin import ModelAdmin, TabularInline

from codification.mixins import CodificationInitialeMixin
from codification.models import RegleCodification
from commercial.models import TauxTVA

from .builder_views import (
    contact_associe_adresse_view,
    convertir_en_commande_view,
    devis_builder_view,
    previsualiser_ligne_nouveau_devis_view,
    previsualiser_ligne_view,
    recalculer_ligne_view,
    valeurs_defaut_tiers_view,
    valider_commande_directe_view,
)
from .models import (
    Commande,
    CommandeLigne,
    CommandeLigneModification,
    Devis,
    DevisLigne,
    DevisLigneOperation,
    Livraison,
    LivraisonError,
    LivraisonLigne,
    OperationOF,
    OrdreFabrication,
)
from .moteur import ChiffrageError, calculer_devis
from .planning_sync import resynchroniser
from .production import (
    CHAMPS_SUIVIS_COMMANDE_LIGNE,
    enregistrer_modification_ligne,
    lancer_en_production,
    lancer_ligne_en_production,
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


@admin.register(Devis)
class DevisAdmin(CodificationInitialeMixin, ModelAdmin):
    codification_entite = RegleCodification.Entite.DEVIS
    form = DevisAdminForm

    list_display = [
        "numero",
        "client",
        "date_creation",
        "statut",
        "delai",
        "taux_marge_globale",
        "montant_matiere_ht",
        "montant_operations_ht",
        "montant_total_ht",
        "montant_total_ttc",
    ]
    list_filter = ["statut"]
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
        # Bouton "Créer une commande directement" : même principe, mais le
        # constructeur s'ouvre en mode "commande directe" (?commande_directe=1)
        # — ce devis ne sert que de support de calcul interne, jamais montré
        # au client ; un bouton y permet de le valider et d'enchaîner
        # aussitôt sur lancer_en_production (voir builder_views.valider_commande_directe_view).
        if "_construire_commande" in request.POST:
            url = reverse("admin:chiffrage_devis_builder", args=[obj.pk]) + "?commande_directe=1"
            return HttpResponseRedirect(url)
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
                "<str:numero>/valider-commande/",
                self.admin_site.admin_view(valider_commande_directe_view),
                name="chiffrage_devis_valider_commande",
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


@admin.register(DevisLigne)
class DevisLigneAdmin(ModelAdmin):
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
    ]
    readonly_fields = [
        "montant_ht",
        "montant_ttc",
        "date_livraison_possible_display",
        "statut_approvisionnement",
        "quantite_livree",
        "reliquat",
        "entierement_livree",
    ]

    @admin.display(description="Date de livraison possible (appro)")
    def date_livraison_possible_display(self, obj):
        return date_livraison_possible_display(obj)


@admin.register(Commande)
class CommandeAdmin(CodificationInitialeMixin, ModelAdmin):
    codification_entite = RegleCodification.Entite.COMMANDE

    list_display = ["numero", "devis", "date_commande", "statut", "devise"]
    search_fields = ["numero", "devis__numero"]
    autocomplete_fields = ["devis", "adresse_facturation", "adresse_livraison", "devise"]
    inlines = [CommandeLigneInline]
    actions = ["action_synchroniser_lignes"]

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
    model = LivraisonLigne
    extra = 1
    autocomplete_fields = ["commande_ligne"]


@admin.register(Livraison)
class LivraisonAdmin(CodificationInitialeMixin, ModelAdmin):
    codification_entite = RegleCodification.Entite.LIVRAISON

    list_display = ["numero", "commande", "date_livraison"]
    search_fields = ["numero", "commande__numero"]
    autocomplete_fields = ["commande"]
    inlines = [LivraisonLigneInline]

    def save_formset(self, request, form, formset, change):
        try:
            super().save_formset(request, form, formset, change)
        except LivraisonError as exc:
            self.message_user(request, str(exc), level=messages.ERROR)


@admin.register(LivraisonLigne)
class LivraisonLigneAdmin(ModelAdmin):
    list_display = ["livraison", "commande_ligne", "quantite_livree"]
    search_fields = ["livraison__numero", "commande_ligne__article__reference"]
    autocomplete_fields = ["livraison", "commande_ligne"]


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
    ]
    list_filter = ["statut_synchro"]
    search_fields = ["numero", "commande__numero", "article__reference"]
    autocomplete_fields = ["commande", "article"]
    inlines = [OperationOFInline]
    actions = ["action_resynchroniser"]

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
