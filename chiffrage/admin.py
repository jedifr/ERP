import dataclasses
import datetime

from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import escape, format_html, format_html_join
from django.utils.safestring import mark_safe
from unfold.decorators import action as unfold_action
from unfold.enums import ActionVariant
from comptes.exports import ExportCsvMixin
from comptes.colonnes import ColonnesPersonnalisablesMixin
from comptes.liens import lien_admin
from comptes.pastilles import A_FAIRE, EN_COURS, NEUTRE, PROBLEME, TERMINE, PastillesMixin
from comptes.montants import arrondir, pourcent, somme
from comptes.concurrence import VerrouOptimisteMixin
from comptes.historique import HistoriqueLectureSeule
from unfold.admin import ModelAdmin, TabularInline

from codification.mixins import CodificationInitialeMixin
from codification.models import RegleCodification
from commercial.models import TauxTVA

from .pieces_views import PiecesDevisMixin
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
    ComposantOF,
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
    indice_pour,
)
from .documents import (
    DocumentError,
    generer_pdf_ar_commande,
    generer_pdf_bon_livraison,
    generer_pdf_bon_preparation,
    generer_pdf_devis,
    generer_pdf_ordre_fabrication,
    generer_pdf_ordres_fabrication,
)
from . import etapes
from .moteur import ChiffrageError, calculer_devis
from .planning_sync import resynchroniser
from .validation import verifier_validation_devis
from .production import (
    CHAMPS_SUIVIS_COMMANDE_LIGNE,
    enregistrer_modification_ligne,
    lancer_en_production,
    lancer_ligne_en_production,
    comparer_indices,
    creer_ordres_fabrication,
    planifier_ordres_fabrication,
    reviser_devis,
    synchroniser_lignes_commande,
)
from unfold.widgets import UnfoldAdminSelectWidget

from .widgets import DelaiWidget


def taux_tva_display(obj):
    """Juste le taux (ex. "20%"), sans le libellé du référentiel — utilisé
    dans les colonnes de liste (lecture seule). Le champ éditable, lui,
    utilise TauxTVACompactChoiceField ci-dessous pour le même rendu compact
    jusque dans les options du menu déroulant (gain de place sur la colonne
    des inlines "Lignes de devis" / "Lignes de commande")."""
    if not obj.taux_tva:
        return "—"
    return f"{pourcent(obj.taux_tva.taux)}%"


class TauxTVACompactChoiceField(forms.ModelChoiceField):
    # Un champ déclaré à la main perd les widgets d'Unfold : sans ce widget la liste n'a pas de bordure.
    widget = UnfoldAdminSelectWidget

    def label_from_instance(self, obj):
        return f"{pourcent(obj.taux)}%"


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


class EtapeSuivanteMixin:
    """Bouton « Étape suivante » en haut de la fiche : propose et exécute la prochaine action du cycle
    devis → commande → ordres de fabrication → livraison → facture (décision dans `etapes.py`).
    Le bouton n'apparaît que s'il y a une étape à faire et son libellé dit laquelle."""

    def etape_de(self, objet):
        raise NotImplementedError

    def executer_etape(self, request, objet, etape):
        raise NotImplementedError

    def has_etape_suivante_permission(self, request, object_id=None):
        return self.has_view_permission(request)

    def get_actions_detail(self, request, object_id):
        actions = super().get_actions_detail(request, object_id)
        objet = self.get_object(request, object_id)
        etape = self.etape_de(objet) if objet is not None else None
        resultat = []
        for a in actions:
            if a.action_name.endswith("action_etape_suivante"):
                if etape is None:  # rien à faire : pas de bouton
                    continue
                a = dataclasses.replace(a, description=f"Étape suivante : {etape.libelle}")
            resultat.append(a)
        return resultat

    @unfold_action(
        description="Étape suivante", icon="arrow_forward", variant=ActionVariant.PRIMARY,
        permissions=["etape_suivante"], url_path="etape-suivante",
    )
    def action_etape_suivante(self, request, object_id):
        objet = get_object_or_404(self.model, pk=object_id)
        if not self.has_view_permission(request, objet):
            raise PermissionDenied
        etape = self.etape_de(objet)
        if etape is None:
            self.message_user(request, f"{objet} : aucune étape suivante pour l'instant.", level=messages.INFO)
            return HttpResponseRedirect(reverse(f"admin:{objet._meta.app_label}_{objet._meta.model_name}_change", args=[objet.pk]))
        return self.executer_etape(request, objet, etape)

    def _refus_droit(self, request, objet, message):
        self.message_user(request, message, level=messages.ERROR)
        return HttpResponseRedirect(reverse(f"admin:{objet._meta.app_label}_{objet._meta.model_name}_change", args=[objet.pk]))

    def _suite_commande(self, request, commande, etape):
        """Étapes qui partent d'une commande (aussi proposées depuis la livraison pour la facture)."""
        if etape == etapes.CREER_ORDRES:
            if not request.user.has_perm("chiffrage.add_ordrefabrication"):
                return self._refus_droit(request, commande, "Vous n'avez pas la permission de créer des ordres de fabrication.")
            return HttpResponseRedirect(reverse("admin:chiffrage_commande_action_creer_ordres", args=[commande.pk]))
        if etape == etapes.CREER_LIVRAISON:
            if not request.user.has_perm("chiffrage.add_livraison"):
                return self._refus_droit(request, commande, "Vous n'avez pas la permission de créer une livraison.")
            return HttpResponseRedirect(f"{reverse('admin:chiffrage_livraison_add')}?commande={commande.pk}")
        if request.user.has_perm("facturation.add_facture"):
            return HttpResponseRedirect(f"{reverse('admin:facturation_facture_preparer')}?commande={commande.pk}")
        return self._refus_droit(request, commande, "Vous n'avez pas la permission de créer une facture.")


class EnregistrerEtValiderMixin:
    """Bouton « Enregistrer et valider » de la barre d'enregistrement : enregistre la fiche, la valide quand le
    document a une validation (le devis), puis ouvre directement son PDF — un clic au lieu de quatre.

    L'admin qui l'utilise déclare `url_pdf` (action PDF de la fiche), `libelle_valider` et, si le document se
    valide, `valeurs_validation` (champs forcés avant l'enregistrement) et `pret_pour_pdf(obj)`. Si la validation
    est refusée (devis sous le coût, sans ligne…), la fiche est enregistrée, les raisons s'affichent, et on reste
    sur la fiche."""

    MARQUEUR = "_enregistrer_valider"
    url_pdf = None
    libelle_valider = "Enregistrer et valider"
    valeurs_validation = {}

    def pret_pour_pdf(self, obj):
        return True

    def peut_enregistrer_valider(self, request):
        return self.has_change_permission(request)

    def has_enregistrer_valider_permission(self, request, object_id=None):
        return self.peut_enregistrer_valider(request)

    @unfold_action(
        description="Enregistrer et valider", icon="task_alt", variant=ActionVariant.PRIMARY,
        permissions=["enregistrer_valider"], attrs={"name": MARQUEUR},
    )
    def action_enregistrer_valider(self, request, obj):
        return None  # le travail se fait dans response_change : une fois la fiche ET ses lignes enregistrées

    def get_actions_submit_line(self, request, object_id):
        return [
            dataclasses.replace(a, description=self.libelle_valider) if a.action_name.endswith("action_enregistrer_valider") else a
            for a in super().get_actions_submit_line(request, object_id)
        ]

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        if request.method == "POST" and self.MARQUEUR in request.POST and self.valeurs_validation:
            if self.peut_enregistrer_valider(request):
                request.POST = request.POST.copy()
                for champ, valeur in self.valeurs_validation.items():
                    request.POST[champ] = valeur
        return super().changeform_view(request, object_id, form_url, extra_context)

    def response_change(self, request, obj):
        if self.MARQUEUR not in request.POST:
            return super().response_change(request, obj)
        fiche = reverse(f"admin:{obj._meta.app_label}_{obj._meta.model_name}_change", args=[obj.pk])
        if not self.pret_pour_pdf(obj):
            self.message_user(request, f"{obj} enregistré, mais pas validé.", level=messages.WARNING)
            return HttpResponseRedirect(fiche)
        return HttpResponseRedirect(reverse(self.url_pdf, args=[obj.pk]))


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


class DernierIndiceFilter(admin.SimpleListFilter):
    title = "indices"
    parameter_name = "indices"

    def lookups(self, request, model_admin):
        return [("derniers", "Derniers indices seulement"), ("remplaces", "Indices remplacés")]

    def queryset(self, request, queryset):
        if self.value() == "derniers":
            return queryset.exclude(issue=Devis.Issue.REMPLACE)
        if self.value() == "remplaces":
            return queryset.filter(issue=Devis.Issue.REMPLACE)
        return queryset


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
class DevisAdmin(PiecesDevisMixin, ColonnesPersonnalisablesMixin, PastillesMixin, EnregistrerEtValiderMixin, EtapeSuivanteMixin, ExportCsvMixin, VerrouOptimisteMixin, CodificationInitialeMixin, HistoriqueLectureSeule, ModelAdmin):
    codification_entite = RegleCodification.Entite.DEVIS
    form = DevisAdminForm

    pastilles = {
        "statut": {"brouillon": EN_COURS, "valide": TERMINE},
        "issue": {"en_attente": A_FAIRE, "accepte": TERMINE, "refuse": PROBLEME, "remplace": NEUTRE},
    }
    list_display = [
        "numero",
        "indice",
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
    list_filter = ["statut", "issue", DernierIndiceFilter, ExpireFilter]
    search_fields = ["numero", "client__raison_sociale"]
    autocomplete_fields = ["client", "adresse_facturation", "adresse_livraison", "contact"]
    readonly_fields = [
        "montant_matiere_ht_display",
        "montant_operations_ht_display",
        "montant_total_ht_display",
        "montant_total_ttc_display",
        "indices_display",
        "comparaison_display",
    ]
    inlines = [DevisLigneInline]
    actions = ["action_recalculer", "action_lancer_en_production"]
    actions_detail = ["action_etape_suivante", "action_pdf", "action_reviser"]
    actions_submit_line = ["action_enregistrer_valider"]
    url_pdf = "admin:chiffrage_devis_action_pdf"
    libelle_valider = "Enregistrer, valider et ouvrir le PDF"
    valeurs_validation = {"statut": Devis.Statut.VALIDE}

    def peut_enregistrer_valider(self, request):
        return request.user.has_perm("chiffrage.valider_devis") and self.has_change_permission(request)

    def pret_pour_pdf(self, obj):
        return obj.statut == Devis.Statut.VALIDE

    def etape_de(self, objet):
        return etapes.etape_devis(objet)

    def executer_etape(self, request, devis, etape):
        if etape == etapes.OUVRIR_COMMANDE:
            return HttpResponseRedirect(
                reverse("admin:chiffrage_commande_change", args=[Commande.objects.filter(devis=devis).first().pk])
            )
        if not request.user.has_perm("chiffrage.add_commande"):
            return self._refus_droit(request, devis, "Vous n'avez pas la permission de créer une commande.")
        try:
            commande = lancer_en_production(devis)
        except ChiffrageError as exc:
            return self._refus_droit(request, devis, f"{devis} : {exc}")
        suite = etapes.etape_commande(commande)
        self.message_user(
            request,
            format_html(
                "Commande {} créée.{}", lien_admin(commande), f" Étape suivante : {suite.libelle.lower()}." if suite else ""
            ),
            level=messages.SUCCESS,
        )
        return HttpResponseRedirect(reverse("admin:chiffrage_commande_change", args=[commande.pk]))

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
        js = ["chiffrage/devis_admin_live.js", "chiffrage/devisligne_reorder.js", "chiffrage/devis_pieces.js"]
        css = {"all": ["chiffrage/devis_admin_live.css", "chiffrage/devis_pieces.css"]}

    # Fiche en deux colonnes : saisie à gauche, récapitulatif (montants, indices) à droite — voir fiche_deux_colonnes.css.
    CHAMPS_RECAPITULATIF = [
        "montant_matiere_ht_display", "montant_operations_ht_display", "montant_total_ht_display",
        "montant_total_ttc_display", "indices_display", "comparaison_display",
    ]

    def get_fieldsets(self, request, obj=None):
        saisie = [c for c in self.get_fields(request, obj) if c not in self.CHAMPS_RECAPITULATIF]
        return [
            (None, {"fields": saisie, "classes": ["fiche-saisie"]}),
            ("Récapitulatif", {"fields": self.CHAMPS_RECAPITULATIF, "classes": ["fiche-recap"]}),
        ]

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

    @admin.display(description="Indices de ce devis")
    def indices_display(self, obj):
        if obj is None or not obj.pk:
            return "—"
        versions = obj.versions()
        if len(versions) == 1:
            return format_html("Indice {} (aucune révision).", obj.indice)
        lignes = []
        for v in versions:
            etat = "remplacé" if v.issue == Devis.Issue.REMPLACE else v.get_issue_display()
            lignes.append(
                format_html(
                    "<tr><td><b>{}</b>{}</td><td style='white-space:nowrap'><a href='{}'>{}</a></td>"
                    "<td>{} · {} · {}</td><td>{}</td></tr>",
                    v.indice,
                    " (cet indice)" if v.pk == obj.pk else "",
                    reverse("admin:chiffrage_devis_change", args=[v.pk]),
                    v.numero,
                    f"{v.date_creation:%d/%m/%Y}",
                    v.get_statut_display(),
                    etat,
                    v.motif_revision or "offre initiale",
                )
            )
        return format_html(
            "<table class='text-sm'><tr class='text-left'><th>Indice</th><th>Devis</th><th>Date · statut · réponse</th>"
            "<th>Modification</th></tr>{}</table>",
            mark_safe("".join(lignes)),
        )

    @admin.display(description="Changements depuis l'indice précédent")
    def comparaison_display(self, obj):
        if obj is None or not obj.pk or not obj.devis_origine_id:
            return "—"
        comparaison = comparer_indices(obj.devis_origine, obj)
        libelles = {"ajoutee": "ajoutée", "supprimee": "supprimée", "modifiee": "modifiée", "identique": "inchangée"}

        def fmt(valeur):
            if valeur is None:
                return "—"
            quantite, prix = valeur
            return f"{quantite:g} × " + ("à chiffrer" if prix is None else f"{prix} € HT")

        lignes = [
            format_html(
                "<tr><td class='pr-4'>{}</td><td class='pr-4'>{}</td><td class='pr-4'>{}</td><td>{}</td></tr>",
                ligne["article"], libelles[ligne["etat"]], fmt(ligne["avant"]), fmt(ligne["apres"]),
            )
            for ligne in comparaison["lignes"]
        ]
        total_apres = comparaison["total_apres"]
        return format_html(
            "<table class='text-sm'><tr class='text-left'><th class='pr-4'>Article</th><th class='pr-4'>Évolution</th>"
            "<th class='pr-4'>Indice {}</th><th>Indice {}</th></tr>{}</table>"
            "<p class='text-sm mt-2'>Total HT : {} € → {}</p>",
            obj.devis_origine.indice, obj.indice, mark_safe("".join(lignes)),
            comparaison["total_avant"],
            f"{total_apres} €" if total_apres is not None else "à chiffrer",
        )

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
        ] + self.urls_pieces()
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

    @admin.action(description="Créer la commande")
    def action_lancer_en_production(self, request, queryset):
        creees = []
        for devis in queryset:
            try:
                commande = lancer_en_production(devis)
            except ChiffrageError as exc:
                self.message_user(request, f"{devis} : {exc}", level=messages.ERROR)
            else:
                creees.append(commande)
                self.message_user(
                    request,
                    format_html(
                        "{} : commande {} créée. Cliquez sur la commande pour l'ouvrir.",
                        devis, lien_admin(commande),
                    ),
                    level=messages.SUCCESS,
                )
        if len(creees) == 1 and queryset.count() == 1:  # un seul devis : on ouvre directement la commande
            return HttpResponseRedirect(reverse("admin:chiffrage_commande_change", args=[creees[0].pk]))


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

    @unfold_action(description="Nouvel indice (réviser)", permissions=["reviser"], url_path="reviser")
    def action_reviser(self, request, object_id):
        devis = get_object_or_404(Devis, pk=object_id)
        retour = reverse("admin:chiffrage_devis_change", args=[devis.pk])
        if request.method == "POST":
            try:
                revision = reviser_devis(devis, request.POST.get("motif", ""))
            except ChiffrageError as exc:
                self.message_user(request, str(exc), level=messages.ERROR)
                return HttpResponseRedirect(retour)
            self.message_user(
                request,
                format_html(
                    "Indice {} créé en brouillon ({}) ; {} (indice {}) est marqué « remplacé ». "
                    "Modifiez-le, chiffrez-le puis validez-le.",
                    revision.indice, lien_admin(revision), lien_admin(devis), devis.indice,
                ),
                level=messages.SUCCESS,
            )
            return HttpResponseRedirect(reverse("admin:chiffrage_devis_change", args=[revision.pk]))
        return TemplateResponse(
            request,
            "admin/confirmer_avec_motif.html",
            {
                **self.admin_site.each_context(request),
                "title": f"Nouvel indice du devis {devis} (indice actuel : {devis.indice})",
                "explication": (
                    f"Un nouvel indice ({indice_pour(devis.revision + 1)}) sera créé en brouillon, avec les mêmes lignes. "
                    f"Le devis {devis} sera marqué « remplacé » : il reste consultable mais ne pourra plus devenir "
                    "une commande. Indiquez ce qui change : le motif figure sur l'offre envoyée au client."
                ),
                "avertissement": "" if devis.statut == Devis.Statut.VALIDE else "Seul un devis validé se révise.",
                "bouton": "Créer le nouvel indice",
                "retour": retour,
            },
        )

    def has_reviser_permission(self, request, obj=None):
        return request.user.has_perm("chiffrage.change_devis")


@admin.register(DevisLigne)
class DevisLigneAdmin(ExportCsvMixin, HistoriqueLectureSeule, ModelAdmin):
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
    if ligne.pk and ligne.ordres_fabrication.exists():
        messages.warning(
            request,
            f"« {ligne.article} » : quantité augmentée alors qu'un ordre de fabrication existe déjà pour "
            "cette ligne — il ne sera pas recalculé. Ajoutez plutôt une nouvelle ligne pour la quantité "
            "supplémentaire (elle pourra être lancée en production séparément)."
        )


class CommandeLigneModificationInline(TabularInline):
    model = CommandeLigneModification
    extra = 0
    can_delete = False
    fields = ["champ", "ancienne_valeur", "nouvelle_valeur", "utilisateur", "date_modification"]
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


class CommandeLigneInline(ColonnesPersonnalisablesMixin, TabularInline):
    # Colonnes que chaque utilisateur peut masquer (« Mes colonnes ») ; le reste sert aux calculs en direct de la fiche.
    colonnes_optionnelles = [
        "designation", "date_livraison_possible_display", "statut_approvisionnement", "quantite_livree",
        "reliquat", "entierement_livree", "quantite_facturee", "reste_a_facturer",
    ]
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
class CommandeAdmin(ColonnesPersonnalisablesMixin, PastillesMixin, EnregistrerEtValiderMixin, EtapeSuivanteMixin, ExportCsvMixin, VerrouOptimisteMixin, CodificationInitialeMixin, HistoriqueLectureSeule, ModelAdmin):
    codification_entite = RegleCodification.Entite.COMMANDE

    list_display = ["numero", "client", "reference_client", "devis", "date_commande", "statut", "devise"]
    pastilles = {"statut": {"en_cours": EN_COURS, "soldee": TERMINE, "annulee": PROBLEME}}
    list_filter = ["statut"]
    search_fields = ["numero", "reference_client", "client__raison_sociale", "devis__numero"]
    autocomplete_fields = ["devis", "client", "adresse_facturation", "adresse_livraison", "devise"]
    # Le statut ne se saisit pas : « soldée » est déduit des livraisons, « annulée »
    # passe par l'action dédiée (qui refuse une commande déjà livrée).
    readonly_fields = [
        "statut", "montant_total_ht_display", "montant_total_ttc_display",
        "ordres_fabrication_display", "livraisons_display", "factures_display",
    ]
    inlines = [CommandeLigneInline]
    actions = ["action_synchroniser_lignes", "action_annuler"]

    # Fiche en deux colonnes : saisie à gauche, récapitulatif (totaux, ordres de fabrication, livraisons, factures)
    # à droite — voir comptes/static/comptes/fiche_deux_colonnes.css.
    CHAMPS_RECAPITULATIF = [
        "montant_total_ht_display", "montant_total_ttc_display",
        "ordres_fabrication_display", "livraisons_display", "factures_display",
    ]

    def get_fieldsets(self, request, obj=None):
        saisie = [c for c in self.get_fields(request, obj) if c not in self.CHAMPS_RECAPITULATIF]
        return [
            (None, {"fields": saisie, "classes": ["fiche-saisie"]}),
            ("Récapitulatif", {"fields": self.CHAMPS_RECAPITULATIF, "classes": ["fiche-recap"]}),
        ]

    @staticmethod
    def _montant_commande(obj, champ):
        if obj is None or not obj.pk:
            return "—"
        valeurs = [getattr(l, champ) for l in obj.lignes.all() if getattr(l, champ) is not None]
        if not valeurs:
            return "—"
        return f"{arrondir(somme(valeurs)):,.2f} €".replace(",", " ").replace(".", ",")

    @admin.display(description="Total HT")
    def montant_total_ht_display(self, obj):
        return self._montant_commande(obj, "montant_ht")

    @admin.display(description="Total TTC")
    def montant_total_ttc_display(self, obj):
        return self._montant_commande(obj, "montant_ttc")

    @staticmethod
    def _liens(objets, nom_url, texte):
        liens = [format_html("<a href='{}'>{}</a>", reverse(nom_url, args=[o.pk]), texte(o)) for o in objets]
        return mark_safe("<br>".join(liens)) if liens else "—"

    @admin.display(description="Ordres de fabrication")
    def ordres_fabrication_display(self, obj):
        if obj is None or not obj.pk:
            return "—"
        return self._liens(
            obj.ordres_fabrication.order_by("numero"), "admin:chiffrage_ordrefabrication_change",
            lambda o: f"{o.numero} — {o.article_id} × {o.quantite:g}"
            + (f" (livraison {o.date_livraison_prevue:%d/%m/%Y})" if o.date_livraison_prevue else ""),
        )

    @admin.display(description="Livraisons")
    def livraisons_display(self, obj):
        if obj is None or not obj.pk:
            return "—"
        return self._liens(
            obj.livraisons.order_by("date_livraison", "numero"), "admin:chiffrage_livraison_change",
            lambda l: f"{l.numero} — {l.date_livraison:%d/%m/%Y}" + (" (annulée)" if l.statut == l.Statut.ANNULEE else ""),
        )

    @admin.display(description="Factures")
    def factures_display(self, obj):
        if obj is None or not obj.pk:
            return "—"
        return self._liens(
            obj.factures.order_by("date_facturation", "numero"), "admin:facturation_facture_change",
            lambda f: f"{f.numero} — {f.date_facturation:%d/%m/%Y}" + (" (avoir)" if f.est_avoir else ""),
        )
    actions_detail = ["action_etape_suivante", "action_creer_ordres", "action_fiches_fabrication_pdf", "action_ar_pdf", "action_bon_preparation_pdf"]
    actions_submit_line = ["action_enregistrer_valider"]
    url_pdf = "admin:chiffrage_commande_action_ar_pdf"
    libelle_valider = "Enregistrer et ouvrir l'AR (PDF)"

    def etape_de(self, objet):
        return etapes.etape_commande(objet)

    def executer_etape(self, request, commande, etape):
        return self._suite_commande(request, commande, etape)

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

    def _pdf_commande(self, request, object_id, generateur, prefixe):
        commande = get_object_or_404(Commande, pk=object_id)
        if not self.has_view_permission(request, commande):
            raise PermissionDenied
        try:
            contenu = generateur(commande)
        except DocumentError as exc:
            self.message_user(request, str(exc), level=messages.ERROR)
            return HttpResponseRedirect(reverse("admin:chiffrage_commande_change", args=[commande.pk]))
        reponse = HttpResponse(contenu, content_type="application/pdf")
        reponse["Content-Disposition"] = f'inline; filename="{prefixe}-{commande.pk}.pdf"'
        return reponse

    @unfold_action(description="AR de commande (PDF)", url_path="ar-pdf")
    def action_ar_pdf(self, request, object_id):
        return self._pdf_commande(request, object_id, generer_pdf_ar_commande, "ar")

    @unfold_action(description="Bon de préparation (PDF)", url_path="bon-preparation-pdf")
    def action_bon_preparation_pdf(self, request, object_id):
        return self._pdf_commande(request, object_id, generer_pdf_bon_preparation, "preparation")

    @unfold_action(description="Fiches de fabrication (PDF)", url_path="fiches-fabrication-pdf")
    def action_fiches_fabrication_pdf(self, request, object_id):
        """Toutes les fiches de fabrication de la commande en un seul PDF (ou celles de `?ofs=A,B`)."""
        commande = get_object_or_404(Commande, pk=object_id)
        if not (self.has_view_permission(request, commande) and request.user.has_perm("chiffrage.view_ordrefabrication")):
            raise PermissionDenied
        ordres = commande.ordres_fabrication.select_related("article", "commande__client").order_by("numero")
        voulus = [n for n in request.GET.get("ofs", "").split(",") if n]
        if voulus:
            ordres = ordres.filter(pk__in=voulus)
        try:
            contenu = generer_pdf_ordres_fabrication(ordres)
        except DocumentError as exc:
            self.message_user(request, str(exc), level=messages.ERROR)
            return HttpResponseRedirect(reverse("admin:chiffrage_commande_change", args=[commande.pk]))
        reponse = HttpResponse(contenu, content_type="application/pdf")
        reponse["Content-Disposition"] = f'inline; filename="fiches-fabrication-{commande.pk}.pdf"'
        return reponse

    @unfold_action(description="Créer les ordres de fabrication", permissions=["creer_ordres"], url_path="ordres-fabrication")
    def action_creer_ordres(self, request, object_id):
        commande = get_object_or_404(Commande, pk=object_id)
        retour = reverse("admin:chiffrage_commande_change", args=[commande.pk])
        regrouper = (request.POST if request.method == "POST" else request.GET).get("regrouper") == "1"
        if request.method == "POST":
            try:
                ordres = creer_ordres_fabrication(commande, regrouper=regrouper)
            except ChiffrageError as exc:
                self.message_user(request, str(exc), level=messages.ERROR)
                return HttpResponseRedirect(retour)
            self.message_user(
                request,
                format_html(
                    "{} ordre(s) de fabrication créé(s) : {}. <a href='{}?ofs={}' target='_blank' class='underline font-semibold'>"
                    "Imprimer les {} fiche(s) de fabrication (PDF)</a>",
                    len(ordres),
                    format_html_join(", ", "{}", ((lien_admin(o),) for o in ordres)),
                    reverse("admin:chiffrage_commande_action_fiches_fabrication_pdf", args=[commande.pk]),
                    ",".join(o.numero for o in ordres),
                    len(ordres),
                ),
                level=messages.SUCCESS,
            )
            return HttpResponseRedirect(retour)
        plan = planifier_ordres_fabrication(commande, regrouper=regrouper)
        a_des_doublons = len(planifier_ordres_fabrication(commande, False)) != len(planifier_ordres_fabrication(commande, True))
        raison = (
            f"La commande {commande} est annulée."
            if commande.statut == Commande.Statut.ANNULEE
            else "Aucune ligne d'article fabriqué sans ordre de fabrication : rien à créer."
        )
        if commande.statut == Commande.Statut.ANNULEE:
            plan = []
        return TemplateResponse(
            request,
            "admin/chiffrage/creer_ordres_fabrication.html",
            {
                **self.admin_site.each_context(request),
                "title": f"Créer les ordres de fabrication de {commande}",
                "commande": commande, "plan": plan, "regrouper": regrouper,
                "a_des_doublons": a_des_doublons, "raison": raison, "retour": retour,
            },
        )

    def has_creer_ordres_permission(self, request, obj=None):
        return request.user.has_perm("chiffrage.add_ordrefabrication")

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
            self.message_user(
                request, format_html("{} : commande annulée.{}", lien_admin(commande), suite), level=messages.SUCCESS
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
class CommandeLigneAdmin(ExportCsvMixin, ModelAdmin):
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
                self.message_user(
                    request, format_html("{} : ordre de fabrication {} créé.", ligne, lien_admin(of)), level=messages.SUCCESS
                )

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

    def get_extra(self, request, obj=None, **kwargs):
        commande = LivraisonAdmin._commande_demandee(request) if not (obj and obj.pk) and request.method == "GET" else None
        nombre = len(etapes.lignes_a_livrer(commande)) if commande is not None else 0
        return nombre or self.extra

    def get_exclude(self, request, obj=None):
        return ["lot"] if not settings.STOCK_ACTIF else super().get_exclude(request, obj)

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_add_permission(self, request, obj=None):
        return (obj is None or obj.statut == Livraison.Statut.VALIDEE) and super().has_add_permission(request, obj)


@admin.register(Livraison)
class LivraisonAdmin(ColonnesPersonnalisablesMixin, PastillesMixin, EnregistrerEtValiderMixin, EtapeSuivanteMixin, ExportCsvMixin, VerrouOptimisteMixin, CodificationInitialeMixin, HistoriqueLectureSeule, ModelAdmin):
    codification_entite = RegleCodification.Entite.LIVRAISON

    list_display = ["numero", "commande", "date_livraison", "statut"]
    pastilles = {"statut": {"validee": TERMINE, "annulee": PROBLEME}}
    list_filter = ["statut"]
    search_fields = ["numero", "commande__numero"]
    autocomplete_fields = ["commande"]
    inlines = [LivraisonLigneInline]
    actions = ["action_annuler"]
    actions_detail = ["action_etape_suivante", "action_pdf"]
    actions_submit_line = ["action_enregistrer_valider"]
    url_pdf = "admin:chiffrage_livraison_action_pdf"
    libelle_valider = "Enregistrer et ouvrir le BL (PDF)"

    def etape_de(self, objet):
        return etapes.etape_livraison(objet)

    def executer_etape(self, request, livraison, etape):
        return self._suite_commande(request, livraison.commande, etape)

    def get_formset_kwargs(self, request, obj, inline, prefix):
        """`?commande=…` (bouton « Créer la livraison ») : les lignes à livrer arrivent pré-remplies avec leur reliquat."""
        kwargs = super().get_formset_kwargs(request, obj, inline, prefix)
        commande = self._commande_demandee(request) if not (obj and obj.pk) and isinstance(inline, LivraisonLigneInline) else None
        if commande is not None and request.method == "GET":
            kwargs["initial"] = [{"commande_ligne": l.pk, "quantite_livree": q} for l, q in etapes.lignes_a_livrer(commande)]
        return kwargs

    @staticmethod
    def _commande_demandee(request):
        return Commande.objects.filter(pk=request.GET.get("commande", "")).exclude(statut=Commande.Statut.ANNULEE).first()

    # Fiche en deux colonnes : saisie à gauche, récapitulatif (client, contenu, reliquat, facturation, annulation)
    # à droite — voir comptes/static/comptes/fiche_deux_colonnes.css.
    CHAMPS_RECAPITULATIF = [
        "client_recap", "contenu_recap", "reliquat_recap", "facturation_recap", "annulation_recap",
    ]

    def get_fieldsets(self, request, obj=None):
        saisie = [c for c in self.get_fields(request, obj) if c not in self.CHAMPS_RECAPITULATIF]
        return [
            (None, {"fields": saisie, "classes": ["fiche-saisie"]}),
            ("Récapitulatif", {"fields": self.CHAMPS_RECAPITULATIF, "classes": ["fiche-recap"]}),
        ]

    @staticmethod
    def _enregistree(obj):
        return obj is not None and obj.pk and obj.commande_id

    @admin.display(description="Client et adresse de livraison")
    def client_recap(self, obj):
        if not self._enregistree(obj):
            return "—"
        commande = obj.commande
        adresse = commande.adresse_livraison
        return format_html(
            "<b>{}</b><br>{}<br>{} {}", commande.client.raison_sociale, adresse.adresse, adresse.code_postal, adresse.ville
        )

    @admin.display(description="Contenu de la livraison")
    def contenu_recap(self, obj):
        if not self._enregistree(obj):
            return "—"
        lignes = list(obj.lignes.select_related("commande_ligne__article"))
        if not lignes:
            return "Aucune ligne"
        detail = "<br>".join(
            format_html("{} × {}", l.commande_ligne.article_id, f"{l.quantite_livree:g}") for l in lignes
        )
        return mark_safe(detail)

    @admin.display(description="Reliquat de la commande")
    def reliquat_recap(self, obj):
        if not self._enregistree(obj):
            return "—"
        restantes = [l for l in obj.commande.lignes.all() if l.reliquat is not None and l.reliquat > 0]
        if not restantes:
            return "Commande entièrement livrée"
        return mark_safe("<br>".join(
            format_html("{} : {} restant(s)", l.article_id, f"{l.reliquat:g}") for l in restantes
        ))

    @admin.display(description="Livré non facturé")
    def facturation_recap(self, obj):
        if not self._enregistree(obj):
            return "—"
        a_facturer = [l for l in obj.commande.lignes.all() if (l.reste_a_facturer or 0) > 0]
        if not a_facturer:
            return "Tout ce qui est livré est facturé"
        return mark_safe("<br>".join(
            format_html("{} : {} à facturer", l.article_id, f"{l.reste_a_facturer:g}") for l in a_facturer
        ))

    @admin.display(description="Annulation")
    def annulation_recap(self, obj):
        if obj is None or not obj.pk or obj.statut != Livraison.Statut.ANNULEE:
            return "—"
        quand = f"{obj.date_annulation:%d/%m/%Y %H:%M}" if obj.date_annulation else "?"
        qui = f" par {obj.utilisateur_annulation}" if obj.utilisateur_annulation_id else ""
        return f"Annulée le {quand}{qui}" + (f" : {obj.motif_annulation}" if obj.motif_annulation else "")

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
        champs = ["statut", *self.CHAMPS_RECAPITULATIF]
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
                self.message_user(
                    request,
                    format_html("Livraison {} annulée : quantités livrées et stock rétablis.", lien_admin(livraison)),
                    level=messages.SUCCESS,
                )
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
    verbose_name_plural = "Gamme (opérations)"


class ComposantOFInline(TabularInline):
    """Nomenclature figée à la création de l'ordre : lecture seule."""

    model = ComposantOF
    extra = 0
    verbose_name_plural = "Nomenclature (composants à sortir)"
    fields = ["article", "longueur_mm", "largeur_mm", "quantite_par_unite", "quantite_necessaire"]
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(OrdreFabrication)
class OrdreFabricationAdmin(ColonnesPersonnalisablesMixin, PastillesMixin, EnregistrerEtValiderMixin, ExportCsvMixin, CodificationInitialeMixin, ModelAdmin):
    codification_entite = RegleCodification.Entite.ORDRE_FABRICATION

    pastilles = {"statut_synchro": {"synchronise": TERMINE, "en_attente": A_FAIRE, "echec_persistant": PROBLEME}}
    list_display = [
        "numero",
        "commande",
        "article",
        "quantite",
        "date_livraison_prevue",
        "statut",
        "statut_synchro",
        "nombre_tentatives",
        "erreur_courte",
    ]
    list_filter = ["statut_synchro"]
    readonly_fields = [
        "statut_synchro", "nombre_tentatives", "date_derniere_tentative", "derniere_erreur", "prochaine_tentative",
        "lignes_commande_display", "commande_recap", "avancement_recap", "synchro_recap",
    ]
    exclude = ["lignes_commande"]

    # Fiche en deux colonnes : saisie à gauche, récapitulatif (commande, lignes couvertes, avancement, synchronisation
    # avec le planning) à droite — voir comptes/static/comptes/fiche_deux_colonnes.css.
    CHAMPS_RECAPITULATIF = ["commande_recap", "lignes_commande_display", "avancement_recap", "synchro_recap"]
    CHAMPS_SYNCHRO = ["statut_synchro", "nombre_tentatives", "date_derniere_tentative", "derniere_erreur", "prochaine_tentative"]

    def get_fieldsets(self, request, obj=None):
        exclus = set(self.CHAMPS_RECAPITULATIF) | set(self.CHAMPS_SYNCHRO)
        saisie = [c for c in self.get_fields(request, obj) if c not in exclus]
        return [
            (None, {"fields": saisie, "classes": ["fiche-saisie"]}),
            ("Récapitulatif", {"fields": self.CHAMPS_RECAPITULATIF, "classes": ["fiche-recap"]}),
        ]

    @admin.display(description="Commande")
    def commande_recap(self, obj):
        if obj is None or not obj.pk or not obj.commande_id:
            return "—"
        commande = obj.commande
        lien = format_html("<a href='{}'>{}</a>", reverse("admin:chiffrage_commande_change", args=[commande.pk]), commande.numero)
        livraison = f"Livraison prévue le {obj.date_livraison_prevue:%d/%m/%Y}" if obj.date_livraison_prevue else "Pas de date de livraison"
        return format_html("{} — {}<br>{}", lien, commande.client.raison_sociale, livraison)

    @admin.display(description="Avancement")
    def avancement_recap(self, obj):
        if obj is None or not obj.pk:
            return "—"
        operations = list(obj.operations.select_related("poste"))
        if not operations:
            return "Aucune opération de gamme"
        prevu = sum(o.temps_prevu or 0 for o in operations)
        reel = sum(o.temps_reel or 0 for o in operations)
        avec_temps = sum(1 for o in operations if o.temps_reel is not None)
        bonnes = sum(o.quantite_bonne or 0 for o in operations)
        rebuts = sum(o.quantite_rebut or 0 for o in operations)
        lignes = [
            f"{avec_temps} opération(s) sur {len(operations)} avec un temps réel",
            f"Temps prévu {prevu:g} min — réel {reel:g} min",
        ]
        if any(o.quantite_bonne is not None or o.quantite_rebut is not None for o in operations):
            lignes.append(f"Pièces bonnes : {bonnes:g} — rebuts : {rebuts:g}")
        return mark_safe("<br>".join(escape(l) for l in lignes))

    @admin.display(description="Synchronisation avec le planning")
    def synchro_recap(self, obj):
        if obj is None or not obj.pk:
            return "—"
        lignes = [f"{obj.get_statut_synchro_display()} — {obj.nombre_tentatives} tentative(s)"]
        if obj.date_derniere_tentative:
            lignes.append(f"Dernière tentative le {obj.date_derniere_tentative:%d/%m/%Y}")
        if obj.prochaine_tentative:
            lignes.append(f"Prochaine reprise le {obj.prochaine_tentative:%d/%m/%Y %H:%M}")
        if obj.derniere_erreur:
            lignes.append(f"Erreur : {obj.derniere_erreur[:200]}")
        return mark_safe("<br>".join(escape(l) for l in lignes))
    search_fields = ["numero", "commande__numero", "article__reference"]
    autocomplete_fields = ["commande", "article"]
    inlines = [ComposantOFInline, OperationOFInline]
    actions = ["action_resynchroniser", "action_imprimer_fiches"]
    actions_detail = ["action_pdf"]
    actions_submit_line = ["action_enregistrer_valider"]
    url_pdf = "admin:chiffrage_ordrefabrication_action_pdf"
    libelle_valider = "Enregistrer et ouvrir la fiche (PDF)"

    @admin.action(description="Imprimer les fiches de fabrication (un seul PDF)")
    def action_imprimer_fiches(self, request, queryset):
        try:
            contenu = generer_pdf_ordres_fabrication(queryset.select_related("article", "commande__client").order_by("numero"))
        except DocumentError as exc:
            self.message_user(request, str(exc), level=messages.ERROR)
            return None
        reponse = HttpResponse(contenu, content_type="application/pdf")
        reponse["Content-Disposition"] = 'inline; filename="fiches-fabrication.pdf"'
        return reponse

    @admin.display(description="Lignes de commande couvertes")
    def lignes_commande_display(self, obj):
        if obj is None or not obj.pk:
            return "—"
        lignes = obj.lignes_commande.select_related("article")
        return ", ".join(f"{l.article} × {l.quantite_commandee:g}" for l in lignes) or "—"

    @unfold_action(description="Fiche de fabrication (PDF)", url_path="pdf")
    def action_pdf(self, request, object_id):
        of = get_object_or_404(OrdreFabrication, pk=object_id)
        if not self.has_view_permission(request, of):
            raise PermissionDenied
        reponse = HttpResponse(generer_pdf_ordre_fabrication(of), content_type="application/pdf")
        reponse["Content-Disposition"] = f'inline; filename="of-{of.pk}.pdf"'
        return reponse

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
            self.message_user(request, format_html("{} : {}.", lien_admin(of), statut), level=niveau)


@admin.register(OperationOF)
class OperationOFAdmin(ModelAdmin):
    list_display = ["ordre_fabrication", "ordre", "poste", "temps_prevu", "temps_reel", "statut"]
    search_fields = ["ordre_fabrication__numero", "poste__nom"]
    autocomplete_fields = ["ordre_fabrication", "poste"]
