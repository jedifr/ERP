from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.forms.models import BaseInlineFormSet
from django.shortcuts import redirect
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import action

from .models import (
    ArticleCompteAchat,
    ArticleCompteVente,
    CodeAnalytique,
    CompteComptable,
    EcritureComptable,
    JournalComptable,
    LigneEcriture,
    ParametresComptables,
    PosteGestion,
    TiersCompteComptable,
)
from .pcg import PCG_MILLESIME, importer_pcg
from .postes_gestion import importer_postes_gestion


@admin.register(CompteComptable)
class CompteComptableAdmin(ModelAdmin):
    list_display = ["code", "libelle", "classe", "systeme", "actif"]
    list_filter = ["classe", "systeme", "actif"]
    search_fields = ["code", "libelle"]
    autocomplete_fields = ["compte_parent"]
    actions_list = ["action_importer_pcg"]

    @action(
        description=f"Importer le plan comptable officiel (PCG {PCG_MILLESIME})",
        icon="cloud_download",
    )
    def action_importer_pcg(self, request):
        crees, maj = importer_pcg()
        self.message_user(
            request,
            f"Plan comptable {PCG_MILLESIME} importé : {crees} compte(s) créé(s), {maj} déjà à jour.",
            level=messages.SUCCESS,
        )
        return redirect("admin:comptabilite_comptecomptable_changelist")


@admin.register(JournalComptable)
class JournalComptableAdmin(ModelAdmin):
    list_display = ["code", "libelle", "nature", "actif"]
    list_filter = ["nature", "actif"]
    search_fields = ["code", "libelle"]


@admin.register(PosteGestion)
class PosteGestionAdmin(ModelAdmin):
    list_display = ["code", "libelle", "groupe", "actif"]
    list_filter = ["groupe", "actif"]
    search_fields = ["code", "libelle"]
    actions_list = ["action_importer_postes_gestion"]

    @action(description="Importer les postes de gestion (achat/vente)", icon="cloud_download")
    def action_importer_postes_gestion(self, request):
        postes_crees, postes_maj, comptes_crees = importer_postes_gestion()
        self.message_user(
            request,
            f"Postes de gestion : {postes_crees} créé(s), {postes_maj} mis à jour "
            f"({comptes_crees} compte(s) comptable(s) créé(s) au passage).",
            level=messages.SUCCESS,
        )
        return redirect("admin:comptabilite_postegestion_changelist")

    autocomplete_fields = [
        "compte_achat_france",
        "compte_achat_france_exonere",
        "compte_achat_intra_ue",
        "compte_achat_hors_ue",
        "compte_vente_france",
        "compte_vente_france_exonere",
        "compte_vente_intra_ue",
        "compte_vente_hors_ue",
        "compte_vente_tva_majoree",
        "code_analytique",
    ]
    fieldsets = [
        (None, {"fields": ["code", "libelle", "groupe", "actif", "code_analytique"]}),
        (
            "Achat, par régime fiscal du fournisseur",
            {
                "fields": [
                    "compte_achat_france",
                    "compte_achat_france_exonere",
                    "compte_achat_intra_ue",
                    "compte_achat_hors_ue",
                ]
            },
        ),
        (
            "Vente, par régime fiscal du client",
            {
                "fields": [
                    "compte_vente_france",
                    "compte_vente_france_exonere",
                    "compte_vente_intra_ue",
                    "compte_vente_hors_ue",
                    "compte_vente_tva_majoree",
                ]
            },
        ),
    ]


@admin.register(ArticleCompteVente)
class ArticleCompteVenteAdmin(ModelAdmin):
    list_display = ["article", "poste_gestion", "compte_vente", "code_analytique"]
    search_fields = ["article__reference", "article__libelle", "compte_vente__code", "poste_gestion__code"]
    autocomplete_fields = ["article", "poste_gestion", "compte_vente", "code_analytique"]


@admin.register(ArticleCompteAchat)
class ArticleCompteAchatAdmin(ModelAdmin):
    list_display = ["article", "poste_gestion", "compte_achat", "code_analytique"]
    search_fields = ["article__reference", "article__libelle", "compte_achat__code", "poste_gestion__code"]
    autocomplete_fields = ["article", "poste_gestion", "compte_achat", "code_analytique"]


@admin.register(TiersCompteComptable)
class TiersCompteComptableAdmin(ModelAdmin):
    list_display = ["tiers", "compte_client", "compte_fournisseur"]
    search_fields = ["tiers__code", "tiers__raison_sociale", "compte_client__code", "compte_fournisseur__code"]
    autocomplete_fields = ["tiers", "compte_client", "compte_fournisseur"]


@admin.register(CodeAnalytique)
class CodeAnalytiqueAdmin(ModelAdmin):
    list_display = ["code", "libelle", "actif"]
    list_filter = ["actif"]
    search_fields = ["code", "libelle"]


@admin.register(ParametresComptables)
class ParametresComptablesAdmin(ModelAdmin):
    fields = ["journal_ventes", "compte_client_defaut", "compte_vente_defaut", "compte_tva_collectee_defaut"]
    autocomplete_fields = fields

    def has_add_permission(self, request):
        # Ligne unique (ParametresComptables.charger()) : jamais d'ajout
        # une fois qu'elle existe, on modifie toujours la même.
        return not ParametresComptables.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        parametres = ParametresComptables.charger()
        return redirect("admin:comptabilite_parametrescomptables_change", parametres.pk)


class LigneEcritureFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        total_debit = total_credit = 0
        for form in self.forms:
            if not form.cleaned_data or form.cleaned_data.get("DELETE"):
                continue
            total_debit += form.cleaned_data.get("debit") or 0
            total_credit += form.cleaned_data.get("credit") or 0
        if round(total_debit - total_credit, 2) != 0:
            raise ValidationError(
                f"L'écriture n'est pas équilibrée : débit {total_debit:g} ≠ crédit {total_credit:g}."
            )


class LigneEcritureInline(TabularInline):
    model = LigneEcriture
    formset = LigneEcritureFormSet
    extra = 2
    autocomplete_fields = ["compte", "code_analytique"]


@admin.register(EcritureComptable)
class EcritureComptableAdmin(ModelAdmin):
    list_display = ["piece", "journal", "date_ecriture", "libelle", "total_debit", "total_credit", "est_equilibree"]
    list_filter = ["journal"]
    search_fields = ["piece", "libelle", "facture__numero"]
    autocomplete_fields = ["journal", "facture"]
    date_hierarchy = "date_ecriture"
    inlines = [LigneEcritureInline]

    @admin.display(description="Équilibrée", boolean=True)
    def est_equilibree(self, obj):
        return obj.est_equilibree
