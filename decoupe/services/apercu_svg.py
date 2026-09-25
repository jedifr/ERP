"""Aperçu visuel (SVG) d'une feuille imbriquée, à partir du contour réel des pièces placées."""

from shapely.affinity import rotate, translate
from shapely.geometry import Polygon


def _polygone_piece(piece):
    donnees = piece.contour_json or {}
    exterieur = donnees.get("exterieur") or []
    trous = donnees.get("trous") or []
    if len(exterieur) < 3:
        return None
    return Polygon(exterieur, trous)


def _chemin_svg(polygone):
    anneaux = [polygone.exterior, *polygone.interiors]
    morceaux = []
    for anneau in anneaux:
        points = " L ".join(f"{x:.2f},{y:.2f}" for x, y in anneau.coords)
        morceaux.append(f"M {points} Z")
    return " ".join(morceaux)


def generer_svg_feuille(largeur_feuille_mm, longueur_feuille_mm, placements):
    """`placements` : itérable de (piece, x_mm, y_mm, rotation_deg)."""
    elements = [
        f'<rect x="0" y="0" width="{largeur_feuille_mm}" height="{longueur_feuille_mm}" '
        'fill="#f5f5f5" stroke="#333333" stroke-width="1" />'
    ]
    for piece, x_mm, y_mm, rotation_deg in placements:
        polygone = _polygone_piece(piece)
        if polygone is None:
            continue
        if rotation_deg:
            polygone = rotate(polygone, rotation_deg, origin=(0, 0))
            minx, miny, _, _ = polygone.bounds
            polygone = translate(polygone, xoff=-minx, yoff=-miny)
        polygone = translate(polygone, xoff=x_mm, yoff=y_mm)
        chemin = _chemin_svg(polygone)
        elements.append(
            f'<path d="{chemin}" fill="#cfe8ff" stroke="#0b5fa5" stroke-width="0.5" fill-rule="evenodd" />'
        )

    contenu = "".join(elements)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {largeur_feuille_mm} {longueur_feuille_mm}" '
        f'width="{largeur_feuille_mm}mm" height="{longueur_feuille_mm}mm">{contenu}</svg>'
    )


def _chemin_svg_depuis_anneaux(exterieur, trous):
    anneaux = [exterieur, *trous]
    morceaux = []
    for anneau in anneaux:
        points = " L ".join(f"{x:.2f},{y:.2f}" for x, y in anneau)
        morceaux.append(f"M {points} Z")
    return " ".join(morceaux)


def generer_svg_piece(largeur_mm, hauteur_mm, exterieur, trous):
    """Aperçu autonome d'une pièce seule (silhouette + trous), sans feuille englobante —
    utilisé sur la fiche PieceDecoupe, contrairement à `generer_svg_feuille` (plusieurs
    pièces placées sur une feuille de tôle, utilisé pour l'aperçu d'imbrication)."""
    if not exterieur or len(exterieur) < 3:
        return None
    chemin = _chemin_svg_depuis_anneaux(exterieur, trous or [])
    marge = max(largeur_mm, hauteur_mm, 1) * 0.04
    epaisseur_trait = max(largeur_mm, hauteur_mm, 1) * 0.004
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="{-marge:.2f} {-marge:.2f} {largeur_mm + 2 * marge:.2f} {hauteur_mm + 2 * marge:.2f}" '
        f'style="width: 100%; max-width: 420px; background: #f5f5f5">'
        f'<path d="{chemin}" fill="#cfe8ff" stroke="#0b5fa5" stroke-width="{epaisseur_trait:.3f}" '
        f'fill-rule="evenodd" /></svg>'
    )
