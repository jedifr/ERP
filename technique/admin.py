from django.conf import settings
from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import get_object_or_404, redirect
from django.urls import path, reverse
from django.utils.html import escape, format_html
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_http_methods
from unfold.admin import ModelAdmin, TabularInline

from comptes.exports import ExportCsvMixin
from achats.models import ArticleFournisseur
from comptabilite.models import ArticleCompteAchat, ArticleCompteVente

from .models import Article, Gamme, Matiere, Nomenclature, PosteTravail, TarifPoste
from .services import DuplicationError, dupliquer_article


class NomenclatureInline(TabularInline):
    model = Nomenclature
    fk_name = "article_parent"
    extra = 1
    autocomplete_fields = ["article_composant"]


class GammeInline(TabularInline):
    model = Gamme
    extra = 1
    autocomplete_fields = ["poste"]


class ArticleFournisseurInline(TabularInline):
    # Ajout rapide des fournisseurs d'un article achetable depuis sa fiche ;
    # l'historique des tarifs (TarifAchatArticle), lui, se gère sur la fiche
    # ArticleFournisseur dédiée (achats/admin.py — même principe que
    # CommandeLigne, inlinée ET dotée de sa propre fiche).
    model = ArticleFournisseur
    extra = 0
    autocomplete_fields = ["fournisseur"]


class ArticleCompteVenteInline(TabularInline):
    # OneToOneField : Django limite automatiquement à un seul formulaire.
    # Surcharge, pour cet article, le compte de vente par défaut utilisé
    # par la génération des écritures comptables (comptabilite.generation).
    model = ArticleCompteVente
    extra = 1
    autocomplete_fields = ["poste_gestion", "compte_vente", "code_analytique"]


class ArticleCompteAchatInline(TabularInline):
    # Même principe côté achat — purement déclaratif pour l'instant, voir
    # ArticleCompteAchat (aucune génération d'écriture d'achat automatique
    # n'existe encore).
    model = ArticleCompteAchat
    extra = 1
    autocomplete_fields = ["poste_gestion", "compte_achat", "code_analytique"]


@admin.register(Matiere)
class MatiereAdmin(ModelAdmin):
    list_display = ["nom", "densite"]
    search_fields = ["nom"]


@staff_member_required
@require_http_methods(["POST"])
def dupliquer_article_view(request, reference):
    article = get_object_or_404(Article, pk=reference)
    try:
        copie = dupliquer_article(article)
    except DuplicationError as exc:
        messages.error(request, f"Duplication impossible : {exc}")
        return redirect("admin:technique_article_change", reference)

    messages.success(request, f"« {article} » dupliqué en « {copie} ». Vous pouvez modifier la copie.")
    return redirect("admin:technique_article_change", copie.pk)


@admin.register(Article)
class ArticleAdmin(ExportCsvMixin, ModelAdmin):
    list_display = [
        "reference",
        "libelle",
        "nature",
        "matiere",
        "unite_cout",
        "cout_unitaire",
        "gere_en_stock",
        "stock_mini",
    ]
    list_filter = ["nature", "unite_cout", "type_profil", "gere_en_stock"]
    search_fields = ["reference", "libelle"]
    autocomplete_fields = ["matiere"]
    inlines = [
        NomenclatureInline,
        GammeInline,
        ArticleFournisseurInline,
        ArticleCompteVenteInline,
        ArticleCompteAchatInline,
    ]

    class Media:
        js = ["technique/article_admin.js"]

    _CHAMPS_STOCK = ("gere_en_stock", "stock_mini", "quantite_reappro")

    # Fiche en deux colonnes : saisie à gauche, récapitulatif (calculé, en lecture seule) à droite.
    CHAMPS_RECAPITULATIF = [
        "composition_recap", "achat_recap", "stock_recap", "utilise_dans_recap", "activite_recap",
    ]
    readonly_fields = CHAMPS_RECAPITULATIF

    def get_fieldsets(self, request, obj=None):
        recap = [c for c in self.CHAMPS_RECAPITULATIF if settings.STOCK_ACTIF or c != "stock_recap"]
        saisie = [c for c in self.get_fields(request, obj) if c not in self.CHAMPS_RECAPITULATIF]
        return [
            (None, {"fields": saisie, "classes": ["fiche-saisie"]}),
            ("Récapitulatif", {"fields": recap, "classes": ["fiche-recap"]}),
        ]

    @staticmethod
    def _vide(obj):
        return obj is None or not obj.pk

    @admin.display(description="Composition")
    def composition_recap(self, obj):
        if self._vide(obj):
            return "—"
        if obj.nature != Article.Nature.FABRIQUE:
            return "Article acheté : pas de nomenclature ni de gamme"
        nb_composants = obj.composants.count()
        nb_etapes = obj.gamme_etapes.count()
        lignes = [f"{nb_composants} composant(s) en nomenclature", f"{nb_etapes} étape(s) de gamme"]
        if not nb_composants or not nb_etapes:
            lignes.append("À compléter avant de pouvoir chiffrer cet article")
        return mark_safe("<br>".join(escape(l) for l in lignes))

    @admin.display(description="Prix d'achat actuel")
    def achat_recap(self, obj):
        if self._vide(obj):
            return "—"
        if obj.nature == Article.Nature.FABRIQUE:
            return "Article fabriqué : coût recalculé à chaque devis"
        lignes = []
        for af in obj.fournisseurs.select_related("fournisseur"):
            tarif = af.tarif_actuel
            prix = f"{tarif.prix_unitaire:,.4f} €".replace(",", " ").replace(".", ",") if tarif else "pas de tarif en vigueur"
            lignes.append(f"{af.fournisseur.raison_sociale} : {prix}")
        if obj.cout_unitaire is not None:
            lignes.insert(0, "Coût retenu : " + f"{obj.cout_unitaire:,.4f} €".replace(",", " ").replace(".", ","))
        return mark_safe("<br>".join(escape(l) for l in lignes)) if lignes else "Aucun fournisseur renseigné"

    @admin.display(description="Stock")
    def stock_recap(self, obj):
        if self._vide(obj):
            return "—"
        from stock.models import AlerteStock, stock_actif_pour, stock_total

        if not stock_actif_pour(obj):
            return "Article non géré en stock"
        total = stock_total(obj)
        lignes = [f"En stock : {total:g}"]
        if obj.stock_mini is not None:
            lignes.append(f"Seuil d'alerte : {obj.stock_mini:g}")
        if AlerteStock.objects.filter(article=obj, statut=AlerteStock.Statut.ACTIVE).exists():
            lignes.append("⚠ Alerte de réapprovisionnement active")
        return mark_safe("<br>".join(escape(l) for l in lignes))

    @admin.display(description="Utilisé dans")
    def utilise_dans_recap(self, obj):
        if self._vide(obj):
            return "—"
        parents = list(obj.utilise_dans.select_related("article_parent").order_by("article_parent_id")[:10])
        if not parents:
            return "Aucune nomenclature"
        liens = [
            format_html(
                '<a href="{}">{}</a> (× {})',
                reverse("admin:technique_article_change", args=[n.article_parent_id]),
                n.article_parent,
                f"{n.quantite:g}",
            )
            for n in parents
        ]
        plus = obj.utilise_dans.count() - len(parents)
        if plus > 0:
            liens.append(escape(f"… et {plus} autre(s)"))
        return mark_safe("<br>".join(liens))

    @admin.display(description="Activité")
    def activite_recap(self, obj):
        if self._vide(obj):
            return "—"
        lignes = [
            f"{obj.devis_lignes.count()} ligne(s) de devis",
            f"{obj.lignes_commande.count()} ligne(s) de commande",
        ]
        if obj.nature == Article.Nature.FABRIQUE:
            lignes.append(f"{obj.ordres_fabrication.count()} ordre(s) de fabrication")
        return mark_safe("<br>".join(escape(l) for l in lignes))

    def get_exclude(self, request, obj=None):
        exclus = list(super().get_exclude(request, obj) or [])
        return exclus + list(self._CHAMPS_STOCK) if not settings.STOCK_ACTIF else exclus or None

    def get_list_display(self, request):
        colonnes = super().get_list_display(request)
        return colonnes if settings.STOCK_ACTIF else [c for c in colonnes if c not in self._CHAMPS_STOCK]

    def get_list_filter(self, request):
        filtres = super().get_list_filter(request)
        return filtres if settings.STOCK_ACTIF else [f for f in filtres if f not in self._CHAMPS_STOCK]

    def get_urls(self):
        urls = [
            path(
                "<str:reference>/dupliquer/",
                self.admin_site.admin_view(dupliquer_article_view),
                name="technique_article_dupliquer",
            ),
        ]
        return urls + super().get_urls()


@admin.register(PosteTravail)
class PosteTravailAdmin(ModelAdmin):
    list_display = ["nom", "type_operation", "mode_calcul", "nombre_machines", "taux_marge_defaut"]
    list_filter = ["mode_calcul"]
    search_fields = ["nom"]


@admin.register(TarifPoste)
class TarifPosteAdmin(ModelAdmin):
    list_display = ["poste", "cout_horaire", "date_debut", "date_fin"]
    list_filter = ["poste"]
    autocomplete_fields = ["poste"]


@admin.register(Nomenclature)
class NomenclatureAdmin(ModelAdmin):
    list_display = ["article_parent", "article_composant", "quantite", "longueur_mm", "largeur_mm"]
    search_fields = ["article_parent__reference", "article_composant__reference"]
    autocomplete_fields = ["article_parent", "article_composant"]


@admin.register(Gamme)
class GammeAdmin(ModelAdmin):
    list_display = ["article", "ordre", "poste", "date_debut", "date_fin"]
    list_filter = ["poste"]
    search_fields = ["article__reference"]
    autocomplete_fields = ["article", "poste"]
