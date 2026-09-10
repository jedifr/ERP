import re

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


class ContactTelephoneInline(TabularInline):
    model = ContactTelephone
    extra = 1


class ContactInline(TabularInline):
    model = Contact
    extra = 0
    autocomplete_fields = ["adresse_livraison"]
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
    à partir d'un code à 5 lettres (voir TiersCompteComptable.save()) —
    utilisé par le JS de la fiche Tiers pour afficher, dès la frappe, le
    compte qui sera résolu (existant, avec son libellé, ou à créer) sans
    attendre l'enregistrement du formulaire."""
    prefixe = request.GET.get("prefixe")
    code = (request.GET.get("code") or "").strip()
    if prefixe not in ("411", "401") or not re.fullmatch(r"[A-Za-z]{5}", code):
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
        js = ["commercial/tiers_admin.js"]

    def get_urls(self):
        urls = [
            path(
                "apercu-compte-comptable/",
                self.admin_site.admin_view(apercu_compte_comptable_view),
                name="commercial_tiers_apercu_compte_comptable",
            ),
        ]
        return urls + super().get_urls()


@admin.register(Adresse)
class AdresseAdmin(ModelAdmin):
    list_display = ["tiers", "type_adresse", "libelle", "ville", "pays", "est_principale"]
    list_filter = ["type_adresse", "est_principale", "pays"]
    search_fields = ["tiers__code", "tiers__raison_sociale", "ville", "libelle"]
    autocomplete_fields = ["tiers", "pays"]


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
        "adresse_livraison",
    ]
    list_filter = ["est_principal"]
    search_fields = ["nom", "prenom", "tiers__code", "tiers__raison_sociale"]
    autocomplete_fields = ["tiers", "adresse_livraison"]
    inlines = [ContactTelephoneInline]

    @admin.display(description="Téléphones")
    def telephones_display(self, obj):
        return ", ".join(f"{t.get_type_telephone_display()} : {t.numero}" for t in obj.telephones.all()) or "—"


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
