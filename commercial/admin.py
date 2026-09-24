import re

from django import forms
from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.http import JsonResponse
from django.urls import path
from django.views.decorators.http import require_http_methods
from unfold.admin import ModelAdmin, TabularInline

from codification.mixins import CodificationInitialeMixin
from codification.models import RegleCodification
from comptabilite.models import CompteComptable, TiersCompteComptable

from .models import (
    Adresse,
    Contact,
    ContactTelephone,
    ConditionPaiement,
    DelaiPropose,
    Devise,
    Pays,
    TauxTVA,
    Tiers,
)


class AdresseInline(TabularInline):
    model = Adresse
    # extra=0 : pas de ligne vide ajoutée automatiquement. Les champs
    # (adresse, code postal, ville...) sont obligatoires sur le modèle, donc
    # une ligne vide "en trop" affichait des astérisques rouges "obligatoire"
    # sur des champs que l'utilisateur n'avait pas l'intention de remplir —
    # gênant à chaque modification d'un tiers qui a déjà ses adresses.
    # "Ajouter un objet Adresse supplémentaire" reste disponible pour en
    # ajouter une volontairement.
    extra = 0
    autocomplete_fields = ["pays"]

    class Media:
        # Autocomplétion de "Adresse" (+ pré-remplissage code postal/ville)
        # via l'API Adresse de data.gouv.fr — voir adresse_autocomplete.js.
        # Même script chargé sur AdresseAdmin (fiche autonome) : il détecte
        # tout seul le nom de champ, imbriqué ("adresses-0-adresse") ou non
        # ("adresse").
        js = ["commercial/adresse_autocomplete.js"]


class ContactTelephoneInline(TabularInline):
    model = ContactTelephone
    extra = 1


class ContactInlineForm(forms.ModelForm):
    """adresse_associee ne peut pas rester un ModelChoiceField classique
    sur cet inline : au moment où Django valide ce formulaire (avant que
    quoi que ce soit soit enregistré), l'adresse qu'on veut associer peut
    être une ligne du tableau Adresses tout juste remplie, sans pk réel —
    un ModelChoiceField rejetterait cette valeur, introuvable en base.

    On référence donc la ligne choisie par son indice dans le formset
    Adresses ("adresse_associee_ref", ex. "1" pour adresses-1-*) plutôt
    que par un pk, et TiersAdmin.save_related() la résout en instance
    réelle une fois que toutes les adresses ont effectivement été
    enregistrées. Les options du <select> sont construites en JS
    (tiers_admin.js) à partir des lignes du tableau Adresses affichées à
    l'écran (livraison et/ou facturation, une même ligne pouvant être les
    deux à la fois), jamais interrogées en base."""

    adresse_associee_ref = forms.CharField(
        label="Adresse associée",
        required=False,
        widget=forms.Select(choices=[("", "---------")]),
        help_text="Uniquement les adresses déjà saisies dans le tableau ci-dessus.",
    )

    class Meta:
        model = Contact
        exclude = ["adresse_associee"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Permet à tiers_admin.js de pré-sélectionner, au premier rendu, la
        # ligne Adresses correspondant à l'adresse déjà liée (contact
        # existant) — comparée au pk réel de chaque ligne, pas à son indice
        # (qui peut différer d'un rendu à l'autre selon l'ordre du tri).
        self.fields["adresse_associee_ref"].widget.attrs["data-adresse-associee-actuelle"] = (
            self.instance.adresse_associee_id or ""
        )


class ContactInline(TabularInline):
    model = Contact
    form = ContactInlineForm
    extra = 0
    # Inline imbriqué (unfold.admin.ModelAdmin embarque nativement
    # NestedInlinesModelAdminMixin) : permet de saisir les numéros de
    # téléphone d'un contact directement depuis la fiche Tiers, sans passer
    # par la fiche Contact dédiée — ContactTelephone reste un modèle à part
    # (plusieurs numéros typés par contact), seule sa présentation change.
    inlines = [ContactTelephoneInline]


class TiersCompteComptableInline(TabularInline):
    # OneToOneField : Django limite automatiquement à un seul formulaire.
    model = TiersCompteComptable
    extra = 1
    fields = ["code_client", "compte_client", "code_fournisseur", "compte_fournisseur"]
    autocomplete_fields = ["compte_client", "compte_fournisseur"]


@staff_member_required
@require_http_methods(["GET"])
def apercu_compte_comptable_view(request):
    """Aperçu en direct du compte comptable généré par TiersCompteComptable
    à partir d'un code à 5 caractères (voir TiersCompteComptable.save()) —
    utilisé par le JS de la fiche Tiers pour afficher, dès la frappe, le
    compte qui sera résolu (existant, avec son libellé, ou à créer) sans
    attendre l'enregistrement du formulaire."""
    prefixe = request.GET.get("prefixe")
    code = (request.GET.get("code") or "").strip()
    if prefixe not in ("411", "401") or not re.fullmatch(r"[A-Za-z0-9]{5}", code):
        return JsonResponse({"valide": False})

    code_complet = f"{prefixe}{code.upper()}"
    compte = CompteComptable.objects.filter(code=code_complet).first()
    return JsonResponse(
        {
            "valide": True,
            "code": code_complet,
            "existe": compte is not None,
            "libelle": compte.libelle if compte else None,
        }
    )


@admin.register(Tiers)
class TiersAdmin(CodificationInitialeMixin, ModelAdmin):
    codification_entite = RegleCodification.Entite.TIERS

    list_display = ["code", "raison_sociale", "type_tiers", "regime_fiscal", "devise", "siret"]
    list_filter = ["type_tiers", "regime_fiscal"]
    search_fields = ["code", "raison_sociale", "siret"]
    autocomplete_fields = ["conditions_paiement", "devise"]
    fieldsets = [
        (None, {"fields": ["code", "raison_sociale", "type_tiers", "siret"]}),
        ("Coordonnées bancaires", {"fields": ["iban", "bic"], "classes": ["tab"]}),
        ("Commercial", {"fields": ["regime_fiscal", "devise", "conditions_paiement"]}),
    ]
    # Compte comptable de tiers en premier (juste après le fieldset
    # "Commercial", qui est le dernier bloc de champs) : Django/l'admin
    # affiche toujours tous les fieldsets avant tous les inlines, donc
    # l'ordre ci-dessous est ce qui rapproche le plus "Compte comptable de
    # tiers" du bloc "Commercial".
    inlines = [TiersCompteComptableInline, AdresseInline, ContactInline]

    class Media:
        # tiers_admin.js : aperçu de compte comptable + options "Adresse
        # associée" des contacts. entreprise_lookup.js : autocomplétion
        # SIRET/SIREN <-> raison sociale + adresse du siège (voir plus bas).
        js = ["commercial/tiers_admin.js", "commercial/entreprise_lookup.js"]

    def get_urls(self):
        urls = [
            path(
                "apercu-compte-comptable/",
                self.admin_site.admin_view(apercu_compte_comptable_view),
                name="commercial_tiers_apercu_compte_comptable",
            ),
        ]
        return urls + super().get_urls()

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        self._resoudre_adresses_associees(form.instance, formsets)

    def _resoudre_adresses_associees(self, tiers, formsets):
        """Contact.adresse_associee ne fait pas partie du formulaire de
        ContactInline (voir ContactInlineForm.adresse_associee_ref) : la
        ligne d'adresse choisie n'est résolue en instance réelle qu'ici,
        une fois que toutes les adresses ont vraiment été enregistrées par
        le super().save_related() ci-dessus (y compris celles tout juste
        créées dans cette même requête)."""
        adresse_formset = next((fs for fs in formsets if fs.model is Adresse), None)
        contact_formset = next((fs for fs in formsets if fs.model is Contact), None)
        if adresse_formset is None or contact_formset is None:
            return
        for contact_form in contact_formset.forms:
            if contact_form.cleaned_data.get("DELETE"):
                continue
            contact = contact_form.instance
            if contact.pk is None:
                continue
            adresse = self._resoudre_reference_adresse(
                contact_form.cleaned_data.get("adresse_associee_ref"), tiers, adresse_formset
            )
            if contact.adresse_associee_id != (adresse.pk if adresse else None):
                contact.adresse_associee = adresse
                contact.save(update_fields=["adresse_associee"])

    @staticmethod
    def _resoudre_reference_adresse(ref, tiers, adresse_formset):
        if not ref:
            return None
        try:
            index = int(ref)
        except ValueError:
            return None
        if index < 0 or index >= len(adresse_formset.forms):
            return None
        adresse_form = adresse_formset.forms[index]
        if adresse_form in adresse_formset.deleted_forms:
            return None
        candidate = adresse_form.instance
        # N'importe quel type d'adresse convient (livraison, facturation, ou
        # les deux à la fois) : seule l'appartenance au bon tiers compte.
        if candidate.pk and candidate.tiers_id == tiers.pk:
            return candidate
        return None


@admin.register(Adresse)
class AdresseAdmin(ModelAdmin):
    list_display = ["tiers", "types_affiches", "libelle", "ville", "pays", "est_principale"]
    list_filter = ["est_livraison", "est_facturation", "est_principale", "pays"]
    search_fields = ["tiers__code", "tiers__raison_sociale", "ville", "libelle"]
    autocomplete_fields = ["tiers", "pays"]

    class Media:
        js = ["commercial/adresse_autocomplete.js"]


@admin.register(Contact)
class ContactAdmin(ModelAdmin):
    list_display = [
        "nom",
        "prenom",
        "tiers",
        "email",
        "telephones_display",
        "fonction",
        "est_principal",
        "adresse_associee",
    ]
    list_filter = ["est_principal"]
    search_fields = ["nom", "prenom", "tiers__code", "tiers__raison_sociale"]
    autocomplete_fields = ["tiers"]
    inlines = [ContactTelephoneInline]

    @admin.display(description="Téléphones")
    def telephones_display(self, obj):
        return ", ".join(str(t) for t in obj.telephones.all()) or "—"

    def get_form(self, request, obj=None, **kwargs):
        # Mémorise le contact en cours d'édition (None à la création) pour
        # restreindre le champ adresse_associee ci-dessous — même souci et
        # même correctif que ContactInline.formfield_for_foreignkey côté
        # fiche Tiers : sans ce filtre, l'autocomplete proposait les
        # adresses de n'importe quel tiers.
        self._obj = obj
        return super().get_form(request, obj, **kwargs)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "adresse_associee":
            obj = getattr(self, "_obj", None)
            if obj is not None and obj.tiers_id:
                # Livraison et facturation conviennent toutes les deux, y
                # compris une adresse cochée pour les deux à la fois.
                kwargs["queryset"] = Adresse.objects.filter(tiers_id=obj.tiers_id)
            else:
                kwargs["queryset"] = Adresse.objects.none()
                kwargs["help_text"] = (
                    "Non disponible tant que le contact et son tiers n'ont pas été enregistrés "
                    "une première fois : enregistrez d'abord, puis revenez associer une adresse."
                )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(TauxTVA)
class TauxTVAAdmin(ModelAdmin):
    list_display = ["nom", "taux", "est_defaut"]
    list_filter = ["est_defaut"]
    search_fields = ["nom"]


@admin.register(DelaiPropose)
class DelaiProposeAdmin(ModelAdmin):
    list_display = ["libelle", "ordre"]
    search_fields = ["libelle"]
    ordering = ["ordre", "libelle"]


@admin.register(ConditionPaiement)
class ConditionPaiementAdmin(ModelAdmin):
    list_display = ["libelle", "nombre_jours", "fin_de_mois", "ordre"]
    search_fields = ["libelle"]
    ordering = ["ordre", "libelle"]


@admin.register(Pays)
class PaysAdmin(ModelAdmin):
    list_display = ["code", "nom", "est_ue"]
    list_filter = ["est_ue"]
    search_fields = ["code", "nom"]


@admin.register(Devise)
class DeviseAdmin(ModelAdmin):
    list_display = ["code", "nom", "symbole"]
    search_fields = ["code", "nom"]
