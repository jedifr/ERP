from django.contrib import admin, messages
from unfold.admin import ModelAdmin, TabularInline

from codification.mixins import CodificationInitialeMixin
from codification.models import RegleCodification

from .models import (
    AchatsError,
    ArticleFournisseur,
    CommandeFournisseur,
    LigneCommandeFournisseur,
    Reception,
    ReceptionLigne,
    TarifAchatArticle,
)


def tarif_actuel_display(obj):
    tarif = obj.tarif_actuel
    return f"{tarif.prix_unitaire} €" if tarif else "—"


class TarifAchatArticleInline(TabularInline):
    model = TarifAchatArticle
    extra = 1


@admin.register(ArticleFournisseur)
class ArticleFournisseurAdmin(ModelAdmin):
    list_display = [
        "article",
        "fournisseur",
        "reference_fournisseur",
        "designation_fournisseur",
        "tarif_actuel_display",
    ]
    list_filter = ["fournisseur"]
    search_fields = ["article__reference", "fournisseur__raison_sociale", "reference_fournisseur"]
    autocomplete_fields = ["article", "fournisseur"]
    inlines = [TarifAchatArticleInline]

    @admin.display(description="Tarif actuel")
    def tarif_actuel_display(self, obj):
        return tarif_actuel_display(obj)


@admin.register(TarifAchatArticle)
class TarifAchatArticleAdmin(ModelAdmin):
    list_display = ["article_fournisseur", "prix_unitaire", "date_debut", "date_fin"]
    list_filter = ["article_fournisseur__fournisseur"]
    search_fields = ["article_fournisseur__article__reference", "article_fournisseur__fournisseur__raison_sociale"]
    autocomplete_fields = ["article_fournisseur"]


class LigneCommandeFournisseurInline(TabularInline):
    model = LigneCommandeFournisseur
    extra = 1
    autocomplete_fields = ["article", "alerte_stock_origine", "commande_ligne_client"]
    readonly_fields = ["quantite_recue"]


@admin.register(CommandeFournisseur)
class CommandeFournisseurAdmin(CodificationInitialeMixin, ModelAdmin):
    codification_entite = RegleCodification.Entite.COMMANDE_FOURNISSEUR

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
        "commande_ligne_client",
    ]
    search_fields = ["commande_fournisseur__numero", "article__reference"]
    autocomplete_fields = ["commande_fournisseur", "article", "alerte_stock_origine", "commande_ligne_client"]
    readonly_fields = ["quantite_recue"]


class ReceptionLigneInline(TabularInline):
    model = ReceptionLigne
    extra = 1
    autocomplete_fields = ["ligne_commande_fournisseur"]


@admin.register(Reception)
class ReceptionAdmin(CodificationInitialeMixin, ModelAdmin):
    codification_entite = RegleCodification.Entite.RECEPTION

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
