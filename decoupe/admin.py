from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.urls import path
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from comptes.pastilles import EN_COURS, PROBLEME, TERMINE, PastillesMixin
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import action as unfold_action

from .admin_views import analyser_fichier_view, previsualiser_imbrication_view
from .models import (
    FormatTole,
    ImbricationJob,
    ImbricationLigne,
    ImbricationPlacement,
    NormeCote,
    ParametreCoupe,
    ProfileSection,
    ReglageProcede,
    PieceDecoupe,
    ProfilImportDecoupe,
    RegleProfilImportDecoupe,
    VitesseCoupe,
)
from .services.apercu_svg import generer_svg_feuille, generer_svg_piece
from .services.gamme import alimenter_gamme
from .services.matiere import ErreurMatiere, cout_matiere_imbrication
from .services.lua_materiaux import NOMS_FRANCAIS, ErreurLua, importer_materiaux, lire_materials_lua, resume
from .services.parametres import ErreurParametre, calculer_vitesses, dupliquer_vers_epaisseurs, format_compatible
from .services.temps import ErreurTemps, estimer_temps_decoupe


def _bloc_feuille_svg(numero, svg):
    """Bloc "Feuille N" + son aperçu SVG — partagé entre le rendu initial (fiche déjà
    enregistrée) et le format que le JS d'aperçu en direct reconstruit à l'identique côté
    client à partir de la réponse JSON de `previsualiser_imbrication_view`."""
    return format_html(
        '<div style="margin-bottom: 1rem;">'
        '<div style="font-weight: 600; margin-bottom: 0.25rem;">Feuille {}</div>{}'
        "</div>",
        numero,
        mark_safe(svg) if svg else "—",
    )


def _table_calques(calques, roles_effectifs):
    """Tableau éditable calque → rôle, une ligne par calque détecté dans le fichier source.
    `roles_effectifs` : `{calque en minuscules: rôle}` déjà résolu (manuel > profil > découpe
    par défaut). Même structure HTML (classes, attributs `data-calque`) que celle reconstruite
    côté client par piecedecoupe_admin.js après une (ré)analyse — un `<select>` par calque,
    stylé comme les autres champs du formulaire plutôt que le rendu brut d'un `<select>` HTML."""
    lignes = []
    for calque in calques:
        role_actuel = roles_effectifs.get(calque.lower(), RegleProfilImportDecoupe.Role.DECOUPE)
        options = "".join(
            format_html(
                '<option value="{}"{}>{}</option>',
                valeur,
                mark_safe(" selected") if valeur == role_actuel else "",
                libelle,
            )
            for valeur, libelle in RegleProfilImportDecoupe.Role.choices
        )
        lignes.append(
            format_html(
                '<tr><td style="padding: 4px 8px 4px 0;">{}</td>'
                '<td style="padding: 4px 0;">'
                '<select class="decoupe-calque-role" data-calque="{}" '
                'style="padding: 0.375rem; border-radius: 0.375rem; border: 1px solid #e5e7eb;">{}</select>'
                "</td></tr>",
                calque,
                calque,
                mark_safe(options),
            )
        )
    return format_html(
        '<table style="border-collapse: collapse;"><thead><tr>'
        '<th style="text-align:left; padding: 4px 8px 4px 0;">Calque</th>'
        '<th style="text-align:left; padding: 4px 0;">Rôle</th>'
        "</tr></thead><tbody>{}</tbody></table>",
        mark_safe("".join(lignes)),
    )


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
class PieceDecoupeAdmin(PastillesMixin, ModelAdmin):
    pastilles = {"statut": {"en_attente": EN_COURS, "ok": TERMINE, "erreur": PROBLEME}}
    list_display = [
        "nom",
        "matiere",
        "epaisseur",
        "procede",
        "format_source",
        "statut",
        "largeur_mm",
        "hauteur_mm",
        "surface_mm2",
        "nb_contours_interieurs",
        "date_import",
    ]
    list_filter = ["statut", "procede", "format_source", "pas_rotation_deg", "symetrie_autorisee", "a_gravure", "profil_import"]
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
        "calques_editables",
        "a_gravure_display",
        "longueur_gravure_mm_display",
        "temps_decoupe_display",
        "contour_json",
        "date_import",
    ]
    actions = ["reimporter"]
    actions_detail = ["action_simuler_imbrication", "action_alimenter_gamme"]

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "tole":  # une tôle est un article matière première
            from technique.models import Article

            kwargs["queryset"] = Article.objects.filter(nature=Article.Nature.MATIERE_PREMIERE)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    @unfold_action(description="Simuler l'imbrication et le coût matière", url_path="simuler-imbrication", icon="grid_view")
    def action_simuler_imbrication(self, request, object_id):
        """Compare des formats de tôle (feuilles, utilisation, surface consommée, coût matière) pour plusieurs quantités ;
        « Retenir » fixe la tôle et le format de la pièce et active le chiffrage de la matière par imbrication."""
        import re

        from technique.models import Article

        piece = get_object_or_404(PieceDecoupe, pk=object_id)
        if not self.has_view_permission(request, piece):
            raise PermissionDenied
        retour = reverse("admin:decoupe_piecedecoupe_change", args=[piece.pk])
        toles = Article.objects.filter(nature=Article.Nature.MATIERE_PREMIERE).order_by("reference")
        formats = list(FormatTole.objects.filter(actif=True))
        exclus = [f for f in formats if not format_compatible(f, piece.procede)[0]]
        formats = [f for f in formats if f not in exclus]  # la machine du procédé ne reçoit pas les tôles plus grandes
        donnees = request.POST if request.method == "POST" else request.GET
        contexte = {**self.admin_site.each_context(request), "title": f"Simuler l'imbrication : {piece}", "piece": piece, "retour": retour,
                    "toles": toles, "formats": formats, "formats_exclus": exclus}
        tole = toles.filter(pk=donnees.get("tole") or (piece.tole_id or "")).first()
        if piece.statut != piece.Statut.OK:
            self.message_user(request, "La géométrie de la pièce n'est pas encore importée.", level=messages.ERROR)
            return redirect(retour)
        try:
            quantites = [int(float(q)) for q in (donnees.get("quantites") or "1, 10, 50, 100").replace(";", ",").split(",") if q.strip()]
            taux = donnees.get("taux") if donnees.get("taux") not in (None, "") else piece.taux_chute_recuperable
            taux = max(0, min(100, float(str(taux).replace(",", "."))))
            if not quantites or min(quantites) <= 0 or len(quantites) > 6:
                raise ValueError
        except ValueError:
            self.message_user(request, "Saisissez 1 à 6 quantités entières positives, séparées par des virgules, et un taux entre 0 et 100.", level=messages.ERROR)
            return redirect(request.path)
        choisis = [f for f in formats if str(f.pk) in (donnees.getlist("formats") if request.method == "POST" or "formats" in request.GET else [str(x.pk) for x in formats])]
        contexte.update({"quantites": quantites, "taux": taux, "tole": tole, "choisis": [f.pk for f in choisis],
                         "quantites_texte": ", ".join(str(q) for q in quantites)})

        if request.method == "POST" and "retenir" in request.POST:
            if not self.has_change_permission(request, piece):
                raise PermissionDenied
            retenu = FormatTole.objects.filter(pk=request.POST["retenir"]).first()
            if tole is None or retenu is None:
                self.message_user(request, "Choisissez la tôle avant de retenir un format.", level=messages.ERROR)
            else:
                piece.tole, piece.format_tole, piece.taux_chute_recuperable, piece.imbrication_chiffrage = tole, retenu, taux, True
                piece.save(update_fields=["tole", "format_tole", "taux_chute_recuperable", "imbrication_chiffrage"])
                suite = "" if piece.article_id else " Liez la pièce à son article fabriqué pour que le devis en profite."
                self.message_user(request, f"{tole} en {retenu} retenu : la matière de {piece.article or 'la pièce'} est chiffrée par imbrication.{suite}", level=messages.SUCCESS)
                return redirect(retour)

        if tole is not None and (request.method == "POST" or "tole" in request.GET):
            lignes, apercus = [], []
            for f in choisis:
                cellules = []
                for q in quantites:
                    try:
                        c = cout_matiere_imbrication(piece, q, tole=tole, format_tole=f, taux_chute_recuperable=taux, avec_placements=(q == quantites[0]))
                    except ErreurMatiere as exc:
                        cellules.append({"erreur": str(exc)})
                        continue
                    cellules.append({"c": c})
                    if q == quantites[0]:
                        svg = generer_svg_feuille(
                            f.largeur_mm, f.longueur_mm,
                            [(piece, p.x_mm, p.y_mm, p.rotation_deg, False) for p in c.placements if p.numero_feuille == 1],
                        )
                        svg = re.sub(r'width="[^"]*mm" height="[^"]*mm"', 'style="width:100%;height:auto"', svg)
                        apercus.append({"format": f, "svg": mark_safe(svg), "quantite": q})
                lignes.append({"format": f, "cellules": cellules})
            # meilleur coût unitaire de chaque colonne
            for i, _q in enumerate(quantites):
                valeurs = [(l["cellules"][i]["c"].cout_unitaire, id(l)) for l in lignes if "c" in l["cellules"][i]]
                if valeurs:
                    mini = min(v for v, _ in valeurs)
                    for l in lignes:
                        if "c" in l["cellules"][i] and l["cellules"][i]["c"].cout_unitaire == mini:
                            l["cellules"][i]["meilleur"] = True
            contexte.update({"lignes": lignes, "apercus": apercus, "peut_retenir": self.has_change_permission(request, piece)})
        return TemplateResponse(request, "admin/decoupe/simuler_imbrication.html", contexte)

    @admin.display(description="Temps de découpe estimé")
    def temps_decoupe_display(self, obj):
        if obj is None or not obj.pk or obj.statut != obj.Statut.OK:
            return "—"
        try:
            e = estimer_temps_decoupe(obj)
        except ErreurTemps as exc:
            return str(exc)
        texte = (
            f"{e.total_min:.1f} min par pièce — coupe {e.coupe_s / 60:.1f} min ({e.longueur_coupe_mm:.0f} mm, {e.nb_coins} coins), "
            f"perçage {e.percage_s / 60:.1f} min ({e.nb_percages} contours), déplacements {e.deplacements_s / 60:.1f} min, marquage {e.marquage_s / 60:.1f} min. "
            f"Paramètres : {e.parametre}."
        )
        return format_html("{}{}", texte, mark_safe("".join(format_html("<br><em>{}</em>", a) for a in e.avertissements)))

    @unfold_action(description="Alimenter la gamme de l'article (temps de découpe)", url_path="alimenter-gamme", icon="schedule")
    def action_alimenter_gamme(self, request, object_id):
        piece = get_object_or_404(PieceDecoupe, pk=object_id)
        retour = reverse("admin:decoupe_piecedecoupe_change", args=[piece.pk])
        if not (request.user.has_perm("technique.change_gamme") and request.user.has_perm("technique.add_gamme")):
            raise PermissionDenied
        try:
            etape, estimation = alimenter_gamme(piece)
        except ErreurTemps as exc:
            self.message_user(request, str(exc), level=messages.ERROR)
        else:
            self.message_user(
                request,
                format_html(
                    "Gamme de {} : étape {} ({}) = {} min par pièce.", piece.article, etape.ordre, etape.poste, f"{estimation.total_min:.2f}"
                ),
                level=messages.SUCCESS,
            )
        return redirect(retour)

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if db_field.name == "regles_calques_manuelles":
            kwargs["widget"] = forms.HiddenInput()
        return super().formfield_for_dbfield(db_field, request, **kwargs)

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

    # Cases à cocher/sélecteurs par calque, pas juste un affichage : le formulaire d'import
    # doit permettre de dire quels calques garder et lesquels sont de la découpe/gravure,
    # directement à l'écran — l'enregistrement écrit le résultat dans
    # `regles_calques_manuelles` (champ caché : voir formfield_for_dbfield et le <style>
    # ci-dessous qui masque sa ligne, class `field-regles_calques_manuelles` posée par Unfold
    # sur un champ de formulaire normal, contrairement aux champs readonly de premier niveau).
    @admin.display(description="Calques")
    def calques_editables(self, obj):
        calques = (obj and obj.calques_detectes) or []
        style = "<style>.field-regles_calques_manuelles { display: none; }</style>"
        if not calques:
            return format_html(
                '<div id="decoupe-calques-editables">{}{}</div>',
                mark_safe(style),
                "En attente d'import du fichier source.",
            )
        if obj.regles_calques_manuelles:
            roles_effectifs = {calque.lower(): role for calque, role in obj.regles_calques_manuelles.items()}
        elif obj.profil_import_id:
            roles_effectifs = obj.profil_import.regles_par_calque()
        else:
            roles_effectifs = {}
        return format_html(
            '<div id="decoupe-calques-editables">{}{}</div>',
            mark_safe(style),
            _table_calques(calques, roles_effectifs),
        )

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
        calques_modifies = "regles_calques_manuelles" in form.changed_data
        super().save_model(request, obj, form, change)
        if fichier_modifie or profil_modifie or calques_modifies:
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
    # Champs bruts exclus du formulaire, remplacés par des méthodes readonly `*_display` — même
    # raison et même solution que sur PieceDecoupeAdmin (voir son commentaire) : donner un
    # <span id="..."> stable au JS d'aperçu en direct, ce qu'Unfold ne fournit pas nativement
    # sur les champs readonly de premier niveau.
    exclude = [
        "nb_feuilles",
        "surface_pieces_mm2",
        "surface_feuilles_mm2",
        "taux_utilisation_pct",
        "cout_matiere_estime",
        "pieces_non_placees",
    ]
    readonly_fields = [
        "apercu_imbrication",
        "nb_feuilles_display",
        "surface_pieces_mm2_display",
        "surface_feuilles_mm2_display",
        "taux_utilisation_pct_display",
        "cout_matiere_estime_display",
        "pieces_non_placees_display",
        "date_calcul",
    ]
    actions = ["recalculer"]

    class Media:
        js = ["decoupe/imbricationjob_admin.js"]

    @admin.display(description="Aperçu des feuilles")
    def apercu_imbrication(self, obj):
        if not obj or not obj.pk or not obj.nb_feuilles:
            contenu = "Aucune feuille calculée pour l'instant."
        else:
            placements = obj.placements.select_related("piece").order_by("numero_feuille", "y_mm", "x_mm")
            par_feuille = {}
            for placement in placements:
                par_feuille.setdefault(placement.numero_feuille, []).append(
                    (placement.piece, placement.x_mm, placement.y_mm, placement.rotation_deg, placement.miroir)
                )
            contenu = mark_safe(
                "".join(
                    _bloc_feuille_svg(
                        numero, generer_svg_feuille(obj.largeur_feuille_mm, obj.longueur_feuille_mm, placements_feuille)
                    )
                    for numero, placements_feuille in sorted(par_feuille.items())
                )
            )
        # Les SVG générés portent des attributs width/height en "mm" (utiles pour un export
        # imprimable via l'API) — beaucoup trop grands tels quels sur une page admin (une feuille
        # 1000×1000mm s'afficherait à ~3780px). On force ici une taille d'écran raisonnable.
        style = (
            "<style>#decoupe-apercu-imbrication svg "
            "{ width: 100%; max-width: 480px; height: auto; display: block; }</style>"
        )
        return format_html('<div id="decoupe-apercu-imbrication">{}{}</div>', mark_safe(style), contenu)

    @admin.display(description="Nb feuilles")
    def nb_feuilles_display(self, obj):
        return format_html('<span id="decoupe-imb-nb-feuilles">{}</span>', (obj and obj.nb_feuilles) or "-")

    @admin.display(description="Surface pieces mm2")
    def surface_pieces_mm2_display(self, obj):
        valeur = obj.surface_pieces_mm2 if obj and obj.surface_pieces_mm2 is not None else "-"
        return format_html('<span id="decoupe-imb-surface-pieces">{}</span>', valeur)

    @admin.display(description="Surface feuilles mm2")
    def surface_feuilles_mm2_display(self, obj):
        valeur = obj.surface_feuilles_mm2 if obj and obj.surface_feuilles_mm2 is not None else "-"
        return format_html('<span id="decoupe-imb-surface-feuilles">{}</span>', valeur)

    @admin.display(description="Taux utilisation pct")
    def taux_utilisation_pct_display(self, obj):
        valeur = obj.taux_utilisation_pct if obj and obj.taux_utilisation_pct is not None else "-"
        return format_html('<span id="decoupe-imb-taux">{}</span>', valeur)

    @admin.display(description="Cout matiere estime")
    def cout_matiere_estime_display(self, obj):
        valeur = obj.cout_matiere_estime if obj and obj.cout_matiere_estime is not None else "-"
        return format_html('<span id="decoupe-imb-cout">{}</span>', valeur)

    @admin.display(description="Pieces non placees")
    def pieces_non_placees_display(self, obj):
        valeurs = (obj and obj.pieces_non_placees) or []
        return format_html('<span id="decoupe-imb-non-placees">{}</span>', ", ".join(map(str, valeurs)) or "-")

    @admin.action(description="Recalculer l'imbrication")
    def recalculer(self, request, queryset):
        for job in queryset:
            job.calculer()
        self.message_user(request, f"{queryset.count()} imbrication(s) recalculée(s).")

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        form.instance.calculer()

    def get_urls(self):
        urls = [
            path(
                "previsualiser/",
                self.admin_site.admin_view(previsualiser_imbrication_view),
                name="decoupe_imbricationjob_previsualiser",
            ),
        ]
        return urls + super().get_urls()


@admin.register(ImbricationPlacement)
class ImbricationPlacementAdmin(ModelAdmin):
    list_display = ["job", "piece", "numero_feuille", "x_mm", "y_mm", "rotation_deg", "miroir"]
    list_filter = ["numero_feuille", "miroir"]


class VitesseCoupeInline(TabularInline):
    model = VitesseCoupe
    extra = 0
    ordering = ["qualite"]


class SansPosteFilter(admin.SimpleListFilter):
    title = "poste de travail"
    parameter_name = "sans_poste"

    def lookups(self, request, model_admin):
        return [("oui", "Sans poste (chiffrés sans main-d'œuvre machine)")]

    def queryset(self, request, queryset):
        return queryset.filter(poste__isnull=True) if self.value() == "oui" else queryset


@admin.register(ParametreCoupe)
class ParametreCoupeAdmin(ModelAdmin):
    """Paramètres de coupe repris du logiciel de la machine (jet d'eau) : une fiche par famille de matière (ou par nuance
    précise, en exception) et épaisseur, avec une ligne de vitesses par niveau de qualité. Servent à estimer le temps
    de découpe des pièces."""

    list_display = ["cible_display", "epaisseur_mm", "procede", "gaz", "vitesse_production_display", "poste", "origine", "intervalle_pieces_mm", "coefficient_ajustement"]
    actions = ["action_calculer_vitesses", "action_affecter_poste"]
    actions_list = ["action_importer_lua"]
    actions_detail = ["action_dupliquer_epaisseurs"]

    @unfold_action(description="Importer materials.lua (IGEMS)", url_path="importer-lua", icon="upload_file")
    def action_importer_lua(self, request):
        """Import en masse du fichier materials.lua du logiciel de la machine : téléversement, contrôle de la
        correspondance des familles de matière, puis création des paramètres de coupe (toutes les matières et épaisseurs)."""
        from technique.models import FamilleMatiere, PosteTravail

        if not request.user.has_perm("decoupe.add_parametrecoupe"):
            raise PermissionDenied
        liste = reverse("admin:decoupe_parametrecoupe_changelist")
        contexte = {**self.admin_site.each_context(request), "title": "Importer materials.lua", "retour": liste,
                    "postes": PosteTravail.objects.order_by("nom"),
                    "familles": FamilleMatiere.objects.order_by("ordre", "nom")}
        if request.method == "POST" and request.FILES.get("fichier"):
            try:
                entrees = lire_materials_lua(request.FILES["fichier"].read().decode("utf-8", errors="replace"))
            except ErreurLua as exc:
                self.message_user(request, str(exc), level=messages.ERROR)
                return redirect(request.path)
            request.session["import_lua"] = entrees
            contexte["lignes"] = [
                {"nom": nom, "nombre": n, "usinabilite": u, "mini": mini, "maxi": maxi, "francais": NOMS_FRANCAIS.get(nom, nom)}
                for nom, n, u, mini, maxi in resume(entrees)
            ]
            return TemplateResponse(request, "admin/decoupe/importer_lua.html", contexte)
        if request.method == "POST" and "confirmer" in request.POST:
            entrees = request.session.pop("import_lua", None)
            if not entrees:
                self.message_user(request, "Le fichier n'est plus en mémoire : téléversez-le à nouveau.", level=messages.ERROR)
                return redirect(request.path)
            correspondance = {nom: request.POST.get(f"famille__{nom}", "") for nom in {e["nom"] for e in entrees}}
            poste = PosteTravail.objects.filter(pk=request.POST.get("poste")).first()
            stats = importer_materiaux(entrees, correspondance, poste=poste, remplacer_calcules="remplacer" in request.POST)
            self.message_user(
                request,
                f"Import terminé : {stats['crees']} paramètre(s) créé(s), {stats['mis_a_jour']} mis à jour, {stats['proteges']} relevé(s) "
                f"machine conservé(s), {stats['ignores']} ignoré(s) ; {stats['familles_creees']} famille(s) créée(s). "
                "Vitesses calculées (estimation calée sur vos temps réels).",
                level=messages.SUCCESS,
            )
            return redirect(liste)
        return TemplateResponse(request, "admin/decoupe/importer_lua.html", contexte)
    list_filter = [SansPosteFilter, "procede", "gaz", "origine", "famille", "poste"]
    search_fields = ["famille__nom", "matiere__nom"]
    autocomplete_fields = ["famille", "matiere", "poste"]
    inlines = [VitesseCoupeInline]
    readonly_fields = ["origine"]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("famille", "matiere", "poste")

    @admin.display(description="Vitesse de production")
    def vitesse_production_display(self, obj):
        if obj.procede == "laser" and obj.vitesse_coupe_production_m_min:
            return f"{obj.vitesse_coupe_production_m_min:g} m/min"
        return "—"

    @admin.display(description="Famille / nuance", ordering="famille__nom")
    def cible_display(self, obj):
        return f"{obj.matiere} (nuance)" if obj.matiere_id else obj.famille

    @admin.action(description="Affecter un poste de travail (machine de coupe)…")
    def action_affecter_poste(self, request, queryset):
        from technique.models import PosteTravail

        if "poste" in request.POST and "apply" in request.POST:
            poste = PosteTravail.objects.filter(pk=request.POST["poste"]).first()
            n = queryset.update(poste=poste)
            self.message_user(request, f"{n} paramètre(s) affecté(s) à « {poste} ».", level=messages.SUCCESS)
            return None
        return TemplateResponse(
            request, "admin/decoupe/affecter_poste.html",
            {**self.admin_site.each_context(request), "title": "Affecter un poste de travail", "queryset": queryset,
             "postes": PosteTravail.objects.order_by("nom"), "action": "action_affecter_poste", "opts": self.model._meta,
             "ids": request.POST.getlist("_selected_action")},
        )

    @admin.action(description="Calculer les vitesses depuis l'usinabilité (estimation)")
    def action_calculer_vitesses(self, request, queryset):
        for parametre in queryset:
            try:
                calculer_vitesses(parametre)
            except ErreurParametre as exc:
                self.message_user(request, str(exc), level=messages.ERROR)
            else:
                self.message_user(request, f"{parametre} : vitesses calculées (estimation).", level=messages.SUCCESS)

    @unfold_action(description="Dupliquer vers d'autres épaisseurs", url_path="dupliquer-epaisseurs", icon="content_copy")
    def action_dupliquer_epaisseurs(self, request, object_id):
        modele = get_object_or_404(ParametreCoupe, pk=object_id)
        if not request.user.has_perm("decoupe.add_parametrecoupe"):
            raise PermissionDenied
        retour = reverse("admin:decoupe_parametrecoupe_change", args=[modele.pk])
        if request.method == "POST":
            try:
                epaisseurs = [float(x.replace(",", ".")) for x in request.POST.get("epaisseurs", "").replace(";", ",").split(",") if x.strip()]
                if not epaisseurs:
                    raise ValueError
                crees, existants = dupliquer_vers_epaisseurs(modele, epaisseurs)
            except ValueError:
                self.message_user(request, "Saisissez des épaisseurs en millimètres, séparées par des virgules (ex. 3, 4, 5, 6).", level=messages.ERROR)
                return redirect(reverse("admin:decoupe_parametrecoupe_action_dupliquer_epaisseurs", args=[modele.pk]))
            except ErreurParametre as exc:
                self.message_user(request, str(exc), level=messages.ERROR)
                return redirect(retour)
            texte = f"{len(crees)} paramètre(s) créé(s) pour {modele.cible} (vitesses calculées, estimation à confirmer)."
            if existants:
                texte += " Déjà existants : " + ", ".join(f"{e:g}" for e in existants) + " mm."
            self.message_user(request, texte, level=messages.SUCCESS)
            return redirect(reverse("admin:decoupe_parametrecoupe_changelist"))
        return TemplateResponse(
            request, "admin/decoupe/dupliquer_parametre.html",
            {**self.admin_site.each_context(request), "title": f"Dupliquer {modele}", "modele": modele, "retour": retour,
             "suggestion": "2, 3, 4, 5, 6, 8, 12, 15, 20, 25, 30"},
        )
    def get_fieldsets(self, request, obj=None):
        """Chaque procédé n'affiche que ses réglages : le laser n'a ni usinabilité, ni qualités, ni modes de perçage du jet d'eau."""
        fieldsets = super().get_fieldsets(request, obj)
        if obj is None or obj.pk is None:
            return fieldsets
        laser = obj.procede == ParametreCoupe.Procede.LASER
        jet_seulement = {"usinabilite", "mode_percage", "percage_stationnaire_bp_s", "percage_circulaire_hp_tours", "percage_circulaire_bp_tours",
                         "diametre_percage_mm", "vitesse_marquage_mm_min", "temporisation_marquage_s", "percement_lineaire_mm", "chevauchement_mm",
                         "rayon_pleine_vitesse_coef", "facteur_vitesse_courbe", "seuil_angle_coin_deg"}
        resultat = []
        for titre, options in fieldsets:
            champs = list(options["fields"])
            if titre and titre.startswith("Laser") and not laser:
                continue
            if laser:
                champs = [c for c in champs if c not in jet_seulement]
            if champs:
                resultat.append((titre, {**options, "fields": champs}))
        return resultat

    fieldsets = [
        (None, {"fields": ["procede", "gaz", "famille", "matiere", "epaisseur_mm", "poste", "usinabilite", "origine"]}),
        ("Laser (tableau du constructeur)", {"fields": [
            "vitesse_coupe_production_m_min", "vitesse_coupe_max_m_min", "consommation_gaz_m3_h", "puissance_kw", "remarque",
        ]}),
        ("Perçage", {"fields": [
            "mode_percage", "percage_stationnaire_hp_s", "percage_stationnaire_bp_s", "percage_circulaire_hp_tours",
            "percage_circulaire_bp_tours", "diametre_percage_mm", "temporisation_pointage_s",
        ]}),
        ("Marquage, amorce et imbrication", {"fields": [
            "vitesse_marquage_mm_min", "temporisation_marquage_s", "percement_lineaire_mm", "chevauchement_mm", "intervalle_pieces_mm", "bord_tole_mm",
        ]}),
        ("Réglages du calcul", {"fields": [
            "rayon_pleine_vitesse_coef", "facteur_vitesse_courbe", "seuil_angle_coin_deg", "facteur_percage", "deplacement_par_contour_s",
            "coefficient_ajustement",
        ]}),
    ]


@admin.register(ReglageProcede)
class ReglageProcedeAdmin(ModelAdmin):
    """Pondération des vitesses du laser et écart minimal entre pièces de chaque procédé."""

    list_display = ["procede", "coefficient_vitesse", "espacement_minimum_mm", "bord_tole_minimum_mm"]

    def has_add_permission(self, request):
        return False  # un réglage par procédé, créé avec les valeurs usuelles

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(FormatTole)
class FormatToleAdmin(ModelAdmin):
    """Formats de tôle proposés à la simulation d'imbrication (3000 × 1500, 2500 × 1250…)."""

    list_display = ["libelle", "longueur_mm", "largeur_mm", "actif"]
    list_filter = ["actif"]
    search_fields = ["libelle"]


@admin.register(NormeCote)
class NormeCoteAdmin(ModelAdmin):
    """Cotes normalisées de la bibliothèque de formes du devis (brides EN 1092-1, rondelles ISO) : à contrôler avec la norme."""

    list_display = ["designation", "famille", "cotes", "verifie", "source"]
    list_filter = ["famille", "verifie"]
    search_fields = ["designation"]
    actions = ["marquer_verifie"]

    @admin.display(description="cotes")
    def cotes(self, obj):
        v = obj.valeurs
        if obj.famille == NormeCote.Famille.BRIDE_EN1092:
            return f"Ø{v.get('D')} · cercle Ø{v.get('K')} · {v.get('n')} × Ø{v.get('L')} · tube Ø{v.get('tube_od')}"
        return f"Ø int. {v.get('d1')} · Ø ext. {v.get('d2')} · ép. {v.get('h')}"

    @admin.action(description="Marquer comme vérifié avec la norme")
    def marquer_verifie(self, request, queryset):
        n = queryset.update(verifie=True)
        self.message_user(request, f"{n} ligne(s) marquée(s) comme vérifiée(s).", messages.SUCCESS)


@admin.register(ProfileSection)
class ProfileSectionAdmin(ModelAdmin):
    """Catalogue des profilés (cornières, UPN, tubes) de la bibliothèque de formes du devis, avec leur article d'achat."""

    list_display = ["designation", "famille", "masse_lineique", "longueur_barre_mm", "article", "verifie"]
    list_filter = ["famille", "verifie"]
    search_fields = ["designation"]
    autocomplete_fields = ["article"]
    actions = ["creer_articles", "marquer_verifie"]

    @admin.action(description="Créer les articles d'achat manquants (coût à renseigner)")
    def creer_articles(self, request, queryset):
        from technique.models import Article

        crees = 0
        for section in queryset.filter(article__isnull=True):
            reference = f"PROF-{section.designation}"[:100]
            article, cree = Article.objects.get_or_create(
                reference=reference,
                defaults={
                    "libelle": f"{section.get_famille_display()} {section.designation}", "nature": Article.Nature.MATIERE_PREMIERE,
                    "unite_cout": Article.UniteCout.LONGUEUR, "poids_lineique": section.masse_lineique,
                },
            )
            section.article = article
            section.save(update_fields=["article"])
            crees += cree
        self.message_user(
            request, f"{crees} article(s) créé(s) en « longueur » avec le poids linéique : saisissez leur coût (€/kg).", messages.SUCCESS,
        )

    @admin.action(description="Marquer comme vérifié")
    def marquer_verifie(self, request, queryset):
        self.message_user(request, f"{queryset.update(verifie=True)} section(s) marquée(s) comme vérifiée(s).", messages.SUCCESS)
