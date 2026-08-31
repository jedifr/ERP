from django.contrib import admin, messages

from .models import Commande, Devis, DevisLigne, DevisLigneOperation, OperationOF, OrdreFabrication
from .moteur import ChiffrageError, calculer_devis
from .planning_sync import resynchroniser
from .production import lancer_en_production


class DevisLigneInline(admin.TabularInline):
    model = DevisLigne
    extra = 1
    autocomplete_fields = ["article"]
    readonly_fields = ["cout_matiere_calcule", "prix_vente_matiere"]


class DevisLigneOperationInline(admin.TabularInline):
    model = DevisLigneOperation
    extra = 0
    readonly_fields = ["poste", "ordre", "cout_calcule", "prix_vente"]
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Devis)
class DevisAdmin(admin.ModelAdmin):
    list_display = ["numero", "client", "date_creation", "statut", "taux_marge_globale"]
    list_filter = ["statut"]
    search_fields = ["numero", "client__raison_sociale"]
    autocomplete_fields = ["client"]
    inlines = [DevisLigneInline]
    actions = ["action_recalculer", "action_lancer_en_production"]

    @admin.action(description="Recalculer le chiffrage")
    def action_recalculer(self, request, queryset):
        for devis in queryset:
            try:
                calculer_devis(devis)
            except ChiffrageError as exc:
                self.message_user(request, f"{devis} : {exc}", level=messages.ERROR)
            else:
                self.message_user(request, f"{devis} : chiffrage recalculé.", level=messages.SUCCESS)

    @admin.action(description="Lancer en production")
    def action_lancer_en_production(self, request, queryset):
        for devis in queryset:
            try:
                commande = lancer_en_production(devis)
            except ChiffrageError as exc:
                self.message_user(request, f"{devis} : {exc}", level=messages.ERROR)
            else:
                self.message_user(
                    request, f"{devis} : commande {commande} créée.", level=messages.SUCCESS
                )


@admin.register(DevisLigne)
class DevisLigneAdmin(admin.ModelAdmin):
    list_display = ["devis", "article", "quantite", "cout_matiere_calcule", "prix_vente_matiere"]
    search_fields = ["devis__numero", "article__reference"]
    autocomplete_fields = ["devis", "article"]
    inlines = [DevisLigneOperationInline]


@admin.register(Commande)
class CommandeAdmin(admin.ModelAdmin):
    list_display = ["numero", "devis", "date_commande", "statut"]
    search_fields = ["numero", "devis__numero"]
    autocomplete_fields = ["devis", "adresse_facturation", "adresse_livraison"]


class OperationOFInline(admin.TabularInline):
    model = OperationOF
    extra = 0


@admin.register(OrdreFabrication)
class OrdreFabricationAdmin(admin.ModelAdmin):
    list_display = [
        "numero",
        "commande",
        "article",
        "quantite",
        "statut",
        "statut_synchro",
        "nombre_tentatives",
    ]
    list_filter = ["statut_synchro"]
    search_fields = ["numero", "commande__numero", "article__reference"]
    autocomplete_fields = ["commande", "article"]
    inlines = [OperationOFInline]
    actions = ["action_resynchroniser"]

    @admin.action(description="Resynchroniser avec le planning atelier")
    def action_resynchroniser(self, request, queryset):
        for of in queryset:
            reussite = resynchroniser(of)
            niveau = messages.SUCCESS if reussite else messages.WARNING
            statut = "synchronisé" if reussite else f"toujours en échec ({of.statut_synchro})"
            self.message_user(request, f"{of} : {statut}.", level=niveau)


@admin.register(OperationOF)
class OperationOFAdmin(admin.ModelAdmin):
    list_display = ["ordre_fabrication", "ordre", "poste", "temps_prevu", "temps_reel", "statut"]
    search_fields = ["ordre_fabrication__numero", "poste__nom"]
    autocomplete_fields = ["ordre_fabrication", "poste"]
