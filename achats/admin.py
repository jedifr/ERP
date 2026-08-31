from django.contrib import admin, messages
from unfold.admin import ModelAdmin, TabularInline

from .models import AchatsError, CommandeFournisseur, LigneCommandeFournisseur, Reception, ReceptionLigne


class LigneCommandeFournisseurInline(TabularInline):
    model = LigneCommandeFournisseur
    extra = 1
    autocomplete_fields = ["article", "alerte_stock_origine"]
    readonly_fields = ["quantite_recue"]


@admin.register(CommandeFournisseur)
class CommandeFournisseurAdmin(ModelAdmin):
    list_display = ["numero", "fournisseur", "date_commande", "date_livraison_prevue", "statut"]
    list_filter = ["statut"]
    search_fields = ["numero", "fournisseur__raison_sociale"]
    autocomplete_fields = ["fournisseur"]
    inlines = [LigneCommandeFournisseurInline]


@admin.register(LigneCommandeFournisseur)
class LigneCommandeFournisseurAdmin(ModelAdmin):
    list_display = [
        "commande_fournisseur",
        "article",
        "quantite_commandee",
        "quantite_recue",
        "prix_unitaire_achat",
    ]
    search_fields = ["commande_fournisseur__numero", "article__reference"]
    autocomplete_fields = ["commande_fournisseur", "article", "alerte_stock_origine"]
    readonly_fields = ["quantite_recue"]


class ReceptionLigneInline(TabularInline):
    model = ReceptionLigne
    extra = 1
    autocomplete_fields = ["ligne_commande_fournisseur"]


@admin.register(Reception)
class ReceptionAdmin(ModelAdmin):
    list_display = ["numero", "commande_fournisseur", "date_reception"]
    search_fields = ["numero", "commande_fournisseur__numero"]
    autocomplete_fields = ["commande_fournisseur"]
    inlines = [ReceptionLigneInline]

    def save_formset(self, request, form, formset, change):
        try:
            super().save_formset(request, form, formset, change)
        except AchatsError as exc:
            self.message_user(request, str(exc), level=messages.ERROR)


@admin.register(ReceptionLigne)
class ReceptionLigneAdmin(ModelAdmin):
    list_display = ["reception", "ligne_commande_fournisseur", "quantite_recue"]
    search_fields = ["reception__numero", "ligne_commande_fournisseur__article__reference"]
    autocomplete_fields = ["reception", "ligne_commande_fournisseur"]
