"""Pièce libre du configurateur web : une plaque de base (rectangle ou disque) à laquelle le client retire ou ajoute des
éléments placés en X / Y (trous ronds, oblongs, rectangles, polygones réguliers). L'ERP reconstruit la pièce à partir de
cette recette, la contrôle et la chiffre comme une pièce de la bibliothèque : le navigateur ne fait que dessiner.

Recette : {"base": {"forme": "rectangle", "largeur", "hauteur", "rayon_angle"} | {"forme": "disque", "diametre"},
           "elements": [{"type": "rond" | "oblong" | "rectangle" | "polygone", "mode": "retirer" | "ajouter",
                         "x", "y" (centre, mm, origine au coin bas-gauche de la plaque), "diametre" | "longueur" + "largeur" |
                         "largeur" + "hauteur" (+ "rayon") | "nb_cotes" + "diametre", "angle" (degrés, sens trigonométrique)}]}"""

import math

from shapely import affinity
from shapely.geometry import LineString, MultiPolygon, Polygon
from shapely.ops import unary_union

from decoupe.services import formes

ELEMENTS_MAX = 200
TYPES = ("rond", "oblong", "rectangle", "polygone")


class ErreurLibre(Exception):
    pass


def _nombre(d, cle, nom, minimum=None, defaut=None):
    brut = d.get(cle, defaut)
    try:
        v = float(str(brut).replace(",", "."))
    except (TypeError, ValueError):
        raise ErreurLibre(f"{nom} : « {cle} » doit être un nombre.")
    if not math.isfinite(v):
        raise ErreurLibre(f"{nom} : « {cle} » doit être un nombre.")
    if minimum is not None and v < minimum:
        raise ErreurLibre(f"{nom} : « {cle} » doit être au moins {minimum:g}.")
    return v


def _base(base):
    if not isinstance(base, dict):
        raise ErreurLibre("Pièce libre : choisissez une plaque de base.")
    forme = base.get("forme")
    if forme == "rectangle":
        cotes = {"largeur": _nombre(base, "largeur", "Plaque", 1), "hauteur": _nombre(base, "hauteur", "Plaque", 1),
                 "rayon_angle": _nombre(base, "rayon_angle", "Plaque", 0, 0), "chanfrein": 0}
    elif forme == "disque":
        cotes = {"diametre": _nombre(base, "diametre", "Plaque", 1)}
    else:
        raise ErreurLibre("Pièce libre : la plaque de base est un rectangle ou un disque.")
    try:
        c = formes.construire(forme, cotes)
    except formes.ErreurForme as exc:
        raise ErreurLibre(str(exc))
    return Polygon(c.exterieur), forme, cotes


def _element(e, i):
    nom = f"Élément {i}"
    if not isinstance(e, dict) or e.get("type") not in TYPES:
        raise ErreurLibre(f"{nom} : type inconnu (rond, oblong, rectangle ou polygone).")
    x, y = _nombre(e, "x", nom), _nombre(e, "y", nom)
    t = e["type"]
    if t == "rond":
        d = _nombre(e, "diametre", nom, 0.5)
        poly = Polygon(formes._cercle(0, 0, d / 2))
        taille = d
    elif t == "oblong":
        lo, la = _nombre(e, "longueur", nom, 0.5), _nombre(e, "largeur", nom, 0.5)
        if lo < la:
            lo, la = la, lo
        r = la / 2
        poly = Polygon(formes._arc(lo / 2 - r, 0, r, -90, 90) + formes._arc(-lo / 2 + r, 0, r, 90, 270))
        taille = la
    elif t == "rectangle":
        w, h = _nombre(e, "largeur", nom, 0.5), _nombre(e, "hauteur", nom, 0.5)
        r = _nombre(e, "rayon", nom, 0, 0)
        try:
            c = formes.construire("rectangle", {"largeur": w, "hauteur": h, "rayon_angle": min(r, min(w, h) / 2), "chanfrein": 0})
        except formes.ErreurForme as exc:
            raise ErreurLibre(f"{nom} : {exc}")
        poly = affinity.translate(Polygon(c.exterieur), -w / 2, -h / 2)
        taille = min(w, h)
    else:
        n = int(_nombre(e, "nb_cotes", nom, 3, 6))
        if n > 64:
            raise ErreurLibre(f"{nom} : 64 côtés au plus.")
        d = _nombre(e, "diametre", nom, 0.5)
        poly = Polygon([(d / 2 * math.cos(math.pi / 2 + 2 * math.pi * k / n), d / 2 * math.sin(math.pi / 2 + 2 * math.pi * k / n)) for k in range(n)])
        taille = d * math.cos(math.pi / n)
    angle = _nombre(e, "angle", nom, None, 0)
    if angle:
        poly = affinity.rotate(poly, angle, origin=(0, 0))
    mode = e.get("mode", "retirer")
    if mode not in ("retirer", "ajouter"):
        raise ErreurLibre(f"{nom} : mode « retirer » ou « ajouter ».")
    return affinity.translate(poly, x, y), mode, taille


def construire(recette, epaisseur=None):
    """Contour (exterieur, trous) de la pièce libre, sa désignation, et les points à faire vérifier par l'atelier.
    Lève ErreurLibre pour une recette invalide ou une pièce impossible."""
    if not isinstance(recette, dict):
        raise ErreurLibre("Pièce libre illisible.")
    piece, forme, cotes = _base(recette.get("base"))
    elements = recette.get("elements") or []
    if not isinstance(elements, list):
        raise ErreurLibre("Pièce libre : liste d'éléments illisible.")
    if len(elements) > ELEMENTS_MAX:
        raise ErreurLibre(f"Pièce libre : {ELEMENTS_MAX} éléments au plus ; au-delà, envoyez un fichier DXF.")
    validations = []
    for i, e in enumerate(elements, start=1):
        poly, mode, taille = _element(e, i)
        if mode == "ajouter":
            if epaisseur and taille < epaisseur:
                validations.append(f"Élément {i} plus étroit que l'épaisseur : faisabilité vérifiée par l'atelier.")
            if not poly.intersects(piece):
                raise ErreurLibre(f"Élément {i} : il doit toucher la pièce pour s'y ajouter.")
            piece = unary_union([piece, poly])
        else:
            if not poly.intersects(piece):
                raise ErreurLibre(f"Élément {i} : il est en dehors de la pièce.")
            piece = piece.difference(poly)
        if piece.is_empty:
            raise ErreurLibre(f"Élément {i} : il ne reste plus de matière.")
    if isinstance(piece, MultiPolygon):
        raise ErreurLibre("La pièce se coupe en plusieurs morceaux : vérifiez les découpes (une pièce à la fois).")
    piece = piece.buffer(0)
    if not isinstance(piece, Polygon) or piece.is_empty:
        raise ErreurLibre("La pièce obtenue n'est pas valide.")

    if epaisseur:
        # Matière plus fine que l'épaisseur entre deux découpes, ou entre une découpe et le bord.
        bords = [LineString(i.coords) for i in piece.interiors]
        exterieur = LineString(piece.exterior.coords)
        fin = any(b.distance(exterieur) < epaisseur for b in bords) or any(
            a.distance(b) < epaisseur for k, a in enumerate(bords) for b in bords[k + 1:])
        if fin:
            validations.append("Matière plus fine que l'épaisseur entre deux découpes ou avec le bord : faisabilité vérifiée par l'atelier.")

    minx, miny, _, _ = piece.bounds
    piece = affinity.translate(piece, -minx, -miny)
    exterieur = [(round(x, 4), round(y, 4)) for x, y in piece.exterior.coords[:-1]]
    trous = [[(round(x, 4), round(y, 4)) for x, y in i.coords[:-1]] for i in piece.interiors]
    if forme == "rectangle":
        designation = f"Pièce libre {cotes['largeur']:g} × {cotes['hauteur']:g}"
    else:
        designation = f"Pièce libre Ø{cotes['diametre']:g}"
    if elements:
        designation += f", {len(elements)} élément{'s' if len(elements) > 1 else ''}"
    return {"exterieur": exterieur, "trous": trous}, designation, validations
