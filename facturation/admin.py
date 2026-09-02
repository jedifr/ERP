from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline

from codification.mixins import CodificationInitialeMixin
from codification.models import RegleCodification

from .models import Facture


@admin.register(Facture)
class FactureAdmin(CodificationInitialeMixin, ModelAdmin):
    codification_entite = RegleCodification.Entite.FACTURE

    list_display = [
        "numero",
        "commande",
        "date_facturation",
        "montant_ht",
        "montant_ttc",
        "statut_paiement",
        "mode_creation",
    ]
    list_filter = ["mode_creation", "statut_paiement"]
    search_fields = ["numero", "reference_tiime", "commande__numero"]
    autocomplete_fields = ["commande"]
