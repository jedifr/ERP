from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline

from codification.mixins import CodificationInitialeMixin
from codification.models import RegleCodification

from .models import AlerteStock, Emplacement, Lot, MouvementStock


class MouvementStockInline(TabularInline):
    model = MouvementStock
    extra = 0


@admin.register(Emplacement)
class EmplacementAdmin(CodificationInitialeMixin, ModelAdmin):
    codification_entite = RegleCodification.Entite.EMPLACEMENT

    list_display = ["code", "libelle"]
    search_fields = ["code", "libelle"]


@admin.register(Lot)
class LotAdmin(ModelAdmin):
    list_display = ["article", "emplacement", "quantite", "statut"]
    list_filter = ["emplacement", "statut"]
    search_fields = ["article__reference"]
    autocomplete_fields = ["article", "emplacement"]
    readonly_fields = ["quantite"]
    inlines = [MouvementStockInline]


@admin.register(MouvementStock)
class MouvementStockAdmin(ModelAdmin):
    list_display = ["lot", "type_mouvement", "quantite", "date_mouvement", "reference_origine"]
    list_filter = ["type_mouvement"]
    search_fields = ["lot__article__reference", "reference_origine"]
    autocomplete_fields = ["lot"]


@admin.register(AlerteStock)
class AlerteStockAdmin(ModelAdmin):
    list_display = ["article", "statut", "date_declenchement", "date_traitement"]
    list_filter = ["statut"]
    search_fields = ["article__reference"]
    autocomplete_fields = ["article"]
