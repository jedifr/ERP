"""Bibliothèque de formes paramétriques : un contour extérieur et des trous calculés à partir de cotes (mm), sans fichier DXF.

Chaque famille déclare ses paramètres (libellé, valeur par défaut, minimum) et un constructeur. Le contour est exprimé en
polyligne dans un repère dont l'origine est le coin bas-gauche du rectangle englobant ; les arcs sont discrétisés avec une
flèche de 0,05 mm. `construire()` valide les cotes (trous dans la pièce, pas de recoupement…) avec shapely et lève
ErreurForme avec un message en français ; `dxf_bytes()` écrit le contour en DXF pour la machine ; `parametres_normalises()`
complète les familles « bride EN 1092-1 » et « rondelle » avec les cotes de la table des cotes normalisées (modèle NormeCote)."""

import io
import math
from dataclasses import dataclass, field

from shapely.geometry import Polygon

FLECHE_MM = 0.05


class ErreurForme(Exception):
    """Cotes invalides ou famille inconnue (message affichable)."""


@dataclass
class Parametre:
    cle: str
    libelle: str
    defaut: float
    minimum: float = 0.0
    entier: bool = False
    aide: str = ""
    maximum: float | None = None
    texte: bool = False  # valeur de liste de choix (DN, PN, norme…) et non un nombre


@dataclass
class Famille:
    cle: str
    libelle: str
    description: str
    parametres: list
    constructeur: object = None
    normalisee: bool = False  # les cotes viennent de la table des cotes normalisées
    groupe: str = "Formes simples"


@dataclass
class Contour:
    exterieur: list
    trous: list = field(default_factory=list)

    @property
    def polygone(self):
        return Polygon(self.exterieur, self.trous)

    @property
    def dimensions(self):
        minx, miny, maxx, maxy = Polygon(self.exterieur).bounds
        return maxx - minx, maxy - miny


# ------------------------------------------------------------------ géométrie de base
def _arc(cx, cy, r, a0, a1):
    """Points d'un arc de cercle de centre (cx, cy) de a0 à a1 (degrés, sens trigonométrique), extrémités comprises."""
    ecart = math.radians(a1 - a0)
    pas = 2 * math.acos(max(0.0, 1 - FLECHE_MM / r)) if r > FLECHE_MM else math.pi / 2
    n = max(2, math.ceil(abs(ecart) / pas))
    n += n % 2  # nombre pair : le milieu de l'arc est un point (cotes exactes)
    return [(cx + r * math.cos(math.radians(a0) + ecart * k / n), cy + r * math.sin(math.radians(a0) + ecart * k / n)) for k in range(n + 1)]


def _cercle(cx, cy, r):
    pas = 2 * math.acos(max(0.0, 1 - FLECHE_MM / r)) if r > FLECHE_MM else math.pi / 2
    n = max(16, math.ceil(2 * math.pi / pas))
    n += -n % 4  # multiple de 4 : points aux quatre quadrants (cotes exactes)
    return [(cx + r * math.cos(2 * math.pi * k / n), cy + r * math.sin(2 * math.pi * k / n)) for k in range(n)]


def _normaliser(points):
    """Ramène le contour à une origine en bas à gauche."""
    minx = min(x for x, _ in points)
    miny = min(y for _, y in points)
    return minx, miny


def _decaler(contour_points, dx, dy):
    return [(x + dx, y + dy) for x, y in contour_points]


def _verifier(contour, nom):
    """Contour valide : polygone simple, trous à l'intérieur et séparés."""
    poly = Polygon(contour.exterieur)
    if not poly.is_valid or poly.area <= 0:
        raise ErreurForme(f"{nom} : le contour obtenu n'est pas valide avec ces cotes.")
    trous = [Polygon(t) for t in contour.trous]
    for i, t in enumerate(trous):
        if not t.is_valid or not poly.contains(t):
            raise ErreurForme(f"{nom} : un perçage sort de la pièce ou touche son bord (vérifiez les cotes).")
        if any(t.intersects(autre) for autre in trous[:i]):
            raise ErreurForme(f"{nom} : deux perçages se chevauchent.")
    return contour


def _recaler(contour):
    """Translate le contour pour que le coin bas-gauche de son rectangle englobant soit l'origine."""
    minx, miny = _normaliser(contour.exterieur)
    return Contour(_decaler(contour.exterieur, -minx, -miny), [_decaler(t, -minx, -miny) for t in contour.trous])


# ------------------------------------------------------------------ constructeurs
def _rectangle(v):
    w, h, r, c = v["largeur"], v["hauteur"], v["rayon_angle"], v["chanfrein"]
    if r and c:
        raise ErreurForme("Rectangle : choisissez un rayon d'angle OU un chanfrein, pas les deux.")
    retrait = r or c
    if retrait * 2 > min(w, h) + 1e-9:
        raise ErreurForme("Rectangle : le rayon (ou le chanfrein) dépasse la moitié du plus petit côté.")
    if not retrait:
        return Contour([(0, 0), (w, 0), (w, h), (0, h)])
    pts = []
    for (cx, cy, a0) in ((w - retrait, retrait, -90), (w - retrait, h - retrait, 0), (retrait, h - retrait, 90), (retrait, retrait, 180)):
        if r:
            pts += _arc(cx, cy, r, a0, a0 + 90)
        else:
            pts += [(cx + c * math.cos(math.radians(a0)), cy + c * math.sin(math.radians(a0))),
                    (cx + c * math.cos(math.radians(a0 + 90)), cy + c * math.sin(math.radians(a0 + 90)))]
    return Contour(pts)


def _disque(v):
    r = v["diametre"] / 2
    return Contour(_cercle(r, r, r))


def _anneau(v):
    de, di = v["diametre_ext"], v["diametre_int"]
    if di >= de:
        raise ErreurForme("Anneau : le diamètre intérieur doit être plus petit que le diamètre extérieur.")
    r = de / 2
    return Contour(_cercle(r, r, r), [_cercle(r, r, di / 2)])


def _oblong(v):
    L, l = v["longueur"], v["largeur"]
    if L < l:
        raise ErreurForme("Oblong : la longueur doit être supérieure ou égale à la largeur.")
    r = l / 2
    pts = _arc(L - r, r, r, -90, 90) + _arc(r, r, r, 90, 270)
    return Contour(pts)


def _equerre(v):
    w, h, ta, tb = v["largeur"], v["hauteur"], v["epaisseur_horizontale"], v["epaisseur_verticale"]
    if ta >= h or tb >= w:
        raise ErreurForme("Équerre : les épaisseurs de branche doivent être plus petites que les dimensions de l'équerre.")
    return Contour([(0, 0), (w, 0), (w, ta), (tb, ta), (tb, h), (0, h)])


def _u(v):
    w, h, tf, tb = v["largeur"], v["hauteur"], v["epaisseur_fond"], v["epaisseur_branches"]
    if 2 * tb >= w or tf >= h:
        raise ErreurForme("U : les épaisseurs doivent laisser une ouverture (2 × branche < largeur, fond < hauteur).")
    return Contour([(0, 0), (w, 0), (w, h), (w - tb, h), (w - tb, tf), (tb, tf), (tb, h), (0, h)])


def _trapeze(v):
    gb, pb, h = v["base_grande"], v["base_petite"], v["hauteur"]
    if pb > gb:
        raise ErreurForme("Trapèze : la petite base doit être inférieure ou égale à la grande base.")
    d = (gb - pb) / 2
    return Contour([(0, 0), (gb, 0), (gb - d, h), (d, h)])


def _polygone(v):
    n, d = int(v["nb_cotes"]), v["diametre"]
    if n < 3 or n > 64:
        raise ErreurForme("Polygone : entre 3 et 64 côtés.")
    r = d / 2
    pts = [(r * math.cos(math.pi / 2 + 2 * math.pi * k / n), r * math.sin(math.pi / 2 + 2 * math.pi * k / n)) for k in range(n)]
    return _recaler(Contour(pts))


def _percages_cercle(cx, cy, n, diametre_cercle, diametre_trou, angle_depart):
    rc, rt = diametre_cercle / 2, diametre_trou / 2
    return [_cercle(cx + rc * math.cos(math.radians(angle_depart + 360 * k / n)), cy + rc * math.sin(math.radians(angle_depart + 360 * k / n)), rt) for k in range(int(n))]


def _platine_grille(v):
    base = _rectangle({"largeur": v["largeur"], "hauteur": v["hauteur"], "rayon_angle": v["rayon_angle"], "chanfrein": 0})
    nx, ny, px, py, d = int(v["nb_x"]), int(v["nb_y"]), v["pas_x"], v["pas_y"], v["diametre_trou"]
    w, h = v["largeur"], v["hauteur"]
    x0 = (w - (nx - 1) * px) / 2
    y0 = (h - (ny - 1) * py) / 2
    trous = [_cercle(x0 + i * px, y0 + j * py, d / 2) for i in range(nx) for j in range(ny)]
    return Contour(base.exterieur, trous)


def _platine_cercle(v):
    base = _rectangle({"largeur": v["largeur"], "hauteur": v["hauteur"], "rayon_angle": v["rayon_angle"], "chanfrein": 0})
    trous = _percages_cercle(v["largeur"] / 2, v["hauteur"] / 2, v["nb_trous"], v["diametre_cercle"], v["diametre_trou"], v["angle_depart"])
    return Contour(base.exterieur, trous)


def _bride(v):
    de, di = v["diametre_ext"], v["alesage"]
    r = de / 2
    trous = []
    if di:
        if di >= de:
            raise ErreurForme("Bride : l'alésage doit être plus petit que le diamètre extérieur.")
        trous.append(_cercle(r, r, di / 2))
    if v["nb_trous"]:
        if v["diametre_percage"] + v["diametre_trou"] > de:
            raise ErreurForme("Bride : le cercle de perçage sort de la bride (diamètre extérieur trop petit).")
        if di and v["diametre_percage"] - v["diametre_trou"] < di:
            raise ErreurForme("Bride : les trous chevauchent l'alésage (cercle de perçage trop petit).")
        trous += _percages_cercle(r, r, v["nb_trous"], v["diametre_percage"], v["diametre_trou"], v["angle_depart"])
    return Contour(_cercle(r, r, r), trous)


def _flasque(v):
    de, di = v["diametre"], v["alesage"]
    r = de / 2
    trous = []
    if di:
        if di >= de:
            raise ErreurForme("Flasque : l'alésage doit être plus petit que le diamètre.")
        trous.append(_cercle(r, r, di / 2))
    for suffixe in ("1", "2"):
        n = int(v[f"nb_trous_{suffixe}"])
        if n:
            trous += _percages_cercle(r, r, n, v[f"cercle_{suffixe}"], v[f"diametre_trou_{suffixe}"], v[f"angle_depart_{suffixe}"])
    return Contour(_cercle(r, r, r), trous)


def _bride_en1092(v):
    ligne = cote_normalisee("bride_en1092", f"DN{int(v['dn'])} {v['pn']}")
    t = ligne.valeurs
    type_bride = v.get("type_bride") or "01"
    if type_bride not in ("01", "05"):
        raise ErreurForme("Bride EN 1092-1 : type 01 (plate à souder) ou 05 (pleine).")
    alesage = 0.0 if type_bride == "05" else float(t["tube_od"]) + float(v.get("jeu_alesage") or 0)
    return _bride({
        "diametre_ext": t["D"], "alesage": alesage, "diametre_percage": t["K"], "nb_trous": t["n"], "diametre_trou": t["L"],
        "angle_depart": float(v.get("angle_depart") or 0),
    })


def _rondelle(v):
    ligne = cote_normalisee("rondelle", f"{v['norme']} {v['taille']}")
    return _anneau({"diametre_ext": ligne.valeurs["d2"], "diametre_int": ligne.valeurs["d1"]})


# ------------------------------------------------------------------ cotes normalisées (table NormeCote)
def cote_normalisee(famille, designation):
    from ..models import NormeCote

    ligne = NormeCote.objects.filter(famille=famille, designation=designation).first()
    if ligne is None:
        raise ErreurForme(f"Cote normalisée introuvable : « {designation} ».")
    return ligne


def options_normalisees():
    """Listes de choix des familles normalisées : DN, PN, normes et tailles de rondelles (depuis la table des cotes)."""
    from ..models import NormeCote

    brides = list(NormeCote.objects.filter(famille="bride_en1092").order_by("ordre", "designation"))
    rondelles = list(NormeCote.objects.filter(famille="rondelle").order_by("ordre", "designation"))
    return {
        "dn": sorted({int(l.valeurs["dn"]) for l in brides}),
        "pn": sorted({l.valeurs["pn"] for l in brides}, key=lambda p: int(p[2:])),
        "brides_non_verifiees": any(not l.verifie for l in brides),
        "normes_rondelles": list(dict.fromkeys(l.valeurs["norme"] for l in rondelles)),
        "tailles_rondelles": {norme: [l.valeurs["taille"] for l in rondelles if l.valeurs["norme"] == norme] for norme in dict.fromkeys(l.valeurs["norme"] for l in rondelles)},
    }


# ------------------------------------------------------------------ catalogue
def P(cle, libelle, defaut, minimum=0.0, entier=False, aide="", maximum=None, texte=False):
    return Parametre(cle, libelle, defaut, minimum, entier, aide, maximum, texte)


FAMILLES = {f.cle: f for f in [
    Famille("rectangle", "Rectangle", "Angles vifs, arrondis (rayon) ou chanfreinés.", [
        P("largeur", "Largeur (mm)", 200, 1), P("hauteur", "Hauteur (mm)", 100, 1),
        P("rayon_angle", "Rayon d'angle (mm)", 0, 0, aide="0 : angle vif"), P("chanfrein", "Chanfrein d'angle (mm)", 0, 0, aide="0 : pas de chanfrein"),
    ], _rectangle),
    Famille("disque", "Disque", "Disque plein.", [P("diametre", "Diamètre (mm)", 100, 1)], _disque),
    Famille("anneau", "Anneau", "Disque percé au centre.", [P("diametre_ext", "Diamètre extérieur (mm)", 100, 1), P("diametre_int", "Diamètre intérieur (mm)", 50, 1)], _anneau),
    Famille("oblong", "Oblong", "Deux demi-cercles reliés (forme de stade).", [P("longueur", "Longueur (mm)", 100, 1), P("largeur", "Largeur (mm)", 40, 1)], _oblong),
    Famille("equerre", "Équerre en L", "Équerre à deux branches.", [
        P("largeur", "Largeur (mm)", 200, 1), P("hauteur", "Hauteur (mm)", 150, 1),
        P("epaisseur_horizontale", "Largeur de la branche horizontale (mm)", 40, 1), P("epaisseur_verticale", "Largeur de la branche verticale (mm)", 40, 1),
    ], _equerre),
    Famille("u", "U", "Fond et deux branches.", [
        P("largeur", "Largeur (mm)", 200, 1), P("hauteur", "Hauteur (mm)", 150, 1),
        P("epaisseur_fond", "Largeur du fond (mm)", 40, 1), P("epaisseur_branches", "Largeur des branches (mm)", 40, 1),
    ], _u),
    Famille("trapeze", "Trapèze", "Trapèze isocèle.", [P("base_grande", "Grande base (mm)", 200, 1), P("base_petite", "Petite base (mm)", 100, 0), P("hauteur", "Hauteur (mm)", 100, 1)], _trapeze),
    Famille("polygone", "Polygone régulier", "Polygone régulier, un sommet vers le haut.", [P("nb_cotes", "Nombre de côtés", 6, 3, True, maximum=64), P("diametre", "Diamètre du cercle circonscrit (mm)", 100, 1)], _polygone),
    Famille("platine_grille", "Platine, perçages en grille", "Plaque rectangulaire avec une grille de trous centrée.", [
        P("largeur", "Largeur (mm)", 200, 1), P("hauteur", "Hauteur (mm)", 150, 1), P("rayon_angle", "Rayon d'angle (mm)", 0, 0),
        P("nb_x", "Nombre de trous en largeur", 2, 1, True), P("nb_y", "Nombre de trous en hauteur", 2, 1, True),
        P("pas_x", "Entraxe en largeur (mm)", 150, 0), P("pas_y", "Entraxe en hauteur (mm)", 100, 0), P("diametre_trou", "Diamètre des trous (mm)", 13, 1),
    ], _platine_grille, groupe="Platines et brides"),
    Famille("platine_cercle", "Platine, perçages sur un cercle", "Plaque rectangulaire avec des trous régulièrement répartis sur un cercle centré.", [
        P("largeur", "Largeur (mm)", 200, 1), P("hauteur", "Hauteur (mm)", 200, 1), P("rayon_angle", "Rayon d'angle (mm)", 0, 0),
        P("nb_trous", "Nombre de trous", 4, 1, True), P("diametre_cercle", "Diamètre du cercle de perçage (mm)", 120, 1), P("diametre_trou", "Diamètre des trous (mm)", 13, 1),
        P("angle_depart", "Angle du premier trou (°)", 45, -360, maximum=360),
    ], _platine_cercle, groupe="Platines et brides"),
    Famille("bride", "Bride (cotes libres)", "Disque avec alésage central et cercle de perçage.", [
        P("diametre_ext", "Diamètre extérieur (mm)", 165, 1), P("alesage", "Alésage (mm)", 61, 0, aide="0 : bride pleine"),
        P("diametre_percage", "Diamètre du cercle de perçage (mm)", 125, 1), P("nb_trous", "Nombre de trous", 4, 0, True),
        P("diametre_trou", "Diamètre des trous (mm)", 18, 1), P("angle_depart", "Angle du premier trou (°)", 0, -360, maximum=360),
    ], _bride, groupe="Platines et brides"),
    Famille("flasque", "Flasque", "Disque avec alésage central et jusqu'à deux cercles de perçage.", [
        P("diametre", "Diamètre (mm)", 300, 1), P("alesage", "Alésage central (mm)", 80, 0, aide="0 : pas d'alésage"),
        P("nb_trous_1", "Cercle 1 : nombre de trous", 8, 0, True), P("cercle_1", "Cercle 1 : diamètre de perçage (mm)", 240, 1),
        P("diametre_trou_1", "Cercle 1 : diamètre des trous (mm)", 14, 1), P("angle_depart_1", "Cercle 1 : angle du premier trou (°)", 0, -360, maximum=360),
        P("nb_trous_2", "Cercle 2 : nombre de trous", 0, 0, True, aide="0 : pas de second cercle"), P("cercle_2", "Cercle 2 : diamètre de perçage (mm)", 140, 1),
        P("diametre_trou_2", "Cercle 2 : diamètre des trous (mm)", 10, 1), P("angle_depart_2", "Cercle 2 : angle du premier trou (°)", 0, -360, maximum=360),
    ], _flasque, groupe="Platines et brides"),
    Famille("bride_en1092", "Bride EN 1092-1 (DN / PN)", "Bride plate type 01 (alésage = tube + jeu) ou pleine type 05, cotes de la table normalisée.", [
        P("dn", "DN", 50, 10, True), P("pn", "PN", "PN16", texte=True), P("type_bride", "Type", "01", texte=True, aide="01 : plate à souder, alésage = tube + jeu ; 05 : pleine"),
        P("jeu_alesage", "Jeu d'alésage sur le tube (mm)", 1.0, 0, aide="type 01 : alésage = Ø extérieur du tube + jeu"), P("angle_depart", "Angle du premier trou (°)", 0, -360, maximum=360),
    ], _bride_en1092, normalisee=True, groupe="Cotes normalisées"),
    Famille("rondelle", "Rondelle (ISO)", "Rondelle plate selon ISO 7089 / 7091 / 7093 / 7094.", [
        P("norme", "Norme", "ISO 7089", texte=True), P("taille", "Taille", "M10", texte=True),
    ], _rondelle, normalisee=True, groupe="Cotes normalisées"),
]}


def _valeurs(famille, saisies):
    """Valeurs numériques validées d'une famille ; les paramètres manquants prennent leur valeur par défaut."""
    valeurs = {}
    for p in famille.parametres:
        brut = (saisies or {}).get(p.cle, p.defaut)
        if p.texte:
            valeurs[p.cle] = str(brut).strip()
            continue
        try:
            nombre = float(str(brut).replace(",", ".")) if brut not in (None, "") else float(p.defaut)
        except ValueError:
            raise ErreurForme(f"{famille.libelle} : « {p.libelle} » doit être un nombre.")
        if p.entier:
            if nombre != int(nombre):
                raise ErreurForme(f"{famille.libelle} : « {p.libelle} » doit être un nombre entier.")
            nombre = int(nombre)
        if nombre < p.minimum:
            raise ErreurForme(f"{famille.libelle} : « {p.libelle} » doit être au moins {p.minimum:g}.")
        if p.maximum is not None and nombre > p.maximum:
            raise ErreurForme(f"{famille.libelle} : « {p.libelle} » doit être au plus {p.maximum:g}.")
        valeurs[p.cle] = nombre
    return valeurs


def construire(cle, saisies):
    """Contour (valide, recalé à l'origine) de la famille `cle` avec les cotes `saisies`. Lève ErreurForme."""
    famille = FAMILLES.get(cle)
    if famille is None:
        raise ErreurForme(f"Forme inconnue : « {cle} ».")
    valeurs = _valeurs(famille, saisies)
    if famille.cle == "bride_en1092":
        valeurs["dn"] = int(valeurs["dn"])
    contour = _recaler(famille.constructeur(valeurs))
    return _verifier(contour, famille.libelle)


def nom_suggere(cle, saisies):
    """Nom lisible de la pièce (« Bride DN50 PN16 », « Disque Ø100 »…)."""
    famille = FAMILLES[cle]
    v = _valeurs(famille, saisies)
    g = lambda x: f"{x:g}"
    if cle == "bride_en1092":
        return f"Bride DN{int(v['dn'])} {v['pn']} type {v['type_bride']}"
    if cle == "rondelle":
        return f"Rondelle {v['taille']} {v['norme']}"
    if cle == "disque":
        return f"Disque Ø{g(v['diametre'])}"
    if cle == "anneau":
        return f"Anneau Ø{g(v['diametre_ext'])}/Ø{g(v['diametre_int'])}"
    if cle in ("rectangle", "equerre", "u", "platine_grille", "platine_cercle"):
        return f"{famille.libelle.split(',')[0]} {g(v['largeur'])}×{g(v['hauteur'])}"
    if cle == "oblong":
        return f"Oblong {g(v['longueur'])}×{g(v['largeur'])}"
    if cle == "trapeze":
        return f"Trapèze {g(v['base_grande'])}/{g(v['base_petite'])}×{g(v['hauteur'])}"
    if cle == "polygone":
        return f"Polygone {int(v['nb_cotes'])} côtés Ø{g(v['diametre'])}"
    if cle == "bride":
        return f"Bride Ø{g(v['diametre_ext'])} alésage {g(v['alesage'])}"
    if cle == "flasque":
        return f"Flasque Ø{g(v['diametre'])}"
    return famille.libelle


def dxf_bytes(contour):
    """Contour en DXF (polylignes fermées : extérieur puis trous), lisible par l'import de pièces comme par la machine."""
    import ezdxf

    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    for anneau in [contour.exterieur, *contour.trous]:
        msp.add_lwpolyline([(round(x, 4), round(y, 4)) for x, y in anneau], close=True)
    flux = io.StringIO()
    doc.write(flux)
    return flux.getvalue().encode("utf-8")


def catalogue():
    """Description JSON-able du catalogue pour l'interface (familles, paramètres, listes de choix normalisées)."""
    return {
        "familles": [
            {
                "cle": f.cle, "libelle": f.libelle, "description": f.description, "groupe": f.groupe, "normalisee": f.normalisee,
                "parametres": [
                    {"cle": p.cle, "libelle": p.libelle, "defaut": p.defaut, "minimum": p.minimum, "entier": p.entier, "aide": p.aide, "maximum": p.maximum, "texte": p.texte}
                    for p in f.parametres
                ],
            }
            for f in FAMILLES.values()
        ],
        "normalisees": options_normalisees(),
    }
