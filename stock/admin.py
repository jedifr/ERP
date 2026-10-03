from django.contrib import admin, messages
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from unfold.admin import ModelAdmin, TabularInline

from codification.mixins import CodificationInitialeMixin
from codification.models import RegleCodification

from comptes.concurrence import VerrouOptimisteMixin
from comptes.historique import HistoriqueLectureSeule

from .models import (
    AlerteStock,
    Emplacement,
    Inventaire,
    InventaireError,
    InventaireLigne,
    Lot,
    MouvementImmuableError,
    MouvementStock,
    StockInsuffisantError,
    Transfert,
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
class EmplacementAdmin(VerrouOptimisteMixin, CodificationInitialeMixin, HistoriqueLectureSeule, ModelAdmin):
    codification_entite = RegleCodification.Entite.EMPLACEMENT

    list_display = ["code", "libelle"]
    search_fields = ["code", "libelle"]


@admin.register(Lot)
class LotAdmin(VerrouOptimisteMixin, HistoriqueLectureSeule, ModelAdmin):
    list_display = ["article", "emplacement", "quantite", "cout_unitaire_moyen", "valeur_stock", "statut"]
    list_filter = ["emplacement", "statut"]
    search_fields = ["article__reference"]
    autocomplete_fields = ["article", "emplacement"]
    readonly_fields = ["quantite", "cout_unitaire_moyen", "valeur_stock"]
    inlines = [MouvementStockInline]

    def get_readonly_fields(self, request, obj=None):
        champs = list(super().get_readonly_fields(request, obj))
        if obj is not None and obj.mouvements.exists():
            # Changer l'article ou l'emplacement d'un lot déjà mouvementé fausserait
            # tout son historique : on passe par un transfert.
            champs += ["article", "emplacement"]
        return champs

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


@admin.register(Transfert)
class TransfertAdmin(ModelAdmin):
    """Déplacer du stock d'un emplacement à un autre : saisie seule, jamais modifiable."""

    list_display = ["date_transfert", "lot_source", "emplacement_cible", "quantite", "motif", "utilisateur"]
    autocomplete_fields = ["lot_source", "emplacement_cible"]
    readonly_fields = ["lot_cible", "utilisateur", "date_creation"]
    search_fields = ["lot_source__article__reference", "motif"]

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        obj.utilisateur = request.user
        try:
            super().save_model(request, obj, form, change)
        except StockInsuffisantError as exc:
            self.message_user(request, str(exc), level=messages.ERROR)


class InventaireLigneInline(TabularInline):
    model = InventaireLigne
    extra = 0
    autocomplete_fields = ["lot"]
    fields = ["lot", "quantite_comptee", "quantite_theorique", "ecart"]
    readonly_fields = ["quantite_theorique", "ecart"]

    def _brouillon(self, obj):
        return obj is None or obj.statut == Inventaire.Statut.BROUILLON

    def has_add_permission(self, request, obj=None):
        return self._brouillon(obj) and super().has_add_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        return self._brouillon(obj) and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return self._brouillon(obj) and super().has_delete_permission(request, obj)


@admin.register(Inventaire)
class InventaireAdmin(ModelAdmin):
    list_display = ["__str__", "statut", "commentaire", "utilisateur_validation", "date_validation"]
    list_filter = ["statut"]
    readonly_fields = ["statut", "utilisateur_validation", "date_validation"]
    inlines = [InventaireLigneInline]
    actions = ["action_ajouter_tous_les_lots", "action_valider"]

    def has_delete_permission(self, request, obj=None):
        return (obj is None or obj.statut == Inventaire.Statut.BROUILLON) and super().has_delete_permission(request, obj)

    @admin.action(description="Ajouter tous les lots en stock (brouillons seulement)")
    def action_ajouter_tous_les_lots(self, request, queryset):
        total = 0
        for inventaire in queryset.filter(statut=Inventaire.Statut.BROUILLON):
            existants = set(inventaire.lignes.values_list("lot_id", flat=True))
            for lot in Lot.objects.filter(article__gere_en_stock=True).exclude(pk__in=existants):
                InventaireLigne.objects.create(inventaire=inventaire, lot=lot, quantite_comptee=lot.quantite)
                total += 1
        self.message_user(request, f"{total} ligne(s) ajoutée(s), à corriger avec les quantités comptées.")

    @admin.action(description="Valider l'inventaire (ajuste le stock)", permissions=["valider"])
    def action_valider(self, request, queryset):
        for inventaire in queryset:
            try:
                inventaire.valider(utilisateur=request.user)
            except (InventaireError, StockInsuffisantError) as exc:
                self.message_user(request, f"{inventaire} : {exc}", level=messages.ERROR)
            else:
                nb = inventaire.lignes.exclude(ecart=0).count()
                self.message_user(
                    request, f"{inventaire} validé : {nb} ajustement(s) de stock.", level=messages.SUCCESS
                )

    def has_valider_permission(self, request):
        return request.user.has_perm("stock.valider_inventaire")


@admin.register(AlerteStock)
class AlerteStockAdmin(ModelAdmin):
    list_display = ["article", "statut", "date_declenchement", "date_traitement"]
    list_filter = ["statut"]
    search_fields = ["article__reference"]
    autocomplete_fields = ["article"]
