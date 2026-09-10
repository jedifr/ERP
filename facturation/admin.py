from django.contrib import admin, messages
from unfold.admin import ModelAdmin, TabularInline

from codification.mixins import CodificationInitialeMixin
from codification.models import RegleCodification
from comptabilite.generation import GenerationEcritureError, generer_ecriture_facture

from .models import Facture


@admin.register(Facture)
class FactureAdmin(CodificationInitialeMixin, ModelAdmin):
    codification_entite = RegleCodification.Entite.FACTURE

    list_display = [
        "numero",
        "commande",
        "date_facturation",
        "date_echeance_display",
        "montant_ht",
        "montant_ttc",
        "statut_paiement",
        "mode_creation",
    ]
    list_filter = ["mode_creation", "statut_paiement"]
    search_fields = ["numero", "reference_tiime", "commande__numero"]
    autocomplete_fields = ["commande"]
    actions = ["action_generer_ecriture"]

    @admin.display(description="Échéance")
    def date_echeance_display(self, obj):
        return obj.date_echeance or "—"

    @admin.action(description="Générer l'écriture comptable")
    def action_generer_ecriture(self, request, queryset):
        creees = existantes = 0
        for facture in queryset:
            try:
                _, creee = generer_ecriture_facture(facture)
            except GenerationEcritureError as exc:
                self.message_user(request, f"{facture} : {exc}", level=messages.ERROR)
                continue
            creees += creee
            existantes += not creee
        if creees:
            self.message_user(request, f"{creees} écriture(s) comptable(s) générée(s).", level=messages.SUCCESS)
        if existantes:
            self.message_user(
                request, f"{existantes} facture(s) avaient déjà leur écriture.", level=messages.INFO
            )
