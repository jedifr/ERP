from django.contrib import admin

from .models import Article, Gamme, Matiere, Nomenclature, PosteTravail, TarifPoste


class NomenclatureInline(admin.TabularInline):
    model = Nomenclature
    fk_name = "article_parent"
    extra = 1
    autocomplete_fields = ["article_composant"]


class GammeInline(admin.TabularInline):
    model = Gamme
    extra = 1
    autocomplete_fields = ["poste"]


@admin.register(Matiere)
class MatiereAdmin(admin.ModelAdmin):
    list_display = ["nom", "densite"]
    search_fields = ["nom"]


@admin.register(Article)
class ArticleAdmin(admin.ModelAdmin):
    list_display = [
        "reference",
        "nature",
        "matiere",
        "unite_cout",
        "cout_unitaire",
        "gere_en_stock",
        "stock_mini",
    ]
    list_filter = ["nature", "unite_cout", "type_profil", "gere_en_stock"]
    search_fields = ["reference"]
    autocomplete_fields = ["matiere"]
    inlines = [NomenclatureInline, GammeInline]


@admin.register(PosteTravail)
class PosteTravailAdmin(admin.ModelAdmin):
    list_display = ["nom", "type_operation", "mode_calcul", "nombre_machines", "taux_marge_defaut"]
    list_filter = ["mode_calcul"]
    search_fields = ["nom"]


@admin.register(TarifPoste)
class TarifPosteAdmin(admin.ModelAdmin):
    list_display = ["poste", "cout_horaire", "date_debut", "date_fin"]
    list_filter = ["poste"]
    autocomplete_fields = ["poste"]


@admin.register(Nomenclature)
class NomenclatureAdmin(admin.ModelAdmin):
    list_display = ["article_parent", "article_composant", "quantite", "longueur_mm", "largeur_mm"]
    search_fields = ["article_parent__reference", "article_composant__reference"]
    autocomplete_fields = ["article_parent", "article_composant"]


@admin.register(Gamme)
class GammeAdmin(admin.ModelAdmin):
    list_display = ["article", "ordre", "poste", "date_debut", "date_fin"]
    list_filter = ["poste"]
    search_fields = ["article__reference"]
    autocomplete_fields = ["article", "poste"]
