import tempfile
from pathlib import Path

from django.contrib.admin.views.decorators import staff_member_required
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from .models import PieceDecoupe, ProfilImportDecoupe
from .services.apercu_svg import generer_svg_piece
from .services.geometrie import ErreurImportGeometrie, extraire_geometrie


@staff_member_required
@require_http_methods(["POST"])
def analyser_fichier_view(request):
    """Extrait la géométrie d'un fichier DXF/DWG tout juste sélectionné dans le formulaire,
    sans l'enregistrer — utilisé par le JS de la fiche PieceDecoupe pour afficher l'analyse et
    l'aperçu en direct, avant même de cliquer sur "Enregistrer". L'import réel (persisté) a
    toujours lieu à l'enregistrement, via `PieceDecoupe.importer_geometrie()` — cette vue ne
    fait qu'anticiper le même résultat pour l'affichage. Si un profil d'import est sélectionné
    dans le formulaire (`profil_import`), ses règles de calque sont appliquées pour séparer
    découpe / gravure / pliage, exactement comme à l'enregistrement."""
    fichier = request.FILES.get("fichier_source")
    if not fichier:
        return JsonResponse({"detail": "Aucun fichier reçu."}, status=400)

    extension = fichier.name.rsplit(".", 1)[-1].lower() if "." in fichier.name else ""
    if extension not in (PieceDecoupe.FormatSource.DXF, PieceDecoupe.FormatSource.DWG):
        return JsonResponse({"detail": "Seuls les fichiers .dxf et .dwg sont acceptés."}, status=400)

    regles_calques = None
    profil_id = request.POST.get("profil_import")
    if profil_id:
        profil = ProfilImportDecoupe.objects.filter(pk=profil_id).first()
        if profil:
            regles_calques = profil.regles_par_calque()

    with tempfile.TemporaryDirectory() as dossier:
        chemin = Path(dossier) / fichier.name
        with open(chemin, "wb") as cible:
            for morceau in fichier.chunks():
                cible.write(morceau)

        try:
            resultat = extraire_geometrie(str(chemin), extension, regles_calques=regles_calques)
        except ErreurImportGeometrie as exc:
            return JsonResponse(
                {
                    "ok": False,
                    "statut_display": PieceDecoupe.Statut.ERREUR.label,
                    "message_erreur": str(exc),
                    "format_source_display": PieceDecoupe.FormatSource(extension).label,
                }
            )

    svg = generer_svg_piece(
        resultat.largeur_mm, resultat.hauteur_mm, resultat.exterior, resultat.holes, resultat.gravure, resultat.pliage
    )
    return JsonResponse(
        {
            "ok": True,
            "statut_display": PieceDecoupe.Statut.OK.label,
            "message_erreur": "",
            "format_source_display": PieceDecoupe.FormatSource(extension).label,
            "avertissements": resultat.avertissements,
            "surface_mm2": resultat.surface_mm2,
            "perimetre_decoupe_mm": resultat.perimetre_mm,
            "largeur_mm": resultat.largeur_mm,
            "hauteur_mm": resultat.hauteur_mm,
            "nb_contours_interieurs": len(resultat.holes),
            "calques_detectes": resultat.calques,
            "a_gravure": bool(resultat.gravure),
            "longueur_gravure_mm": resultat.longueur_gravure_mm,
            "svg": svg,
        }
    )
