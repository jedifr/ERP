from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import get_object_or_404, redirect
from django.urls import path
from django.views.decorators.http import require_http_methods
from unfold.admin import ModelAdmin, TabularInline

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
class ArticleAdmin(ModelAdmin):
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
    inlines = [NomenclatureInline, GammeInline]

    class Media:
        js = ["technique/article_admin.js"]

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
