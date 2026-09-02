from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline

from codification.mixins import CodificationInitialeMixin
from codification.models import RegleCodification

from .models import Adresse, Contact, DelaiPropose, TauxTVA, Tiers


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


class ContactInline(TabularInline):
    model = Contact
    extra = 0
    autocomplete_fields = ["adresse_livraison"]


@admin.register(Tiers)
class TiersAdmin(CodificationInitialeMixin, ModelAdmin):
    codification_entite = RegleCodification.Entite.TIERS

    list_display = ["code", "raison_sociale", "type_tiers", "siret"]
    list_filter = ["type_tiers"]
    search_fields = ["code", "raison_sociale", "siret"]
    inlines = [AdresseInline, ContactInline]


@admin.register(Adresse)
class AdresseAdmin(ModelAdmin):
    list_display = ["tiers", "type_adresse", "libelle", "ville", "est_principale"]
    list_filter = ["type_adresse", "est_principale"]
    search_fields = ["tiers__code", "tiers__raison_sociale", "ville", "libelle"]
    autocomplete_fields = ["tiers"]


@admin.register(Contact)
class ContactAdmin(ModelAdmin):
    list_display = [
        "nom",
        "prenom",
        "tiers",
        "email",
        "telephone",
        "fonction",
        "est_principal",
        "adresse_livraison",
    ]
    list_filter = ["est_principal"]
    search_fields = ["nom", "prenom", "tiers__code", "tiers__raison_sociale"]
    autocomplete_fields = ["tiers", "adresse_livraison"]


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
