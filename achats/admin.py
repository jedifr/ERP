from django.conf import settings
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.html import escape, format_html
from django.utils.safestring import mark_safe
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import action as unfold_action

from codification.mixins import CodificationInitialeMixin
from codification.models import RegleCodification

from comptes.exports import ExportCsvMixin
from comptes.montants import arrondir, somme
from .generation import GenerationEcritureAchatError, generer_ecriture_achat
from .models import (
    AchatsError,
    ArticleFournisseur,
    CommandeFournisseur,
    FactureFournisseur,
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
    list_display = ["article_fournisseur", "prix_unitaire", "frais_port", "date_debut", "date_fin"]
    list_filter = ["article_fournisseur__fournisseur"]
    search_fields = ["article_fournisseur__article__reference", "article_fournisseur__fournisseur__raison_sociale"]
    autocomplete_fields = ["article_fournisseur"]


class LigneCommandeFournisseurInline(TabularInline):
    model = LigneCommandeFournisseur
    extra = 1
    autocomplete_fields = ["article", "poste_gestion", "taux_tva", "alerte_stock_origine", "commande_ligne_client"]
    readonly_fields = ["quantite_recue"]


@admin.register(CommandeFournisseur)
class CommandeFournisseurAdmin(ExportCsvMixin, CodificationInitialeMixin, ModelAdmin):
    codification_entite = RegleCodification.Entite.COMMANDE_FOURNISSEUR

    list_display = ["numero", "fournisseur", "date_commande", "date_livraison_prevue", "statut"]
    list_filter = ["statut"]
    search_fields = ["numero", "fournisseur__raison_sociale"]
    autocomplete_fields = ["fournisseur"]
    inlines = [LigneCommandeFournisseurInline]
    actions_detail = ["action_pdf"]
    readonly_fields = [
        "fournisseur_recap", "montant_total_ht_display", "montant_total_ttc_display",
        "reste_a_recevoir_recap", "receptions_recap", "factures_recap",
    ]

    # Fiche en deux colonnes : saisie à gauche, récapitulatif (fournisseur, totaux, reste à recevoir, réceptions,
    # factures) à droite — voir comptes/static/comptes/fiche_deux_colonnes.css.
    CHAMPS_RECAPITULATIF = [
        "fournisseur_recap", "montant_total_ht_display", "montant_total_ttc_display",
        "reste_a_recevoir_recap", "receptions_recap", "factures_recap",
    ]

    def get_fieldsets(self, request, obj=None):
        saisie = [c for c in self.get_fields(request, obj) if c not in self.CHAMPS_RECAPITULATIF]
        return [
            (None, {"fields": saisie, "classes": ["fiche-saisie"]}),
            ("Récapitulatif", {"fields": self.CHAMPS_RECAPITULATIF, "classes": ["fiche-recap"]}),
        ]

    @unfold_action(description="Bon de commande (PDF)", url_path="pdf")
    def action_pdf(self, request, object_id):
        from chiffrage.documents import DocumentError

        from .documents import generer_pdf_commande_fournisseur

        commande = get_object_or_404(CommandeFournisseur, pk=object_id)
        if not self.has_view_permission(request, commande):
            raise PermissionDenied
        try:
            contenu = generer_pdf_commande_fournisseur(commande)
        except DocumentError as exc:
            self.message_user(request, str(exc), level=messages.ERROR)
            return HttpResponseRedirect(reverse("admin:achats_commandefournisseur_change", args=[commande.pk]))
        reponse = HttpResponse(contenu, content_type="application/pdf")
        reponse["Content-Disposition"] = f'inline; filename="bon-commande-{commande.pk}.pdf"'
        return reponse

    @staticmethod
    def _euros(valeur):
        return f"{arrondir(valeur):,.2f} €".replace(",", " ").replace(".", ",")

    @staticmethod
    def _liens(objets, nom_url, texte):
        liens = [format_html("<a href='{}'>{}</a>", reverse(nom_url, args=[o.pk]), texte(o)) for o in objets]
        return mark_safe("<br>".join(liens)) if liens else "—"

    @admin.display(description="Fournisseur")
    def fournisseur_recap(self, obj):
        if obj is None or not obj.pk or not obj.fournisseur_id:
            return "—"
        adresse = obj.fournisseur.adresses.order_by("-est_principale", "id").first()
        lignes = [format_html("<b>{}</b>", obj.fournisseur.raison_sociale)]
        if adresse:
            lignes += [adresse.adresse, f"{adresse.code_postal} {adresse.ville}".strip()]
        return mark_safe("<br>".join(str(l) if hasattr(l, "__html__") else escape(l) for l in lignes if l))

    @admin.display(description="Total HT")
    def montant_total_ht_display(self, obj):
        if obj is None or not obj.pk:
            return "—"
        return self._euros(somme(l.montant_ht for l in obj.lignes.all()))

    @admin.display(description="Total TTC")
    def montant_total_ttc_display(self, obj):
        if obj is None or not obj.pk:
            return "—"
        return self._euros(somme(l.montant_ttc for l in obj.lignes.all()))

    @admin.display(description="Reste à recevoir")
    def reste_a_recevoir_recap(self, obj):
        if obj is None or not obj.pk:
            return "—"
        lignes = list(obj.lignes.select_related("article", "poste_gestion"))
        if not lignes:
            return "Aucune ligne"
        restantes = [(l, l.quantite_commandee - (l.quantite_recue or 0)) for l in lignes]
        restantes = [(l, reste) for l, reste in restantes if reste > 0]
        if not restantes:
            return "Tout est reçu"
        return mark_safe("<br>".join(
            format_html("{} : {} sur {}", str(l.article or l.poste_gestion or l.designation or "?"), f"{reste:g}", f"{l.quantite_commandee:g}")
            for l, reste in restantes
        ))

    @admin.display(description="Réceptions")
    def receptions_recap(self, obj):
        if obj is None or not obj.pk:
            return "—"
        return self._liens(obj.receptions.order_by("date_reception", "numero"), "admin:achats_reception_change",
                           lambda r: f"{r.numero} — {r.date_reception:%d/%m/%Y}")

    @admin.display(description="Factures fournisseur")
    def factures_recap(self, obj):
        if obj is None or not obj.pk:
            return "—"
        factures = list(obj.factures.order_by("date_facture", "numero"))
        if not factures:
            return "—"
        total = somme(f.montant_ht for f in factures)
        liens = self._liens(
            factures, "admin:achats_facturefournisseur_change",
            lambda f: f"{f.numero} — {f.date_facture:%d/%m/%Y}" + (f" — {self._euros(f.montant_ht)} HT" if f.montant_ht is not None else ""),
        )
        commande_ht = somme(l.montant_ht for l in obj.lignes.all())
        return mark_safe(f"{liens}<br>" + escape(f"Facturé : {self._euros(total)} HT sur {self._euros(commande_ht)} commandés"))


@admin.register(LigneCommandeFournisseur)
class LigneCommandeFournisseurAdmin(ExportCsvMixin, ModelAdmin):
    list_display = [
        "commande_fournisseur",
        "article",
        "poste_gestion",
        "quantite_commandee",
        "quantite_recue",
        "prix_unitaire_achat",
        "taux_tva",
        "commande_ligne_client",
    ]
    search_fields = ["commande_fournisseur__numero", "article__reference", "poste_gestion__code", "designation"]
    autocomplete_fields = [
        "commande_fournisseur",
        "article",
        "poste_gestion",
        "taux_tva",
        "alerte_stock_origine",
        "commande_ligne_client",
    ]
    readonly_fields = ["quantite_recue"]


class ReceptionLigneInline(TabularInline):
    model = ReceptionLigne
    extra = 1
    autocomplete_fields = ["ligne_commande_fournisseur", "lot", "emplacement"]

    def get_exclude(self, request, obj=None):
        # Sans gestion de stock : une réception ne note que la quantité reçue (ni lot, ni coulée, ni certificat).
        sans_stock = ["lot", "emplacement", "numero_coulee", "certificat"]
        return sans_stock if not settings.STOCK_ACTIF else super().get_exclude(request, obj)


@admin.register(Reception)
class ReceptionAdmin(ExportCsvMixin, CodificationInitialeMixin, ModelAdmin):
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


@admin.register(FactureFournisseur)
class FactureFournisseurAdmin(ExportCsvMixin, CodificationInitialeMixin, ModelAdmin):
    codification_entite = RegleCodification.Entite.FACTURE_FOURNISSEUR

    list_display = [
        "numero",
        "commande_fournisseur",
        "reference_fournisseur",
        "date_facture",
        "montant_ht",
        "montant_ttc",
        "statut_paiement",
        "date_paiement",
    ]
    list_filter = ["statut_paiement", "autoliquidation"]
    search_fields = ["numero", "reference_fournisseur", "commande_fournisseur__numero"]
    autocomplete_fields = ["commande_fournisseur"]
    actions = ["action_generer_ecriture"]

    @admin.action(description="Générer l'écriture comptable")
    def action_generer_ecriture(self, request, queryset):
        creees = existantes = 0
        for facture in queryset:
            try:
                _, creee = generer_ecriture_achat(facture)
            except GenerationEcritureAchatError as exc:
                self.message_user(request, f"{facture} : {exc}", level=messages.ERROR)
                continue
            creees += creee
            existantes += not creee
        if creees:
            self.message_user(request, f"{creees} écriture(s) comptable(s) générée(s).", level=messages.SUCCESS)
        if existantes:
            self.message_user(
                request, f"{existantes} facture(s) fournisseur avaient déjà leur écriture.", level=messages.INFO
            )
