from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import path
from django.views.decorators.http import require_http_methods
from unfold.admin import ModelAdmin, TabularInline

from chiffrage.models import Commande
from codification.mixins import CodificationInitialeMixin
from codification.models import RegleCodification
from comptabilite.generation import GenerationEcritureError, generer_ecriture_facture

from .models import Facture


@staff_member_required
@require_http_methods(["GET"])
def montants_calcules_commande_view(request, numero):
    """Total HT/TTC indicatif de la commande `numero`, depuis ses lignes
    actuelles (Facture.montant_ht_calcule/montant_ttc_calcule) — utilisé
    par le JS de la fiche Facture pour pré-remplir montant_ht/montant_ttc
    dès qu'une commande est choisie sur le formulaire d'ajout, sans jamais
    écraser une valeur déjà saisie (voir facture_admin.js)."""
    commande = get_object_or_404(Commande, pk=numero)
    montants = [l.montant_ht for l in commande.lignes.all() if l.montant_ht is not None]
    montants_ttc = [l.montant_ttc for l in commande.lignes.all() if l.montant_ttc is not None]
    return JsonResponse(
        {
            "montant_ht": sum(montants) if montants else None,
            "montant_ttc": sum(montants_ttc) if montants_ttc else None,
        }
    )


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
    readonly_fields = ["montants_calcules_display"]

    class Media:
        js = ["facturation/facture_admin.js"]

    def get_urls(self):
        urls = [
            path(
                "<str:numero>/montants-calcules/",
                self.admin_site.admin_view(montants_calcules_commande_view),
                name="facturation_facture_montants_calcules",
            ),
        ]
        return urls + super().get_urls()

    @admin.display(description="Échéance")
    def date_echeance_display(self, obj):
        return obj.date_echeance or "—"

    @admin.display(description="Montants calculés depuis la commande (indicatif)")
    def montants_calcules_display(self, obj):
        if obj is None or obj.commande_id is None:
            return "—"
        ht = obj.montant_ht_calcule
        if ht is None:
            return "—"
        return f"HT : {ht:g} € — TTC : {obj.montant_ttc_calcule:g} €"

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
