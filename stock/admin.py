from django.contrib import admin, messages
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from unfold.admin import ModelAdmin, TabularInline

from codification.mixins import CodificationInitialeMixin
from codification.models import RegleCodification

from .models import (
    AlerteStock,
    Emplacement,
    Lot,
    MouvementImmuableError,
    MouvementStock,
    StockInsuffisantError,
)

PREFIXES_DOCUMENTS = ("LIVRAISON-", "RECEPTION-")


class MouvementStockInline(TabularInline):
    """Saisie d'un mouvement depuis la fiche du lot : on peut en ajouter, jamais
    modifier ni supprimer un existant (journal immuable)."""

    model = MouvementStock
    extra = 0
    fields = ["type_mouvement", "quantite", "date_mouvement", "reference_origine", "motif", "utilisateur", "date_creation"]
    readonly_fields = ["utilisateur", "date_creation"]

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


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

    def save_formset(self, request, form, formset, change):
        if formset.model is not MouvementStock:
            return super().save_formset(request, form, formset, change)
        for mouvement in formset.save(commit=False):
            mouvement.utilisateur = request.user
            try:
                mouvement.save()
            except StockInsuffisantError as exc:
                self.message_user(request, str(exc), level=messages.ERROR)
        formset.save_m2m()


@admin.register(MouvementStock)
class MouvementStockAdmin(ModelAdmin):
    list_display = [
        "date_mouvement",
        "article_lot",
        "emplacement_lot",
        "type_mouvement",
        "quantite",
        "reference_origine",
        "motif",
        "utilisateur",
    ]
    list_filter = ["type_mouvement"]
    list_select_related = ["lot__article", "lot__emplacement", "utilisateur"]
    search_fields = ["lot__article__reference", "reference_origine", "motif"]
    autocomplete_fields = ["lot"]
    readonly_fields = ["utilisateur", "date_creation", "annule_mouvement"]
    actions = ["action_annuler"]

    @admin.display(description="Article", ordering="lot__article__reference")
    def article_lot(self, obj):
        return obj.lot.article.reference

    @admin.display(description="Emplacement", ordering="lot__emplacement__code")
    def emplacement_lot(self, obj):
        return obj.lot.emplacement.code

    def has_change_permission(self, request, obj=None):
        # Journal immuable : un mouvement s'affiche, il ne s'édite pas.
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        obj.utilisateur = request.user
        try:
            super().save_model(request, obj, form, change)
        except StockInsuffisantError as exc:
            self.message_user(request, str(exc), level=messages.ERROR)

    def get_urls(self):
        urls = [
            path(
                "<int:mouvement_id>/annuler/",
                self.admin_site.admin_view(self.annuler_view),
                name="stock_mouvementstock_annuler",
            ),
        ]
        return urls + super().get_urls()

    @admin.action(description="Annuler le mouvement (contre-passation)", permissions=["annuler"])
    def action_annuler(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, "Sélectionnez un seul mouvement à annuler.", level=messages.ERROR)
            return None
        return HttpResponseRedirect(reverse("admin:stock_mouvementstock_annuler", args=[queryset.get().pk]))

    def has_annuler_permission(self, request):
        return request.user.has_perm("stock.annuler_mouvement")

    def annuler_view(self, request, mouvement_id):
        mouvement = MouvementStock.objects.select_related("lot__article").get(pk=mouvement_id)
        retour = reverse("admin:stock_mouvementstock_changelist")
        if not request.user.has_perm("stock.annuler_mouvement"):
            self.message_user(request, "Vous n'avez pas la permission d'annuler un mouvement.", level=messages.ERROR)
            return HttpResponseRedirect(retour)
        if request.method == "POST":
            try:
                inverse = mouvement.annuler(utilisateur=request.user, motif=request.POST.get("motif", "").strip())
            except (MouvementImmuableError, StockInsuffisantError) as exc:
                self.message_user(request, str(exc), level=messages.ERROR)
            else:
                self.message_user(request, f"Mouvement annulé : {inverse}.", level=messages.SUCCESS)
            return HttpResponseRedirect(retour)
        avertissement = ""
        if mouvement.reference_origine.startswith(PREFIXES_DOCUMENTS):
            avertissement = (
                f"Ce mouvement a été généré par un document ({mouvement.reference_origine}) : "
                "pensez à corriger ce document, qui ne sera pas modifié par cette annulation."
            )
        contexte = {
            **self.admin_site.each_context(request),
            "title": "Annuler un mouvement de stock",
            "mouvement": mouvement,
            "inverse": "sortie" if mouvement.type_mouvement == MouvementStock.TypeMouvement.ENTREE else "entrée",
            "avertissement": avertissement,
            "retour": retour,
            "motif": "",
        }
        return TemplateResponse(request, "admin/stock/annuler_mouvement.html", contexte)


@admin.register(AlerteStock)
class AlerteStockAdmin(ModelAdmin):
    list_display = ["article", "statut", "date_declenchement", "date_traitement"]
    list_filter = ["statut"]
    search_fields = ["article__reference"]
    autocomplete_fields = ["article"]
