"""Aperçu visuel (SVG) d'une feuille imbriquée, à partir du contour réel des pièces placées."""

from shapely.affinity import rotate, scale, translate
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


def generer_svg_feuille(largeur_feuille_mm, longueur_feuille_mm, placements, couleurs=None):
    """`placements` : itérable de (piece, x_mm, y_mm, rotation_deg, miroir). `couleurs` : {id de pièce: (remplissage, trait)}
    pour distinguer les pièces d'une même feuille (couleur bleue pour toutes sinon)."""
    elements = [
        f'<rect x="0" y="0" width="{largeur_feuille_mm}" height="{longueur_feuille_mm}" '
        'fill="#f5f5f5" stroke="#333333" stroke-width="1" />'
    ]
    for piece, x_mm, y_mm, rotation_deg, miroir in placements:
        polygone = _polygone_piece(piece)
        if polygone is None:
            continue
        if miroir:
            polygone = scale(polygone, xfact=-1, origin=(0, 0))
        if rotation_deg:
            polygone = rotate(polygone, rotation_deg, origin=(0, 0))
        minx, miny, _, _ = polygone.bounds
        polygone = translate(polygone, xoff=-minx, yoff=-miny)
        polygone = translate(polygone, xoff=x_mm, yoff=y_mm)
        chemin = _chemin_svg(polygone)
        remplissage, trait = (couleurs or {}).get(piece.pk, ("#cfe8ff", "#0b5fa5"))
        elements.append(
            f'<path d="{chemin}" fill="{remplissage}" stroke="{trait}" stroke-width="0.5" fill-rule="evenodd" />'
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


def _traits_svg(traits, couleur, epaisseur, pointilles=False):
    tirets = f' stroke-dasharray="{epaisseur * 3:.3f},{epaisseur * 2:.3f}"' if pointilles else ""
    elements = []
    for trait in traits or []:
        if len(trait) < 2:
            continue
        points = " L ".join(f"{x:.2f},{y:.2f}" for x, y in trait)
        elements.append(
            f'<path d="M {points}" fill="none" stroke="{couleur}" stroke-width="{epaisseur:.3f}"{tirets} />'
        )
    return elements


def generer_svg_piece(largeur_mm, hauteur_mm, exterieur, trous, gravure=None, pliage=None):
    """Aperçu autonome d'une pièce seule (silhouette + trous), sans feuille englobante —
    utilisé sur la fiche PieceDecoupe, contrairement à `generer_svg_feuille` (plusieurs
    pièces placées sur une feuille de tôle, utilisé pour l'aperçu d'imbrication). `gravure` et
    `pliage` sont des listes de tracés (chacun une liste de points) affichés par-dessus la
    silhouette de découpe, dans une couleur distincte, pour vérifier visuellement le
    classement des calques du profil d'import."""
    if not exterieur or len(exterieur) < 3:
        return None
    chemin = _chemin_svg_depuis_anneaux(exterieur, trous or [])
    marge = max(largeur_mm, hauteur_mm, 1) * 0.04
    epaisseur_trait = max(largeur_mm, hauteur_mm, 1) * 0.004
    elements = [
        f'<path d="{chemin}" fill="#cfe8ff" stroke="#0b5fa5" stroke-width="{epaisseur_trait:.3f}" '
        f'fill-rule="evenodd" />'
    ]
    elements.extend(_traits_svg(gravure, "#c2410c", epaisseur_trait))
    elements.extend(_traits_svg(pliage, "#15803d", epaisseur_trait, pointilles=True))
    contenu = "".join(elements)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="{-marge:.2f} {-marge:.2f} {largeur_mm + 2 * marge:.2f} {hauteur_mm + 2 * marge:.2f}" '
        f'style="width: 100%; max-width: 420px; background: #f5f5f5">{contenu}</svg>'
    )


def generer_svg_feuille_a_plat(largeur_x_mm, hauteur_y_mm, placements, couleurs=None, chute_bout=None):
    """Feuille posée à plat (longueur à l'horizontale), origine en bas à gauche comme sur la machine. `placements` : itérable
    de (piece, x_mm, y_mm, rotation_deg, miroir) dans ce repère (y vers le haut). `chute_bout` : (x, y, largeur, hauteur) de la
    chute de bout, hachurée en rouge. Le fond gris est la chute entre les pièces."""
    H = hauteur_y_mm
    defs = (
        '<defs><pattern id="dp-hachure" width="60" height="60" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
        '<rect width="60" height="60" fill="#fee2e2"/><rect width="26" height="60" fill="#f87171"/></pattern></defs>'
    )
    # Le repère de dessin a l'axe y vers le haut : les pièces sont dessinées comme dans leur fichier DXF.
    elements = [f'<rect x="0" y="0" width="{largeur_x_mm}" height="{H}" fill="#e5e7eb" stroke="#6b7280" stroke-width="6" />']
    texte = ""
    if chute_bout:
        x, y, w, h = chute_bout
        elements.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" fill="url(#dp-hachure)" />')
        cote = f"{max(w, h):.0f} × {min(w, h):.0f} mm"
        taille = max(min(w * 0.11, h * 0.2, 90), 28)
        centre_x, centre_y = x + w / 2, H - (y + h / 2)
        for ligne, dy, t in (("chute de bout", -taille * 0.2, taille), (cote, taille * 0.95, taille * 0.8)):
            texte += (
                f'<text x="{centre_x:.1f}" y="{centre_y + dy:.1f}" text-anchor="middle" font-size="{t:.0f}" font-weight="700" fill="#991b1b" '
                f'paint-order="stroke" stroke="#ffffff" stroke-width="{t * 0.25:.0f}">{ligne}</text>'
            )
    for piece, x_mm, y_mm, rotation_deg, miroir in placements:
        polygone = _polygone_piece(piece)
        if polygone is None:
            continue
        if miroir:
            polygone = scale(polygone, xfact=-1, origin=(0, 0))
        if rotation_deg:
            polygone = rotate(polygone, rotation_deg, origin=(0, 0))
        minx, miny, _, _ = polygone.bounds
        polygone = translate(polygone, xoff=x_mm - minx, yoff=y_mm - miny)
        remplissage, trait = (couleurs or {}).get(piece.pk, ("#cfe8ff", "#0b5fa5"))
        elements.append(f'<path d="{_chemin_svg(polygone)}" fill="{remplissage}" stroke="{trait}" stroke-width="5" fill-rule="evenodd" />')
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {largeur_x_mm} {H}" class="dp-tole" preserveAspectRatio="xMidYMid meet">'
        f'{defs}<g transform="translate(0,{H}) scale(1,-1)">{"".join(elements)}</g>{texte}</svg>'
    )
