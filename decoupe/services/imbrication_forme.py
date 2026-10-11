"""Imbrication selon la forme réelle des pièces (contour extérieur et trous), par grille.

Principe : la feuille est découpée en cellules de quelques millimètres. Chaque pièce, sous chacune de ses orientations, est
tramée sur cette grille après avoir été **élargie de la moitié de l'écart entre pièces** (plus une demi-diagonale de cellule, ce
qui rend la trame prudente) : deux pièces dont les trames ne se chevauchent pas sont donc toujours séparées d'au moins l'écart
demandé. Les trous restent libres : une petite pièce peut se loger dans le trou d'une grande.

Placement « en bas à gauche » : chaque pièce prend, parmi toutes les positions libres de toutes ses orientations, celle qui
descend le moins loin le long de la feuille (puis la plus à gauche) ; les pièces les plus grandes passent d'abord. Les
positions libres se calculent d'un coup par corrélation de la trame de la pièce avec l'occupation de la feuille (FFT).

La grille étant prudente (jusqu'à une cellule de marge de chaque côté), la pièce posée est ensuite **tassée** : on la fait
glisser vers le bas puis vers la gauche, par pas de plus en plus fins, tant qu'elle reste dans la zone utile et à l'écart
demandé de ses voisines (distances exactes sur des contours simplifiés). Son occupation est alors retramée à sa position réelle.

Le résultat est contrôlé avec les vrais contours (shapely) : une pièce hors de la zone utile ou trop près d'une autre fait
écarter tout le calcul (l'appelant garde alors l'imbrication par rectangles). Les rotations sont limitées à 8 orientations."""

import math

import numpy as np
import shapely
from shapely.affinity import rotate, translate
from shapely.geometry import Polygon
from shapely.strtree import STRtree

from .imbrication import Placement, _resultat

MAX_CELLULES = 60_000
MAX_ORIENTATIONS = 8
RESOLUTION_MIN_MM, RESOLUTION_MAX_MM = 2.0, 12.0
DEMI_DIAGONALE = 0.7072
TOLERANCE_SIMPLIFICATION_MM = 0.15
PAS_TASSEMENT_MM = (8.0, 4.0, 2.0, 1.0, 0.5)


class ImbricationFormeImpossible(Exception):
    """Pas de contour exploitable, ou résultat qui ne passe pas le contrôle géométrique."""


def _angles(pas_rotation_deg):
    if not pas_rotation_deg:
        return [0]
    pas = max(int(pas_rotation_deg), 360 // MAX_ORIENTATIONS)
    return [i * pas for i in range(360 // pas)]


def _polygone(item):
    if len(item.exterieur) < 3:
        raise ImbricationFormeImpossible("contour absent")
    polygone = Polygon(item.exterieur, [t for t in (getattr(item, "trous", None) or []) if len(t) >= 3])
    if not polygone.is_valid:
        polygone = polygone.buffer(0)
    if polygone.is_empty or polygone.geom_type != "Polygon":
        raise ImbricationFormeImpossible("contour invalide")
    return polygone


def _choisir_resolution(polygones, largeur_utile, hauteur_utile):
    plus_petite = min(min(maxx - minx, maxy - miny) for minx, miny, maxx, maxy in (p.bounds for p in polygones))
    c = min(max(plus_petite / 25, RESOLUTION_MIN_MM), RESOLUTION_MAX_MM)
    return max(c, math.sqrt(largeur_utile * hauteur_utile / MAX_CELLULES))


class _Trame:
    __slots__ = ("polygone", "angle", "largeur", "hauteur", "masque", "fft", "lignes", "colonnes")


def _trame(base, angle, c, b):
    """Trame (cellules occupées, bords élargis de `b`) de la pièce tournée de `angle`, calée sur son rectangle englobant."""
    g = rotate(base, angle, origin=(0, 0)) if angle else base
    minx, miny, maxx, maxy = g.bounds
    g = translate(g, xoff=-minx, yoff=-miny)
    t = _Trame()
    t.polygone, t.angle, t.largeur, t.hauteur = g, angle, maxx - minx, maxy - miny
    t.colonnes = math.ceil((t.largeur + 2 * b) / c)
    t.lignes = math.ceil((t.hauteur + 2 * b) / c)
    xs = -b + (np.arange(t.colonnes) + 0.5) * c
    ys = -b + (np.arange(t.lignes) + 0.5) * c
    X, Y = np.meshgrid(xs, ys)
    t.masque = shapely.contains_xy(g.buffer(b), X, Y)
    t.fft = None
    return t


def imbriquer_forme(items, largeur_feuille_mm, longueur_feuille_mm, marge_bord_mm=0.0, espacement_pieces_mm=0.0):
    """Imbrication par la forme (voir le module) : deux critères de placement sont essayés (bord bas de la pièce le plus bas, ou
    coin bas-gauche le plus bas), le meilleur est gardé (moins de feuilles, puis moins de bande entamée sur la dernière).
    Lève ImbricationFormeImpossible si une pièce n'a pas de contour exploitable ou si le contrôle géométrique final échoue."""
    from .imbrication import etendue_derniere_feuille_mm

    resultats, erreur = [], None
    if len(items) == 1 and items[0].quantite >= 3:  # un seul modèle en série : un réseau en quinconce peut battre le placement pièce à pièce
        try:
            reseau = _reseau(items[0], largeur_feuille_mm, longueur_feuille_mm, marge_bord_mm, espacement_pieces_mm)
        except ImbricationFormeImpossible:
            reseau = None
        if reseau is not None:
            resultats.append(reseau)
    for critere in ("bord", "coin"):
        try:
            resultats.append(_imbriquer(items, largeur_feuille_mm, longueur_feuille_mm, marge_bord_mm, espacement_pieces_mm, critere))
        except ImbricationFormeImpossible as exc:
            erreur = exc
    if not resultats:
        raise erreur
    return min(resultats, key=lambda r: (len(r.pieces_non_placees), r.nb_feuilles, etendue_derniere_feuille_mm(r, marge_bord_mm)))


def _imbriquer(items, largeur_feuille_mm, longueur_feuille_mm, marge_bord_mm, espacement_pieces_mm, critere):
    if not items:
        raise ImbricationFormeImpossible("aucune pièce")
    base = {i.piece_id: _polygone(i) for i in items}
    largeur_utile = largeur_feuille_mm - 2 * marge_bord_mm
    hauteur_utile = longueur_feuille_mm - 2 * marge_bord_mm
    c = _choisir_resolution(list(base.values()), largeur_utile, hauteur_utile)
    b = espacement_pieces_mm / 2 + DEMI_DIAGONALE * c
    colonnes, lignes = math.ceil((largeur_utile + 2 * b) / c), math.ceil((hauteur_utile + 2 * b) / c)
    origine_x, origine_y = marge_bord_mm - b, marge_bord_mm - b

    # trames de chaque pièce sous chacune de ses orientations (celles qui tiennent dans une feuille vide)
    trames, surface, non_placees = {}, 0.0, []
    pas_par_piece = {i.piece_id: i.pas_rotation_deg for i in items}
    for piece_id, polygone in base.items():
        vues, liste = set(), []
        for angle in _angles(pas_par_piece[piece_id]):
            t = _trame(polygone, angle, c, b)
            cle = (t.lignes, t.colonnes, hash(t.masque.tobytes()))  # une orientation qui donne la même trame est inutile
            if cle in vues or t.lignes > lignes or t.colonnes > colonnes:
                continue
            vues.add(cle)
            t.fft = np.conj(np.fft.rfft2(t.masque.astype(np.float32), s=(lignes, colonnes)))
            liste.append(t)
        trames[piece_id] = liste
    unites = []
    for item in sorted(items, key=lambda i: -base[i.piece_id].area):
        if not trames[item.piece_id]:
            non_placees.append(item.piece_id)
            continue
        unites.extend([item] * int(item.quantite))
        surface += item.surface_mm2 * item.quantite

    feuilles = []  # occupation (lignes, colonnes), son FFT et les pièces posées
    bloque = set()  # (feuille, pièce, angle) : plus aucune place, et l'occupation ne fait que croître
    placements = []
    simplifiees = {}  # (pièce, angle) -> contour simplifié pour les distances du tassement
    xs_grille = origine_x + (np.arange(colonnes) + 0.5) * c
    ys_grille = origine_y + (np.arange(lignes) + 0.5) * c
    zone = (marge_bord_mm, marge_bord_mm, largeur_feuille_mm - marge_bord_mm, longueur_feuille_mm - marge_bord_mm)
    for item in unites:
        meilleur = None
        for numero, feuille in enumerate(feuilles):
            meilleur = _meilleure_position(feuille, trames[item.piece_id], numero, item.piece_id, bloque, lignes, colonnes, c, b, largeur_utile, hauteur_utile, critere)
            if meilleur:
                break
        if meilleur is None:
            feuille = {"occ": np.zeros((lignes, colonnes), dtype=np.float32), "fft": None, "posees": [], "bornes": []}
            feuilles.append(feuille)
            numero = len(feuilles) - 1
            meilleur = _meilleure_position(feuille, trames[item.piece_id], numero, item.piece_id, bloque, lignes, colonnes, c, b, largeur_utile, hauteur_utile, critere)
            if meilleur is None:  # ne devrait pas arriver (la trame tient dans une feuille vide)
                raise ImbricationFormeImpossible("pièce impossible à placer")
        trame, i, j = meilleur
        x, y = origine_x + j * c + b, origine_y + i * c + b
        cle = (item.piece_id, trame.angle)
        if cle not in simplifiees:
            simplifiees[cle] = trame.polygone.simplify(TOLERANCE_SIMPLIFICATION_MM, preserve_topology=True)
        x, y = _tasser(simplifiees[cle], x, y, feuille["posees"], feuille["bornes"], zone, espacement_pieces_mm)
        posee = translate(simplifiees[cle], xoff=x, yoff=y)
        feuille["posees"].append(posee)
        feuille["bornes"].append(posee.bounds)  # calculées une fois : `.bounds` de shapely est coûteux appelé des millions de fois
        _occuper(feuille["occ"], trame.polygone, x, y, b, xs_grille, ys_grille)
        feuille["fft"] = None
        placements.append(Placement(
            piece_id=item.piece_id, numero_feuille=numero + 1, x_mm=x, y_mm=y,
            largeur_placee_mm=trame.largeur, hauteur_placee_mm=trame.hauteur, rotation_deg=trame.angle,
        ))
    _controler(placements, base, largeur_feuille_mm, longueur_feuille_mm, marge_bord_mm, espacement_pieces_mm)
    return _resultat(placements, len(feuilles), surface, largeur_feuille_mm, longueur_feuille_mm, non_placees)


def _pas_minimum(simplifie, minimum, direction, borne, depart=(0.0, 0.0)):
    """Plus petit coefficient t dans [0, borne] tel que la pièce, décalée de `depart` + t × `direction`, reste à plus de `minimum` de la
    pièce d'origine (dichotomie ; `borne` est connu valide). Les cas non monotones sont rattrapés par le contrôle exact du résultat."""
    bas, haut = 0.0, borne
    for _ in range(18):
        milieu = (bas + haut) / 2
        decalee = translate(simplifie, xoff=depart[0] + direction[0] * milieu, yoff=depart[1] + direction[1] * milieu)
        if simplifie.distance(decalee) >= minimum:
            haut = milieu
        else:
            bas = milieu
    return haut


def _reseau(item, largeur_feuille_mm, longueur_feuille_mm, marge, espacement):
    """Réseau régulier pour un lot d'un seul modèle de pièce : rangées le long de la largeur de la feuille au pas minimal, chaque
    rangée décalée d'une fraction de pas par rapport à la précédente (une demi-pièce : le quinconce des ronds), rangées rapprochées
    au maximum. Plusieurs orientations et plusieurs décalages sont essayés ; le meilleur réseau (moins de feuilles, puis moins de bande
    entamée) est contrôlé avec les vrais contours. Lève ImbricationFormeImpossible si aucun réseau ne passe."""
    from .imbrication import etendue_derniere_feuille_mm

    base = _polygone(item)
    minimum = espacement + 2 * TOLERANCE_SIMPLIFICATION_MM
    largeur_utile, hauteur_utile = largeur_feuille_mm - 2 * marge, longueur_feuille_mm - 2 * marge
    meilleur = None
    for angle in _angles(item.pas_rotation_deg):
        g = rotate(base, angle, origin=(0, 0)) if angle else base
        minx, miny, maxx, maxy = g.bounds
        g = translate(g, xoff=-minx, yoff=-miny)
        simplifie = g.simplify(TOLERANCE_SIMPLIFICATION_MM, preserve_topology=True)
        largeur, hauteur = maxx - minx, maxy - miny
        if largeur > largeur_utile + 1e-9 or hauteur > hauteur_utile + 1e-9:
            continue
        pas = _pas_minimum(simplifie, minimum, (1.0, 0.0), largeur + minimum)
        for fraction in (0.5, 0.45, 0.55, 0.4, 0.6, 0.0):
            decalage = pas * fraction
            h = _pas_minimum(simplifie, minimum, (0.0, 1.0), hauteur + minimum, (decalage, 0.0))
            if decalage:  # l'autre voisine de la rangée du dessous
                h = max(h, _pas_minimum(simplifie, minimum, (0.0, 1.0), hauteur + minimum, (decalage - pas, 0.0)))
            placements, feuille, k, restant = [], 1, 0, int(item.quantite)
            while restant > 0:
                y = marge + k * h
                if y + hauteur > marge + hauteur_utile + 1e-9:
                    feuille, k = feuille + 1, 0
                    continue
                x0 = marge + (decalage if k % 2 else 0.0)
                place = 0
                while restant > 0 and x0 + place * pas + largeur <= marge + largeur_utile + 1e-9:
                    placements.append(Placement(
                        piece_id=item.piece_id, numero_feuille=feuille, x_mm=x0 + place * pas, y_mm=y,
                        largeur_placee_mm=largeur, hauteur_placee_mm=hauteur, rotation_deg=angle,
                    ))
                    place, restant = place + 1, restant - 1
                if place == 0:  # une rangée décalée qui ne tient pas : on saute pour ne pas boucler
                    if k % 2:
                        k += 1
                        continue
                    raise ImbricationFormeImpossible("réseau impossible")
                k += 1
            resultat = _resultat(placements, feuille, item.surface_mm2 * item.quantite, largeur_feuille_mm, longueur_feuille_mm, [])
            cle = (resultat.nb_feuilles, etendue_derniere_feuille_mm(resultat, marge))
            if meilleur is not None and cle >= meilleur[0]:
                continue
            try:
                _controler(placements, {item.piece_id: base}, largeur_feuille_mm, longueur_feuille_mm, marge, espacement)
            except ImbricationFormeImpossible:
                continue
            meilleur = (cle, resultat)
    if meilleur is None:
        raise ImbricationFormeImpossible("aucun réseau valide")
    return meilleur[1]


def _meilleure_position(feuille, trames_piece, numero, piece_id, bloque, lignes, colonnes, c, b, largeur_utile, hauteur_utile, critere):
    """(trame, ligne, colonne) de la position libre qui descend le moins loin, ou None. L'occupation ne faisant que croître, une
    orientation sans place sur cette feuille est mémorisée et plus jamais recalculée."""
    if feuille["fft"] is None:
        feuille["fft"] = np.fft.rfft2(feuille["occ"])
    meilleur, score_min = None, None
    for t in trames_piece:
        cle = (numero, piece_id, t.angle)
        if cle in bloque:
            continue
        corr = np.fft.irfft2(feuille["fft"] * t.fft, s=(lignes, colonnes))
        # positions où la pièce reste dans la zone utile (et sa trame dans la grille)
        max_i = min(lignes - t.lignes, int((hauteur_utile - t.hauteur) // c))
        max_j = min(colonnes - t.colonnes, int((largeur_utile - t.largeur) // c))
        if max_i < 0 or max_j < 0:
            bloque.add(cle)
            continue
        libre = corr[: max_i + 1, : max_j + 1] < 0.5
        if not libre.any():
            bloque.add(cle)
            continue
        ii, jj = np.indices(libre.shape)
        rang = (ii + t.lignes) if critere == "bord" else ii
        score = np.where(libre, rang * colonnes + jj, np.iinfo(np.int64).max)
        indice = np.unravel_index(int(np.argmin(score)), score.shape)
        valeur = int(score[indice])
        if score_min is None or valeur < score_min:
            meilleur, score_min = (t, int(indice[0]), int(indice[1])), valeur
    return meilleur


def _controler(placements, base, largeur_feuille_mm, longueur_feuille_mm, marge, espacement):
    """Contrôle exact avec les contours réels : dans la zone utile, et au moins `espacement` entre deux pièces d'une feuille."""
    par_feuille = {}
    for p in placements:
        g = base[p.piece_id]
        g = rotate(g, p.rotation_deg, origin=(0, 0)) if p.rotation_deg else g
        minx, miny, _, _ = g.bounds
        g = translate(g, xoff=-minx + p.x_mm, yoff=-miny + p.y_mm)
        minx, miny, maxx, maxy = g.bounds
        tolerance = 1e-3
        if minx < marge - tolerance or miny < marge - tolerance or maxx > largeur_feuille_mm - marge + tolerance or maxy > longueur_feuille_mm - marge + tolerance:
            raise ImbricationFormeImpossible("pièce hors de la zone utile")
        par_feuille.setdefault(p.numero_feuille, []).append(g)
    for geometries in par_feuille.values():
        arbre = STRtree(geometries)
        for k, g in enumerate(geometries):
            for autre in arbre.query(g.buffer(espacement + 1.0)):
                if autre != k and g.distance(geometries[autre]) < espacement - 1e-3:
                    raise ImbricationFormeImpossible("pièces trop proches")


def _occuper(occ, polygone, x, y, b, xs_grille, ys_grille):
    """Ajoute à l'occupation la trame (élargie de `b`) de la pièce posée avec son coin bas-gauche en (x, y)."""
    minx, miny, maxx, maxy = polygone.bounds
    elargi = translate(polygone, xoff=x, yoff=y).buffer(b)
    bx0, by0, bx1, by1 = elargi.bounds
    j0, j1 = np.searchsorted(xs_grille, bx0 - 1e-9), np.searchsorted(xs_grille, bx1 + 1e-9)
    i0, i1 = np.searchsorted(ys_grille, by0 - 1e-9), np.searchsorted(ys_grille, by1 + 1e-9)
    if j1 <= j0 or i1 <= i0:
        return
    X, Y = np.meshgrid(xs_grille[j0:j1], ys_grille[i0:i1])
    occ[i0:i1, j0:j1] += shapely.contains_xy(elargi, X, Y)


def _tasser(simplifie, x, y, posees, bornes, zone, espacement):
    """Fait glisser la pièce (contour simplifié, coin bas-gauche en (x, y)) vers le bas puis vers la gauche tant qu'elle reste dans
    la zone (xmin, ymin, xmax, ymax) et à plus de `espacement` de ses voisines. Marge de sécurité de la simplification comprise."""
    minimum = espacement + 2 * TOLERANCE_SIMPLIFICATION_MM
    largeur, hauteur = simplifie.bounds[2], simplifie.bounds[3]

    tableau = np.asarray(bornes, dtype=float).reshape(-1, 4)  # (xmin, ymin, xmax, ymax) des pièces déjà posées

    def possible(px, py):
        if px < zone[0] - 1e-9 or py < zone[1] - 1e-9 or px + largeur > zone[2] + 1e-9 or py + hauteur > zone[3] + 1e-9:
            return False
        if not len(tableau):
            return True
        # Seules les voisines dont le rectangle englobant touche la zone élargie sont comparées exactement (filtre vectorisé).
        proches = np.nonzero(~((tableau[:, 2] < px - minimum) | (tableau[:, 0] > px + largeur + minimum)
                               | (tableau[:, 3] < py - minimum) | (tableau[:, 1] > py + hauteur + minimum)))[0]
        if not len(proches):
            return True
        g = translate(simplifie, xoff=px, yoff=py)
        return all(g.distance(posees[k]) >= minimum for k in proches)

    for _ in range(3):
        bouge = False
        for ux, uy in ((0.0, -1.0), (-1.0, 0.0)):
            for pas in PAS_TASSEMENT_MM:
                for _tour in range(8):
                    if possible(x + ux * pas, y + uy * pas):
                        x, y, bouge = x + ux * pas, y + uy * pas, True
                    else:
                        break
        if not bouge:
            break
    return x, y
