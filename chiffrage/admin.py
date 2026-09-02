from django.contrib import admin, messages
from django.http import HttpResponseRedirect
from django.urls import path, reverse
from django.utils.html import format_html
from unfold.admin import ModelAdmin, TabularInline

from codification.mixins import CodificationInitialeMixin
from codification.models import RegleCodification

from .builder_views import (
    contact_associe_adresse_view,
    devis_builder_view,
    previsualiser_ligne_nouveau_devis_view,
    previsualiser_ligne_view,
    recalculer_ligne_view,
    valeurs_defaut_tiers_view,
)
from .models import Commande, Devis, DevisLigne, DevisLigneOperation, OperationOF, OrdreFabrication
from .moteur import ChiffrageError, calculer_devis
from .planning_sync import resynchroniser
from .production import lancer_en_production


class DevisLigneInline(TabularInline):
    model = DevisLigne
    extra = 1
    autocomplete_fields = ["article"]
    readonly_fields = [
        "cout_matiere_calcule",
        "prix_vente_matiere",
        "prix_vente_operations",
        "prix_vente_total",
        "prix_vente_ttc",
    ]


class DevisLigneOperationInline(TabularInline):
    model = DevisLigneOperation
    extra = 0
    readonly_fields = ["poste", "ordre", "cout_calcule", "prix_vente"]
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Devis)
class DevisAdmin(CodificationInitialeMixin, ModelAdmin):
    codification_entite = RegleCodification.Entite.DEVIS

    list_display = [
        "numero",
        "client",
        "date_creation",
        "statut",
        "taux_marge_globale",
        "montant_matiere_ht",
        "montant_operations_ht",
        "montant_total_ht",
        "montant_total_ttc",
    ]
    list_filter = ["statut"]
    search_fields = ["numero", "client__raison_sociale"]
    autocomplete_fields = ["client", "adresse_facturation", "adresse_livraison", "contact"]
    readonly_fields = [
        "montant_matiere_ht_display",
        "montant_operations_ht_display",
        "montant_total_ht_display",
        "montant_total_ttc_display",
    ]
    inlines = [DevisLigneInline]
    actions = ["action_recalculer", "action_lancer_en_production"]

    class Media:
        js = ["chiffrage/devis_admin_live.js"]
        css = {"all": ["chiffrage/devis_admin_live.css"]}

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("lignes__operations", "lignes__taux_tva")

    # Wrappés dans un <span id="..."> (plutôt que les propriétés du modèle
    # directement) pour offrir un point d'accroche stable au JS de recalcul
    # en direct (Unfold ne pose pas de classe `field-<nom>` sur les champs
    # readonly de premier niveau, contrairement à ses tableaux inline).
    @admin.display(description="Montant matière HT")
    def montant_matiere_ht_display(self, obj):
        return format_html('<span id="montant-matiere-ht">{}</span>', obj.montant_matiere_ht)

    @admin.display(description="Montant opérations HT (temps machine / main d'œuvre)")
    def montant_operations_ht_display(self, obj):
        return format_html('<span id="montant-operations-ht">{}</span>', obj.montant_operations_ht)

    @admin.display(description="Montant total HT")
    def montant_total_ht_display(self, obj):
        return format_html('<span id="montant-total-ht">{}</span>', obj.montant_total_ht)

    @admin.display(description="Montant total TTC")
    def montant_total_ttc_display(self, obj):
        return format_html('<span id="montant-total-ttc">{}</span>', obj.montant_total_ttc)

    def response_add(self, request, obj, post_url_continue=None):
        # Bouton "Enregistrer et ouvrir le constructeur" du formulaire d'ajout :
        # le devis (et ses lignes déjà saisies dans l'inline) vient d'être
        # enregistré normalement par la vue d'admin ; on redirige simplement
        # vers le constructeur au lieu de la liste/fiche par défaut.
        if "_construire" in request.POST:
            return HttpResponseRedirect(reverse("admin:chiffrage_devis_builder", args=[obj.pk]))
        return super().response_add(request, obj, post_url_continue)

    def get_urls(self):
        urls = [
            # Chemin fixe (pas de <str:numero>) : utilisé sur le formulaire
            # d'AJOUT d'un devis, qui n'a par définition pas encore de numéro
            # (l'objet Devis n'existe pas encore en base).
            path(
                "nouveau-devis/previsualiser-ligne/",
                self.admin_site.admin_view(previsualiser_ligne_nouveau_devis_view),
                name="chiffrage_devisligne_previsualiser_nouveau_devis",
            ),
            path(
                "tiers/<str:code>/valeurs-defaut/",
                self.admin_site.admin_view(valeurs_defaut_tiers_view),
                name="chiffrage_devis_valeurs_defaut_tiers",
            ),
            path(
                "adresses/<int:adresse_id>/contact-associe/",
                self.admin_site.admin_view(contact_associe_adresse_view),
                name="chiffrage_devis_contact_associe_adresse",
            ),
            path(
                "<str:numero>/constructeur/",
                self.admin_site.admin_view(devis_builder_view),
                name="chiffrage_devis_builder",
            ),
            path(
                "<str:numero>/lignes/<int:ligne_id>/recalculer/",
                self.admin_site.admin_view(recalculer_ligne_view),
                name="chiffrage_devisligne_recalculer",
            ),
            path(
                "<str:numero>/lignes/previsualiser/",
                self.admin_site.admin_view(previsualiser_ligne_view),
                name="chiffrage_devisligne_previsualiser",
            ),
        ]
        return urls + super().get_urls()

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
class DevisLigneAdmin(ModelAdmin):
    list_display = [
        "devis",
        "article",
        "quantite",
        "cout_matiere_calcule",
        "prix_vente_matiere",
        "prix_vente_operations",
        "prix_vente_total",
        "taux_tva",
        "prix_vente_ttc",
    ]
    search_fields = ["devis__numero", "article__reference"]
    autocomplete_fields = ["devis", "article"]
    inlines = [DevisLigneOperationInline]


@admin.register(Commande)
class CommandeAdmin(CodificationInitialeMixin, ModelAdmin):
    codification_entite = RegleCodification.Entite.COMMANDE

    list_display = ["numero", "devis", "date_commande", "statut"]
    search_fields = ["numero", "devis__numero"]
    autocomplete_fields = ["devis", "adresse_facturation", "adresse_livraison"]


class OperationOFInline(TabularInline):
    model = OperationOF
    extra = 0


@admin.register(OrdreFabrication)
class OrdreFabricationAdmin(CodificationInitialeMixin, ModelAdmin):
    codification_entite = RegleCodification.Entite.ORDRE_FABRICATION

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
class OperationOFAdmin(ModelAdmin):
    list_display = ["ordre_fabrication", "ordre", "poste", "temps_prevu", "temps_reel", "statut"]
    search_fields = ["ordre_fabrication__numero", "poste__nom"]
    autocomplete_fields = ["ordre_fabrication", "poste"]
