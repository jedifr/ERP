from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.forms.models import BaseInlineFormSet
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from comptes.lots import ChampLot
from comptes.lots_admin import ModificationParLotsMixin
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import action

from comptes.exports import ExportCsvMixin
from comptes.montants import ZERO, D0, arrondir, pourcent
from .models import (
    ArticleCompteAchat,
    ArticleCompteVente,
    CodeAnalytique,
    CompteComptable,
    EcritureComptable,
    JournalComptable,
    LigneEcriture,
    ParametresComptables,
    ParametresExportComptable,
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
class ArticleCompteVenteAdmin(ModificationParLotsMixin, ModelAdmin):
    list_display = ["article", "poste_gestion", "compte_vente", "code_analytique"]
    actions = ["action_modifier_par_lots"]
    champs_lot = [
        ChampLot("compte_vente", "Compte de vente", genre="fk", queryset=lambda: CompteComptable.objects.order_by("code"), vide_permis=True),
        ChampLot("poste_gestion", "Poste de gestion", genre="fk", queryset=lambda: PosteGestion.objects.order_by("code"), vide_permis=True),
        ChampLot("code_analytique", "Code analytique", genre="fk", queryset=lambda: CodeAnalytique.objects.order_by("code"), vide_permis=True),
    ]
    search_fields = ["article__reference", "article__libelle", "compte_vente__code", "poste_gestion__code"]
    autocomplete_fields = ["article", "poste_gestion", "compte_vente", "code_analytique"]


@admin.register(ArticleCompteAchat)
class ArticleCompteAchatAdmin(ModificationParLotsMixin, ModelAdmin):
    list_display = ["article", "poste_gestion", "compte_achat", "code_analytique"]
    actions = ["action_modifier_par_lots"]
    champs_lot = [
        ChampLot("compte_achat", "Compte d'achat", genre="fk", queryset=lambda: CompteComptable.objects.order_by("code"), vide_permis=True),
        ChampLot("poste_gestion", "Poste de gestion", genre="fk", queryset=lambda: PosteGestion.objects.order_by("code"), vide_permis=True),
        ChampLot("code_analytique", "Code analytique", genre="fk", queryset=lambda: CodeAnalytique.objects.order_by("code"), vide_permis=True),
    ]
    search_fields = ["article__reference", "article__libelle", "compte_achat__code", "poste_gestion__code"]
    autocomplete_fields = ["article", "poste_gestion", "compte_achat", "code_analytique"]


@admin.register(TiersCompteComptable)
class TiersCompteComptableAdmin(ModificationParLotsMixin, ModelAdmin):
    list_display = ["tiers", "compte_client", "compte_fournisseur"]
    actions = ["action_modifier_par_lots"]
    champs_lot = [
        ChampLot("compte_client", "Compte client", genre="fk", queryset=lambda: CompteComptable.objects.order_by("code"), vide_permis=True),
        ChampLot("compte_fournisseur", "Compte fournisseur", genre="fk", queryset=lambda: CompteComptable.objects.order_by("code"), vide_permis=True),
    ]
    search_fields = ["tiers__code", "tiers__raison_sociale", "compte_client__code", "compte_fournisseur__code"]
    autocomplete_fields = ["tiers", "compte_client", "compte_fournisseur"]


@admin.register(CodeAnalytique)
class CodeAnalytiqueAdmin(ModelAdmin):
    list_display = ["code", "libelle", "actif"]
    list_filter = ["actif"]
    search_fields = ["code", "libelle"]


@admin.register(ParametresComptables)
class ParametresComptablesAdmin(ModelAdmin):
    fieldsets = [
        (
            "Ventes",
            {"fields": ["journal_ventes", "compte_client_defaut", "compte_vente_defaut", "compte_tva_collectee_defaut"]},
        ),
        (
            "Achats",
            {
                "fields": [
                    "journal_achats",
                    "compte_fournisseur_defaut",
                    "compte_achat_defaut",
                    "compte_tva_deductible_defaut",
                    "compte_tva_autoliquidation_deductible",
                    "compte_tva_autoliquidation_due",
                ]
            },
        ),
    ]
    autocomplete_fields = [
        "compte_tva_autoliquidation_deductible",
        "compte_tva_autoliquidation_due",
        "journal_ventes",
        "compte_client_defaut",
        "compte_vente_defaut",
        "compte_tva_collectee_defaut",
        "journal_achats",
        "compte_fournisseur_defaut",
        "compte_achat_defaut",
        "compte_tva_deductible_defaut",
    ]

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
        total_debit = total_credit = ZERO
        for form in self.forms:
            if not form.cleaned_data or form.cleaned_data.get("DELETE"):
                continue
            total_debit += D0(form.cleaned_data.get("debit"))
            total_credit += D0(form.cleaned_data.get("credit"))
        if arrondir(total_debit - total_credit) != 0:
            raise ValidationError(
                f"L'écriture n'est pas équilibrée : débit {pourcent(total_debit)} ≠ crédit {pourcent(total_credit)}."
            )


class LigneEcritureInline(TabularInline):
    model = LigneEcriture
    formset = LigneEcritureFormSet
    extra = 2
    autocomplete_fields = ["compte", "code_analytique"]


@admin.register(ParametresExportComptable)
class ParametresExportComptableAdmin(ModelAdmin):
    """Journaux, compte de banque et libellés de l'export vers le comptable (une seule ligne)."""

    fieldsets = [
        ("Journaux et banque", {"fields": ["code_journal_ventes", "code_journal_achats", "code_journal_banque", "compte_banque"]}),
        ("Libellés des écritures", {"fields": ["libelle_ventes", "libelle_achats", "libelle_banque", "piece_achats"]}),
    ]

    def has_add_permission(self, request):
        return not ParametresExportComptable.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        ParametresExportComptable.charger()
        return redirect("admin:comptabilite_parametresexportcomptable_change", 1)


@admin.register(EcritureComptable)
class EcritureComptableAdmin(ExportCsvMixin, ModelAdmin):
    actions_list = ["action_exporter_comptable"]

    @action(description="Exporter vers le comptable (ISACOMPTA)", url_path="export-comptable", icon="outbox")
    def action_exporter_comptable(self, request):
        import datetime

        from . import export_comptable

        if not request.user.has_perm("comptabilite.view_ecriturecomptable"):
            raise PermissionDenied
        retour = reverse("admin:comptabilite_ecriturecomptable_changelist")
        donnees = request.POST if request.method == "POST" else request.GET
        aujourdhui = datetime.date.today()
        precedent = (aujourdhui.replace(day=1) - datetime.timedelta(days=1))
        brut = donnees.get("mois") or f"{precedent:%Y-%m}"
        try:
            annee, mois = (int(x) for x in brut.split("-"))
            debut, fin = export_comptable.bornes_du_mois(annee, mois)
        except ValueError:
            self.message_user(request, "Choisissez un mois (AAAA-MM).", level=messages.ERROR)
            return redirect(request.path)
        contexte = {**self.admin_site.each_context(request), "title": "Exporter vers le comptable", "retour": retour, "mois": brut,
                    "lien_parametres": reverse("admin:comptabilite_parametresexportcomptable_changelist")}
        if request.method == "POST" or donnees.get("mois"):
            try:
                resultat = export_comptable.exporter(debut, fin)
            except export_comptable.ErreurExport as exc:
                self.message_user(request, str(exc), level=messages.ERROR)
                return TemplateResponse(request, "admin/comptabilite/export_comptable.html", contexte)
            if request.method == "POST":
                reponse = HttpResponse(export_comptable.archive_zip(resultat, debut), content_type="application/zip")
                reponse["Content-Disposition"] = f'attachment; filename="export-comptable-{debut:%m-%Y}.zip"'
                return reponse
            contexte["apercu"] = [
                {"fichier": export_comptable.nom_fichier(nom, debut), "nombre": n} for nom, n in resultat["comptes"].items()
            ]
        return TemplateResponse(request, "admin/comptabilite/export_comptable.html", contexte)

    list_display = ["piece", "journal", "date_ecriture", "libelle", "total_debit", "total_credit", "est_equilibree"]
    list_filter = ["journal"]
    search_fields = ["piece", "libelle", "facture__numero", "facture_fournisseur__numero"]
    autocomplete_fields = ["journal", "facture", "facture_fournisseur"]
    date_hierarchy = "date_ecriture"
    inlines = [LigneEcritureInline]

    @admin.display(description="Équilibrée", boolean=True)
    def est_equilibree(self, obj):
        return obj.est_equilibree
