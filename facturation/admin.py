from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline

from .models import Facture


@admin.register(Facture)
class FactureAdmin(ModelAdmin):
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
