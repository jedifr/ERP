from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline

from codification.mixins import CodificationInitialeMixin
from codification.models import RegleCodification

from .models import Adresse, Contact, TauxTVA, Tiers


class AdresseInline(TabularInline):
    model = Adresse
    extra = 1


class ContactInline(TabularInline):
    model = Contact
    extra = 1


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
    search_fields = ["tiers__code", "tiers__raison_sociale", "ville"]
    autocomplete_fields = ["tiers"]


@admin.register(Contact)
class ContactAdmin(ModelAdmin):
    list_display = ["nom", "prenom", "tiers", "email", "telephone", "fonction"]
    search_fields = ["nom", "prenom", "tiers__code", "tiers__raison_sociale"]
    autocomplete_fields = ["tiers"]


@admin.register(TauxTVA)
class TauxTVAAdmin(ModelAdmin):
    list_display = ["nom", "taux", "est_defaut"]
    list_filter = ["est_defaut"]
    search_fields = ["nom"]
