import json
import tempfile
from pathlib import Path

from django.contrib.admin.views.decorators import staff_member_required
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from .models import ImbricationJob, PieceDecoupe, ProfilImportDecoupe, RegleProfilImportDecoupe
from .services.apercu_svg import generer_svg_feuille, generer_svg_piece
from .services.geometrie import ErreurImportGeometrie, extraire_geometrie
from .services.imbrication import ItemANester, calculer_imbrication


@staff_member_required
@require_http_methods(["POST"])
def analyser_fichier_view(request):
    """Extrait la géométrie d'un fichier DXF/DWG, sans rien enregistrer — utilisé par le JS de
    la fiche PieceDecoupe pour afficher l'analyse et l'aperçu en direct. L'import réel
    (persisté) a toujours lieu à l'enregistrement, via `PieceDecoupe.importer_geometrie()` —
    cette vue ne fait qu'anticiper le même résultat pour l'affichage.

    Le fichier à analyser vient soit d'un upload frais (`fichier_source`, formulaire d'ajout ou
    nouveau fichier choisi sur le formulaire de modification), soit — à défaut — du fichier
    déjà enregistré d'une pièce existante (`piece_id`) : permet de rejouer l'analyse (ex.
    après avoir changé le rôle d'un calque dans le tableau) sans avoir à ré-uploader le
    fichier à chaque fois qu'on rouvre une fiche déjà importée.

    Classement des calques, par ordre de priorité :
    1. `calques_roles` (JSON, `{calque: rôle}`) — les choix faits à la main dans le tableau de
       calques affiché après une première analyse, s'il y en a.
    2. `profil_import` (id du profil sélectionné dans le formulaire), sinon.
    3. Par défaut, tout est traité comme découpe (comportement historique).

    Renvoie toujours `calques_detectes` et `calques_roles` (le classement effectivement
    appliqué, calque par calque) — y compris en cas d'échec (ex. plus aucun calque de découpe
    après un mauvais classement), pour que le tableau de calques reste affichable et
    corrigeable sans devoir ré-uploader le fichier."""
    fichier = request.FILES.get("fichier_source")
    piece_existante = None
    if fichier:
        nom_fichier = fichier.name
    else:
        piece_id = request.POST.get("piece_id")
        if piece_id:
            piece_existante = PieceDecoupe.objects.filter(pk=piece_id).exclude(fichier_source="").first()
        if not piece_existante:
            return JsonResponse({"detail": "Aucun fichier reçu."}, status=400)
        nom_fichier = piece_existante.fichier_source.name

    extension = nom_fichier.rsplit(".", 1)[-1].lower() if "." in nom_fichier else ""
    if extension not in (PieceDecoupe.FormatSource.DXF, PieceDecoupe.FormatSource.DWG):
        return JsonResponse({"detail": "Seuls les fichiers .dxf et .dwg sont acceptés."}, status=400)

    regles_calques = None
    calques_roles_manuels = request.POST.get("calques_roles")
    if calques_roles_manuels:
        try:
            mapping = json.loads(calques_roles_manuels)
        except json.JSONDecodeError:
            mapping = None
        if mapping:
            regles_calques = {str(calque).lower(): role for calque, role in mapping.items()}

    if regles_calques is None:
        profil_id = request.POST.get("profil_import")
        if profil_id:
            profil = ProfilImportDecoupe.objects.filter(pk=profil_id).first()
            if profil:
                regles_calques = profil.regles_par_calque()

    def _tenter(chemin):
        try:
            return extraire_geometrie(chemin, extension, regles_calques=regles_calques), None
        except ErreurImportGeometrie as exc:
            return None, exc

    if piece_existante:
        resultat, erreur = _tenter(piece_existante.fichier_source.path)
    else:
        with tempfile.TemporaryDirectory() as dossier:
            chemin = Path(dossier) / fichier.name
            with open(chemin, "wb") as cible:
                for morceau in fichier.chunks():
                    cible.write(morceau)
            resultat, erreur = _tenter(str(chemin))

    if erreur:
        return JsonResponse(
            {
                "ok": False,
                "statut_display": PieceDecoupe.Statut.ERREUR.label,
                "message_erreur": str(erreur),
                "format_source_display": PieceDecoupe.FormatSource(extension).label,
                "calques_detectes": erreur.calques,
                "calques_roles": erreur.calques_roles,
                "role_choices": RegleProfilImportDecoupe.Role.choices,
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
            "calques_roles": resultat.calques_roles,
            "role_choices": RegleProfilImportDecoupe.Role.choices,
            "a_gravure": bool(resultat.gravure),
            "longueur_gravure_mm": resultat.longueur_gravure_mm,
            "svg": svg,
        }
    )


@staff_member_required
@require_http_methods(["POST"])
def previsualiser_imbrication_view(request):
    """Recalcule l'imbrication à partir de l'état courant du formulaire (feuille, direction,
    coin de départ, lignes pièce/quantité), sans rien enregistrer — utilisé par le JS de la
    fiche ImbricationJob pour un aperçu en direct à chaque changement, sur le formulaire
    d'ajout comme de modification, sans passer par "Enregistrer et continuer les
    modifications". Le calcul réel (persisté) reste `ImbricationJob.calculer()`, appelé à
    l'enregistrement."""
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"detail": "JSON invalide."}, status=400)

    try:
        largeur_feuille_mm = float(payload.get("largeur_feuille_mm") or 0)
        longueur_feuille_mm = float(payload.get("longueur_feuille_mm") or 0)
        marge_bord_mm = float(payload.get("marge_bord_mm") or 0)
        espacement_pieces_mm = float(payload.get("espacement_pieces_mm") or 0)
    except (TypeError, ValueError):
        return JsonResponse({"detail": "Dimensions de feuille invalides."}, status=400)

    if largeur_feuille_mm <= 0 or longueur_feuille_mm <= 0:
        return JsonResponse({"ok": False, "detail": "Renseignez la largeur et la longueur de feuille."})

    direction = payload.get("direction") or ImbricationJob.Direction.HORIZONTAL
    coin_depart = payload.get("coin_depart") or ImbricationJob.CoinDepart.BAS_GAUCHE

    lignes = payload.get("lignes") or []
    piece_ids = [str(ligne.get("piece")) for ligne in lignes if ligne.get("piece")]
    pieces = {str(p.pk): p for p in PieceDecoupe.objects.filter(pk__in=piece_ids)}

    items = []
    pieces_ignorees = []
    for ligne in lignes:
        piece_id = ligne.get("piece")
        if not piece_id:
            continue
        piece = pieces.get(str(piece_id))
        if piece is None:
            continue
        if piece.statut != PieceDecoupe.Statut.OK:
            pieces_ignorees.append(piece.nom)
            continue
        try:
            quantite = int(ligne.get("quantite") or 0)
        except (TypeError, ValueError):
            quantite = 0
        if quantite <= 0:
            continue
        items.append(
            ItemANester(
                piece_id=piece.pk,
                largeur_mm=piece.largeur_mm,
                hauteur_mm=piece.hauteur_mm,
                surface_mm2=piece.surface_mm2,
                pas_rotation_deg=piece.pas_rotation_deg,
                symetrie_autorisee=piece.symetrie_autorisee,
                exterieur=(piece.contour_json or {}).get("exterieur") or [],
                quantite=quantite,
            )
        )

    if not items:
        return JsonResponse(
            {
                "ok": True,
                "nb_feuilles": 0,
                "surface_pieces_mm2": 0,
                "surface_feuilles_mm2": 0,
                "taux_utilisation_pct": 0,
                "cout_matiere_estime": None,
                "pieces_non_placees": [],
                "pieces_ignorees": pieces_ignorees,
                "feuilles": [],
            }
        )

    resultat = calculer_imbrication(
        items,
        largeur_feuille_mm=largeur_feuille_mm,
        longueur_feuille_mm=longueur_feuille_mm,
        marge_bord_mm=marge_bord_mm,
        espacement_pieces_mm=espacement_pieces_mm,
        direction=direction,
        coin_depart=coin_depart,
    )

    # Réutilise la formule de coût matière du modèle plutôt que de la dupliquer ici — un job
    # non enregistré (jamais .save()) porte juste assez d'état pour ça.
    job_temporaire = ImbricationJob(
        article_matiere_id=payload.get("article_matiere") or None,
        largeur_feuille_mm=largeur_feuille_mm,
        longueur_feuille_mm=longueur_feuille_mm,
        nb_feuilles=resultat.nb_feuilles,
    )
    cout_matiere_estime = job_temporaire._calculer_cout_matiere()

    par_feuille = {}
    for placement in resultat.placements:
        par_feuille.setdefault(placement.numero_feuille, []).append(
            (
                pieces[str(placement.piece_id)],
                placement.x_mm,
                placement.y_mm,
                placement.rotation_deg,
                placement.miroir,
            )
        )
    feuilles = [
        {"numero": numero, "svg": generer_svg_feuille(largeur_feuille_mm, longueur_feuille_mm, par_feuille[numero])}
        for numero in sorted(par_feuille)
    ]

    return JsonResponse(
        {
            "ok": True,
            "nb_feuilles": resultat.nb_feuilles,
            "surface_pieces_mm2": resultat.surface_pieces_mm2,
            "surface_feuilles_mm2": resultat.surface_feuilles_mm2,
            "taux_utilisation_pct": resultat.taux_utilisation_pct,
            "cout_matiere_estime": cout_matiere_estime,
            "pieces_non_placees": [pieces[str(pid)].nom for pid in resultat.pieces_non_placees if str(pid) in pieces],
            "pieces_ignorees": pieces_ignorees,
            "feuilles": feuilles,
        }
    )
