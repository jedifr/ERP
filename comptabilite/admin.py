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
)
from .pcg import PCG_MILLESIME, importer_pcg


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


@admin.register(ArticleCompteVente)
class ArticleCompteVenteAdmin(ModelAdmin):
    list_display = ["article", "compte_vente", "code_analytique"]
    search_fields = ["article__reference", "article__libelle", "compte_vente__code"]
    autocomplete_fields = ["article", "compte_vente", "code_analytique"]


@admin.register(ArticleCompteAchat)
class ArticleCompteAchatAdmin(ModelAdmin):
    list_display = ["article", "compte_achat", "code_analytique"]
    search_fields = ["article__reference", "article__libelle", "compte_achat__code"]
    autocomplete_fields = ["article", "compte_achat", "code_analytique"]


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
