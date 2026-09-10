from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline

from codification.mixins import CodificationInitialeMixin
from codification.models import RegleCodification
from comptabilite.models import TiersCompteComptable

from .models import Adresse, Contact, ContactTelephone, ConditionPaiement, DelaiPropose, Pays, TauxTVA, Tiers


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


class ContactInline(TabularInline):
    model = Contact
    extra = 0
    autocomplete_fields = ["adresse_livraison"]


class TiersCompteComptableInline(TabularInline):
    # OneToOneField : Django limite automatiquement à un seul formulaire.
    model = TiersCompteComptable
    extra = 1
    autocomplete_fields = ["compte_client", "compte_fournisseur"]


@admin.register(Tiers)
class TiersAdmin(CodificationInitialeMixin, ModelAdmin):
    codification_entite = RegleCodification.Entite.TIERS

    list_display = ["code", "raison_sociale", "type_tiers", "regime_fiscal", "siret"]
    list_filter = ["type_tiers", "regime_fiscal"]
    search_fields = ["code", "raison_sociale", "siret"]
    autocomplete_fields = ["conditions_paiement"]
    inlines = [AdresseInline, ContactInline, TiersCompteComptableInline]


@admin.register(Adresse)
class AdresseAdmin(ModelAdmin):
    list_display = ["tiers", "type_adresse", "libelle", "ville", "pays", "est_principale"]
    list_filter = ["type_adresse", "est_principale", "pays"]
    search_fields = ["tiers__code", "tiers__raison_sociale", "ville", "libelle"]
    autocomplete_fields = ["tiers", "pays"]


class ContactTelephoneInline(TabularInline):
    model = ContactTelephone
    extra = 1


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
