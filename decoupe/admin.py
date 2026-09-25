from django.contrib import admin
from django.urls import path
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from unfold.admin import ModelAdmin, TabularInline

from .admin_views import analyser_fichier_view
from .models import ImbricationJob, ImbricationLigne, ImbricationPlacement, PieceDecoupe
from .services.apercu_svg import generer_svg_piece


class ImbricationLigneInline(TabularInline):
    model = ImbricationLigne
    extra = 1
    autocomplete_fields = ["piece"]


@admin.register(PieceDecoupe)
class PieceDecoupeAdmin(ModelAdmin):
    list_display = [
        "nom",
        "format_source",
        "statut",
        "largeur_mm",
        "hauteur_mm",
        "surface_mm2",
        "nb_contours_interieurs",
        "date_import",
    ]
    list_filter = ["statut", "format_source", "rotation_autorisee"]
    search_fields = ["nom"]
    autocomplete_fields = ["article"]
    # Champs bruts totalement exclus du formulaire (jamais éditables ni affichés tels quels) :
    # remplacés ci-dessous par des méthodes readonly `*_display`, seules à apparaître, pour
    # disposer d'un <span id="..."> stable — voir le commentaire sur `apercu_piece`.
    exclude = [
        "format_source",
        "statut",
        "message_erreur",
        "avertissements",
        "surface_mm2",
        "perimetre_decoupe_mm",
        "largeur_mm",
        "hauteur_mm",
        "nb_contours_interieurs",
    ]
    readonly_fields = [
        "apercu_piece",
        "format_source_display",
        "statut_display",
        "message_erreur_display",
        "avertissements_display",
        "surface_mm2_display",
        "perimetre_decoupe_mm_display",
        "largeur_mm_display",
        "hauteur_mm_display",
        "nb_contours_interieurs_display",
        "contour_json",
        "date_import",
    ]
    actions = ["reimporter"]

    class Media:
        js = ["decoupe/piecedecoupe_admin.js"]

    # Wrappés dans un <span id="..."> (plutôt que les champs readonly bruts) : Unfold ne pose
    # pas de classe `field-<nom>` sur les champs readonly de premier niveau (seulement sur ses
    # tableaux inline) — même contrainte, même solution que sur DevisAdmin (voir son commentaire).
    # Ça donne un point d'accroche stable au JS d'analyse en direct, avant tout enregistrement.
    @admin.display(description="Aperçu")
    def apercu_piece(self, obj):
        svg = None
        if obj and obj.contour_json:
            svg = generer_svg_piece(
                obj.largeur_mm or 0,
                obj.hauteur_mm or 0,
                (obj.contour_json or {}).get("exterieur") or [],
                (obj.contour_json or {}).get("trous") or [],
            )
        return format_html(
            '<div id="decoupe-apercu">{}</div>',
            mark_safe(svg) if svg else "En attente d'import du fichier source.",
        )

    @admin.display(description="Format source")
    def format_source_display(self, obj):
        texte = obj.get_format_source_display() if obj and obj.format_source else "-"
        return format_html('<span id="decoupe-format-source">{}</span>', texte)

    @admin.display(description="Statut")
    def statut_display(self, obj):
        texte = obj.get_statut_display() if obj else "-"
        return format_html('<span id="decoupe-statut">{}</span>', texte)

    @admin.display(description="Message erreur")
    def message_erreur_display(self, obj):
        return format_html('<span id="decoupe-message-erreur">{}</span>', (obj and obj.message_erreur) or "-")

    @admin.display(description="Avertissements")
    def avertissements_display(self, obj):
        valeurs = (obj and obj.avertissements) or []
        return format_html('<span id="decoupe-avertissements">{}</span>', " ".join(valeurs) if valeurs else "-")

    @admin.display(description="Surface mm2")
    def surface_mm2_display(self, obj):
        valeur = obj.surface_mm2 if obj and obj.surface_mm2 is not None else "-"
        return format_html('<span id="decoupe-surface-mm2">{}</span>', valeur)

    @admin.display(description="Perimetre decoupe mm")
    def perimetre_decoupe_mm_display(self, obj):
        valeur = obj.perimetre_decoupe_mm if obj and obj.perimetre_decoupe_mm is not None else "-"
        return format_html('<span id="decoupe-perimetre-mm">{}</span>', valeur)

    @admin.display(description="Largeur mm")
    def largeur_mm_display(self, obj):
        valeur = obj.largeur_mm if obj and obj.largeur_mm is not None else "-"
        return format_html('<span id="decoupe-largeur-mm">{}</span>', valeur)

    @admin.display(description="Hauteur mm")
    def hauteur_mm_display(self, obj):
        valeur = obj.hauteur_mm if obj and obj.hauteur_mm is not None else "-"
        return format_html('<span id="decoupe-hauteur-mm">{}</span>', valeur)

    @admin.display(description="Nb contours interieurs")
    def nb_contours_interieurs_display(self, obj):
        valeur = obj.nb_contours_interieurs if obj else 0
        return format_html('<span id="decoupe-nb-contours">{}</span>', valeur)

    @admin.action(description="Réimporter la géométrie depuis le fichier source")
    def reimporter(self, request, queryset):
        for piece in queryset:
            piece.importer_geometrie()
        self.message_user(request, f"{queryset.count()} pièce(s) réimportée(s).")

    def save_model(self, request, obj, form, change):
        fichier_modifie = "fichier_source" in form.changed_data
        super().save_model(request, obj, form, change)
        if fichier_modifie:
            obj.importer_geometrie()

    def get_urls(self):
        urls = [
            path(
                "analyser/",
                self.admin_site.admin_view(analyser_fichier_view),
                name="decoupe_piecedecoupe_analyser",
            ),
        ]
        return urls + super().get_urls()


@admin.register(ImbricationJob)
class ImbricationJobAdmin(ModelAdmin):
    list_display = [
        "id",
        "article_matiere",
        "largeur_feuille_mm",
        "longueur_feuille_mm",
        "nb_feuilles",
        "taux_utilisation_pct",
        "cout_matiere_estime",
        "date_calcul",
    ]
    autocomplete_fields = ["article_matiere"]
    inlines = [ImbricationLigneInline]
    readonly_fields = [
        "nb_feuilles",
        "surface_pieces_mm2",
        "surface_feuilles_mm2",
        "taux_utilisation_pct",
        "cout_matiere_estime",
        "pieces_non_placees",
        "date_calcul",
    ]
    actions = ["recalculer"]

    @admin.action(description="Recalculer l'imbrication")
    def recalculer(self, request, queryset):
        for job in queryset:
            job.calculer()
        self.message_user(request, f"{queryset.count()} imbrication(s) recalculée(s).")

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        form.instance.calculer()


@admin.register(ImbricationPlacement)
class ImbricationPlacementAdmin(ModelAdmin):
    list_display = ["job", "piece", "numero_feuille", "x_mm", "y_mm", "rotation_deg"]
    list_filter = ["numero_feuille"]
