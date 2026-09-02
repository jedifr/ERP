from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline

from codification.mixins import CodificationInitialeMixin
from codification.models import RegleCodification

from .models import EnvoiSousTraitance, RetourSousTraitance


class RetourSousTraitanceInline(TabularInline):
    model = RetourSousTraitance
    extra = 1


@admin.register(EnvoiSousTraitance)
class EnvoiSousTraitanceAdmin(CodificationInitialeMixin, ModelAdmin):
    codification_entite = RegleCodification.Entite.ENVOI_SOUS_TRAITANCE

    list_display = ["numero", "operation_of", "sous_traitant", "date_envoi", "quantite_envoyee", "statut"]
    list_filter = ["statut"]
    search_fields = ["numero", "sous_traitant__raison_sociale"]
    autocomplete_fields = ["operation_of", "sous_traitant"]
    inlines = [RetourSousTraitanceInline]


@admin.register(RetourSousTraitance)
class RetourSousTraitanceAdmin(CodificationInitialeMixin, ModelAdmin):
    codification_entite = RegleCodification.Entite.RETOUR_SOUS_TRAITANCE

    list_display = ["numero", "envoi", "date_retour", "quantite_retournee", "conforme"]
    list_filter = ["conforme"]
    search_fields = ["numero", "envoi__numero"]
    autocomplete_fields = ["envoi"]
