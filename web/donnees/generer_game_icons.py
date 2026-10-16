"""Génère web/donnees/game_icons.json, la bibliothèque de formes décoratives du configurateur, depuis le jeu
« game-icons » (https://game-icons.net, licence CC BY 3.0) du paquet npm @iconify/json.

Ne garde que les formes découpables d'un seul tenant : un seul morceau de matière, des jours (trous) permis, aucun îlot.
Pour chaque forme, calcule le rapport épaisseur / largeur le plus grand qui respecte la règle de l'atelier « aucun trait
ni jour plus fin que l'épaisseur de la tôle » : la largeur mini pour une épaisseur e est e / rapport.

Hors exécution de l'ERP (outil de préparation) :
    npm pack @iconify/json && tar xzf iconify-json-*.tgz package/json/game-icons.json
    pip install svgelements
    python web/donnees/generer_game_icons.py package/json/game-icons.json web/donnees/noms_game_icons.json
Le second fichier (facultatif) traduit les noms : {"anchor": "Ancre", ...}."""

import json
import math
import multiprocessing
import re
import sys
from pathlib import Path

LARGEUR = 1000.0  # largeur de référence des contours enregistrés (unités = mm à l'échelle 1000 mm)
PAS = 0.4  # longueur des segments qui remplacent les courbes, en unités de l'icône (grille 512)
TOLERANCE = 0.1  # simplification des contours, en unités de référence (0,1 mm pour 1 m de large)
RESOLUTION = 10  # contours enregistrés en dixièmes d'unité de référence (entiers)
RAPPORT_MAX = 0.2  # au-delà, la forme accepterait une épaisseur de plus du cinquième de sa largeur : inutile de chercher
EXCLUES = {"suicide"}  # sujets déplacés sur un site marchand
POINTE = 5.0  # surface tolérée (en épaisseurs au carré) de chaque bout de matière ôté ou ajouté : laisse passer pointes et coins


def boucles(d):
    """Sous-chemins d'un attribut d SVG, aplatis en listes de points (unités de l'icône)."""
    from svgelements import Path as CheminSvg

    resultat = []
    for sous in CheminSvg(d).as_subpaths():
        chemin = CheminSvg(sous)
        longueur = chemin.length(error=1e-3)
        if not longueur:
            continue
        n = max(12, math.ceil(longueur / PAS))
        points = [chemin.point(i / n) for i in range(n)]
        resultat.append([(p.x, p.y) for p in points])
    return resultat


def remplissage_nonzero(anneaux):
    """Surface remplie par des boucles selon la règle SVG « nonzero » (celle des icônes) : on découpe le plan en faces
    par les boucles et on garde les faces dont le nombre d'enroulement n'est pas nul."""
    from shapely.geometry import LinearRing, Polygon
    from shapely.ops import polygonize, unary_union

    polys, sens = [], []
    for a in anneaux:
        if len(a) < 3:
            continue
        p = Polygon(a)
        if not p.is_valid:
            p = p.buffer(0)
        if p.is_empty or p.area < 1e-6:
            continue
        polys.append(p)
        sens.append(1 if LinearRing(a).is_ccw else -1)
    lignes = unary_union([LinearRing(a) for a in anneaux if len(a) >= 3])
    faces = []
    for face in polygonize(lignes):
        point = face.representative_point()
        enroulement = sum(s for p, s in zip(polys, sens) if p.contains(point))
        if enroulement:
            faces.append(face)
    return unary_union(faces).buffer(0)


def normaliser(geo):
    """Largeur ramenée à LARGEUR, origine en bas à gauche, axe Y vers le haut (le SVG l'a vers le bas)."""
    from shapely import affinity

    minx, miny, maxx, maxy = geo.bounds
    k = LARGEUR / (maxx - minx)
    geo = affinity.translate(geo, -minx, -maxy)
    return affinity.scale(geo, k, -k, origin=(0, 0))


def nb_trous(geo):
    return sum(len(p.interiors) for p in getattr(geo, "geoms", [geo]))


def nb_morceaux(geo):
    return len(getattr(geo, "geoms", [geo])) if not geo.is_empty else 0


def respecte(geo, epaisseur):
    """Règle « aucun trait ni jour plus fin que l'épaisseur » : l'ouverture (érosion puis dilatation d'une demi-épaisseur)
    efface les traits plus fins, la fermeture bouche les jours plus étroits. La forme passe si aucune des deux ne change
    sa topologie (un morceau, mêmes trous) et si chaque bout effacé ou bouché reste petit (au plus POINTE × e²) : une
    pointe ou un coin rentrant perd un peu de matière, un trait fin ou une fente étroite en perd beaucoup."""
    r = epaisseur / 2
    limite = POINTE * epaisseur ** 2
    ouverte = geo.buffer(-r, join_style="round").buffer(r, join_style="round")
    if nb_morceaux(ouverte) != 1 or nb_trous(ouverte) != nb_trous(geo):
        return False
    if any(m.area > limite for m in _morceaux(geo.difference(ouverte))):
        return False
    fermee = geo.buffer(r, join_style="round").buffer(-r, join_style="round")
    if nb_morceaux(fermee) != 1 or nb_trous(fermee) != nb_trous(geo):
        return False
    return not any(m.area > limite for m in _morceaux(fermee.difference(geo)))


def _morceaux(geo):
    return [] if geo.is_empty else list(getattr(geo, "geoms", [geo]))


def rapport_maxi(geo):
    """Plus grand rapport épaisseur / largeur accepté (recherche par dichotomie, à 1 % près), 0 si même un trait de
    0,05 % de la largeur ne passe pas (morceaux qui ne se touchent qu'en un point, par exemple)."""
    bas = 0.0005
    if not respecte(geo, bas * LARGEUR):
        return 0
    haut = RAPPORT_MAX
    if respecte(geo, haut * LARGEUR):
        return haut
    while haut / bas > 1.01:
        milieu = math.sqrt(bas * haut)
        if respecte(geo, milieu * LARGEUR):
            bas = milieu
        else:
            haut = milieu
    return bas


def coder(anneau):
    """Anneau → chaîne compacte : coordonnées entières (1/RESOLUTION d'unité), en écarts depuis le point précédent."""
    valeurs, px, py = [], 0, 0
    for x, y in list(anneau.coords)[:-1]:
        ix, iy = round(x * RESOLUTION), round(y * RESOLUTION)
        if (ix, iy) == (px, py) and valeurs:
            continue
        valeurs += [ix - px, iy - py]
        px, py = ix, iy
    return " ".join(map(str, valeurs))


def apercu(geo):
    """Chemin SVG léger pour les vignettes (viewBox 0 0 100 h, axe Y vers le bas)."""
    from shapely import affinity

    g = affinity.scale(geo.simplify(4), 0.1, -0.1, origin=(0, 0))
    g = affinity.translate(g, 0, -g.bounds[1])
    morceaux = []
    for anneau in [g.exterior, *g.interiors]:
        pts = list(anneau.coords)[:-1]
        morceaux.append("M" + "L".join(f"{x:.1f} {y:.1f}" for x, y in pts) + "Z")
    return "".join(morceaux)


def traiter(article):
    cle, corps = article
    from shapely.geometry.polygon import orient

    anneaux = []
    for d in re.findall(r'\sd="([^"]+)"', corps):
        anneaux += boucles(d)
    if not anneaux:
        return cle, None, "vide"
    geo = remplissage_nonzero(anneaux)
    geo = geo.buffer(0)
    if geo.is_empty:
        return cle, None, "vide"
    # miettes d'aplatissement (moins d'un millionième de la surface) écartées avant de compter les morceaux
    if geo.geom_type == "MultiPolygon":
        total = geo.area
        gros = [p for p in geo.geoms if p.area > total * 1e-6]
        if len(gros) != 1:
            return cle, None, "plusieurs morceaux"
        geo = gros[0]
    if geo.geom_type != "Polygon":
        return cle, None, "géométrie"
    geo = normaliser(geo)
    geo = orient(geo.simplify(TOLERANCE, preserve_topology=True), 1.0)
    rapport = rapport_maxi(geo)
    if not rapport:
        return cle, None, "morceaux reliés par un point ou détails trop fins"
    minx, miny, maxx, maxy = geo.bounds
    return cle, {
        "cle": cle,
        "hauteur": round(maxy - miny, 1),
        "rapport": round(rapport, 5),
        "contour": [coder(geo.exterior)] + [coder(i) for i in geo.interiors],
        "apercu": apercu(geo),
    }, "ok"


def main(source, noms=None):
    donnees = json.loads(Path(source).read_text())
    traductions = json.loads(Path(noms).read_text()) if noms else {}
    icones = sorted((cle, i["body"]) for cle, i in donnees["icons"].items() if not i.get("hidden") and cle not in EXCLUES)
    with multiprocessing.Pool() as pool:
        resultats = pool.map(traiter, icones, chunksize=8)
    formes, motifs = [], {}
    for cle, forme, motif in resultats:
        motifs[motif] = motifs.get(motif, 0) + 1
        if forme:
            forme["nom"] = traductions.get(cle) or cle.replace("-", " ").capitalize()
            formes.append(forme)
    info = donnees["info"]
    sortie = {
        "source": "game-icons.net",
        "url": "https://game-icons.net",
        "licence": info["license"]["title"],
        "licence_url": "https://creativecommons.org/licenses/by/3.0/",
        "credit": "Formes issues de game-icons.net (Lorc, Delapouite et contributeurs), licence CC BY 3.0.",
        "largeur": LARGEUR,
        "resolution": RESOLUTION,
        "formes": formes,
    }
    cible = Path(__file__).with_name("game_icons.json")
    cible.write_text(json.dumps(sortie, ensure_ascii=False, separators=(",", ":")))
    print(f"{len(icones)} icônes lues : {motifs} ; {len(formes)} formes écrites dans {cible}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
