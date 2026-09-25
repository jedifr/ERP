from django.contrib import admin
from django.urls import path
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from unfold.admin import ModelAdmin, TabularInline

from .admin_views import analyser_fichier_view
from .models import (
    ImbricationJob,
    ImbricationLigne,
    ImbricationPlacement,
    PieceDecoupe,
    ProfilImportDecoupe,
    RegleProfilImportDecoupe,
)
from .services.apercu_svg import generer_svg_feuille, generer_svg_piece


class ImbricationLigneInline(TabularInline):
    model = ImbricationLigne
    extra = 1
    autocomplete_fields = ["piece"]


class RegleProfilImportDecoupeInline(TabularInline):
    model = RegleProfilImportDecoupe
    extra = 1


@admin.register(ProfilImportDecoupe)
class ProfilImportDecoupeAdmin(ModelAdmin):
    list_display = ["nom", "description"]
    search_fields = ["nom"]
    inlines = [RegleProfilImportDecoupeInline]


@admin.register(PieceDecoupe)
class PieceDecoupeAdmin(ModelAdmin):
    list_display = [
        "nom",
        "matiere",
        "epaisseur",
        "format_source",
        "statut",
        "largeur_mm",
        "hauteur_mm",
        "surface_mm2",
        "nb_contours_interieurs",
        "date_import",
    ]
    list_filter = ["statut", "format_source", "pas_rotation_deg", "symetrie_autorisee", "a_gravure", "profil_import"]
    search_fields = ["nom"]
    autocomplete_fields = ["article", "matiere", "profil_import"]
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
        "calques_detectes",
        "a_gravure",
        "gravure_json",
        "pliage_json",
        "longueur_gravure_mm",
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
        "calques_detectes_display",
        "a_gravure_display",
        "longueur_gravure_mm_display",
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
                (obj.gravure_json or {}).get("traits") or [],
                (obj.pliage_json or {}).get("traits") or [],
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

    @admin.display(description="Calques détectés")
    def calques_detectes_display(self, obj):
        valeurs = (obj and obj.calques_detectes) or []
        return format_html('<span id="decoupe-calques">{}</span>', ", ".join(valeurs) if valeurs else "-")

    @admin.display(description="Gravure détectée")
    def a_gravure_display(self, obj):
        texte = "Oui" if obj and obj.a_gravure else "Non"
        return format_html('<span id="decoupe-a-gravure">{}</span>', texte)

    @admin.display(description="Longueur gravure mm")
    def longueur_gravure_mm_display(self, obj):
        valeur = obj.longueur_gravure_mm if obj and obj.longueur_gravure_mm else "-"
        return format_html('<span id="decoupe-longueur-gravure">{}</span>', valeur)

    @admin.action(description="Réimporter la géométrie depuis le fichier source")
    def reimporter(self, request, queryset):
        for piece in queryset:
            piece.importer_geometrie()
        self.message_user(request, f"{queryset.count()} pièce(s) réimportée(s).")

    def save_model(self, request, obj, form, change):
        fichier_modifie = "fichier_source" in form.changed_data
        profil_modifie = "profil_import" in form.changed_data
        super().save_model(request, obj, form, change)
        if fichier_modifie or profil_modifie:
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
        "apercu_imbrication",
        "nb_feuilles",
        "surface_pieces_mm2",
        "surface_feuilles_mm2",
        "taux_utilisation_pct",
        "cout_matiere_estime",
        "pieces_non_placees",
        "date_calcul",
    ]
    actions = ["recalculer"]

    @admin.display(description="Aperçu des feuilles")
    def apercu_imbrication(self, obj):
        if not obj or not obj.pk or not obj.nb_feuilles:
            return format_html(
                '<div id="decoupe-apercu-imbrication">{}</div>',
                "Aucune feuille calculée pour l'instant.",
            )
        placements = obj.placements.select_related("piece").order_by("numero_feuille", "y_mm", "x_mm")
        par_feuille = {}
        for placement in placements:
            par_feuille.setdefault(placement.numero_feuille, []).append(
                (placement.piece, placement.x_mm, placement.y_mm, placement.rotation_deg, placement.miroir)
            )

        blocs = []
        for numero in sorted(par_feuille):
            svg = generer_svg_feuille(obj.largeur_feuille_mm, obj.longueur_feuille_mm, par_feuille[numero])
            blocs.append(
                format_html(
                    '<div style="margin-bottom: 1rem;">'
                    '<div style="font-weight: 600; margin-bottom: 0.25rem;">Feuille {}</div>{}'
                    "</div>",
                    numero,
                    mark_safe(svg) if svg else "—",
                )
            )
        # Les SVG générés portent des attributs width/height en "mm" (utiles pour un export
        # imprimable via l'API) — beaucoup trop grands tels quels sur une page admin (une feuille
        # 1000×1000mm s'afficherait à ~3780px). On force ici une taille d'écran raisonnable.
        style = (
            "<style>#decoupe-apercu-imbrication svg "
            "{ width: 100%; max-width: 480px; height: auto; display: block; }</style>"
        )
        return format_html(
            '<div id="decoupe-apercu-imbrication">{}{}</div>', mark_safe(style), mark_safe("".join(blocs))
        )

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
    list_display = ["job", "piece", "numero_feuille", "x_mm", "y_mm", "rotation_deg", "miroir"]
    list_filter = ["numero_feuille", "miroir"]
