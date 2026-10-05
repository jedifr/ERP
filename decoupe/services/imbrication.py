"""Imbrication (nesting) de pièces dans des feuilles de dimensions données.

Approche retenue : chaque pièce est représentée par le rectangle englobant *de son orientation
candidate* — rotation par pas de 5°/45°/90° selon `PieceDecoupe.pas_rotation_deg`, recalculé à
partir de son contour réel (pas seulement de sa largeur/hauteur à 0°). Le compactage lui-même
reste un algorithme d'étagères ("shelf packing", variante Best-Fit Decreasing Height) : simple,
déterministe, et qui se généralise naturellement à plusieurs feuilles. Pour chaque pièce à
placer, toutes les orientations candidates sont essayées sur l'étagère courante, les plus
compactes (plus petite aire de rectangle englobant) en premier, et la première qui rentre est
retenue.

Ce n'est donc toujours pas une imbrication polygonale exacte (No-Fit-Polygon) : deux pièces ne
peuvent pas s'imbriquer l'une dans les concavités de l'autre, seuls leurs rectangles englobants
respectifs sont comparés. Mais contrairement à la version précédente (limitée à une rotation
fixe 0°/90°), le rectangle englobant est maintenant évalué sous plusieurs angles, ce qui réduit
sensiblement la perte de place sur des pièces non rectangulaires mal orientées dans leur
fichier source. Le taux d'utilisation retourné reste calculé à partir de la surface réelle des
pièces (issue de leur contour, pas de leur rectangle englobant), pour une estimation matière
prudente et réaliste malgré l'algorithme de placement par rectangles englobants.

Pourquoi `symetrie_autorisee` n'influence pas le placement ici : retourner une pièce (miroir)
ne change JAMAIS la largeur ni la hauteur de son rectangle englobant, quelle que soit la forme
— une réflexion est une isométrie qui préserve l'étendue de la pièce sur chaque axe, elle ne
fait que changer le signe des coordonnées. Autrement dit, pour cet algorithme fondé sur des
rectangles englobants, retourner une pièce n'ouvre jamais une possibilité de placement que la
rotation seule n'explorait pas déjà — le moteur n'a donc jamais besoin de retourner une pièce
pour la caser, et ne le fait jamais. La contrainte "pas de symétrie si gravure" est de ce fait
toujours respectée, mais par construction plutôt que par un choix actif du moteur : elle n'aura
d'effet observable sur le nombre de feuilles/le placement que le jour où une imbrication
polygonale exacte (No-Fit-Polygon) remplacera cette approche par rectangles englobants — c'est
seulement là qu'un retournement peut réellement faire gagner de la place en présentant à une
pièce voisine un profil différent. `Placement.miroir` (toujours `False` aujourd'hui) et
`ItemANester.symetrie_autorisee` sont conservés pour cette évolution future, sans être exploités
ici.
"""

from dataclasses import dataclass, field

from shapely.affinity import rotate
from shapely.geometry import Polygon


@dataclass
class ItemANester:
    piece_id: int
    largeur_mm: float
    hauteur_mm: float
    surface_mm2: float
    quantite: int
    pas_rotation_deg: int | None = None
    symetrie_autorisee: bool = True
    exterieur: list = field(default_factory=list)

    def polygone(self):
        """Contour réel si connu, sinon un rectangle synthétique largeur × hauteur — les deux
        cas se traitent alors de façon strictement identique par `_orientations`."""
        if len(self.exterieur) >= 3:
            return Polygon(self.exterieur)
        return Polygon([(0, 0), (self.largeur_mm, 0), (self.largeur_mm, self.hauteur_mm), (0, self.hauteur_mm)])


@dataclass
class Placement:
    piece_id: int
    numero_feuille: int
    x_mm: float
    y_mm: float
    largeur_placee_mm: float
    hauteur_placee_mm: float
    rotation_deg: int
    miroir: bool = False


@dataclass
class ResultatImbrication:
    placements: list
    nb_feuilles: int
    surface_pieces_mm2: float
    surface_feuilles_mm2: float
    taux_utilisation_pct: float
    pieces_non_placees: list


class _Etagere:
    __slots__ = ("y", "hauteur", "x_courant")

    def __init__(self, y, hauteur):
        self.y = y
        self.hauteur = hauteur
        self.x_courant = 0.0


class _Feuille:
    """Empilement d'étagères (lignes) dans la zone utile d'une feuille."""

    def __init__(self, largeur_utile, hauteur_utile):
        self.largeur_utile = largeur_utile
        self.hauteur_utile = hauteur_utile
        self.etageres = []
        self.y_courant = 0.0

    def placer(self, largeur, hauteur, espacement):
        for etagere in self.etageres:
            if etagere.x_courant + largeur <= self.largeur_utile + 1e-6 and hauteur <= etagere.hauteur + 1e-6:
                x, y = etagere.x_courant, etagere.y
                etagere.x_courant += largeur + espacement
                return x, y

        y_nouvelle = self.y_courant + (espacement if self.etageres else 0.0)
        if largeur > self.largeur_utile + 1e-6 or y_nouvelle + hauteur > self.hauteur_utile + 1e-6:
            return None

        etagere = _Etagere(y_nouvelle, hauteur)
        etagere.x_courant = largeur + espacement
        self.etageres.append(etagere)
        self.y_courant = y_nouvelle + hauteur
        return 0.0, y_nouvelle


def _angles_candidats(pas_rotation_deg):
    if not pas_rotation_deg:
        return [0]
    nb = max(1, 360 // pas_rotation_deg)
    return [i * pas_rotation_deg for i in range(nb)]


def _orientations(item, largeur_utile, hauteur_utile):
    """Renvoie les (largeur, hauteur, rotation) de rectangle englobant sous lesquelles la pièce
    tient dans une feuille vide, triées par aire croissante (les plus compactes d'abord —
    heuristique simple pour limiter la place perdue).

    Pas de candidats "miroir" ici : voir le docstring du module — un retournement ne change
    jamais la largeur/hauteur du rectangle englobant, ce serait donc systématiquement écarté
    par la déduplication ci-dessous sans jamais influencer un seul placement."""
    polygone = item.polygone()
    vus = set()
    options = []
    for angle in _angles_candidats(item.pas_rotation_deg):
        g = rotate(polygone, angle, origin=(0, 0)) if angle else polygone
        minx, miny, maxx, maxy = g.bounds
        largeur, hauteur = maxx - minx, maxy - miny
        cle = (round(largeur, 2), round(hauteur, 2))
        if cle in vus:
            continue
        vus.add(cle)
        if largeur <= largeur_utile + 1e-6 and hauteur <= hauteur_utile + 1e-6:
            options.append((largeur, hauteur, angle))
    options.sort(key=lambda o: o[0] * o[1])
    return options


def calculer_imbrication(
    items,
    largeur_feuille_mm,
    longueur_feuille_mm,
    marge_bord_mm=0.0,
    espacement_pieces_mm=0.0,
    direction="horizontal",
    coin_depart="bas_gauche",
):
    """Place les `items` (avec leur quantité) sur autant de feuilles que nécessaire.

    `items` : itérable de `ItemANester`. Les pièces trop grandes pour tenir sur une feuille
    vide (même seules, sous quelque orientation autorisée que ce soit) sont écartées et
    listées dans `pieces_non_placees`.

    `direction` : "horizontal" (rangées remplies horizontalement, empilées verticalement — le
    comportement historique) ou "vertical" (colonnes remplies verticalement, empilées
    horizontalement). L'algorithme d'étagères lui-même ne change pas : en mode vertical, on lui
    présente juste la feuille et chaque pièce avec largeur/hauteur inversées ("une étagère"
    devient alors une colonne), puis on ré-inverse la position obtenue.

    `coin_depart` : coin de la feuille où démarre le placement — "bas_gauche" (par défaut,
    convention machine la plus courante), "bas_droite", "haut_gauche" ou "haut_droite".
    L'algorithme calcule toujours en interne dans un repère canonique (origine en haut à
    gauche) ; `coin_depart` ne fait que réfléchir les coordonnées obtenues selon l'axe
    concerné, une fois le placement calculé.
    """
    largeur_utile = largeur_feuille_mm - 2 * marge_bord_mm
    hauteur_utile = longueur_feuille_mm - 2 * marge_bord_mm
    vertical = direction == "vertical"
    largeur_algo, hauteur_algo = (hauteur_utile, largeur_utile) if vertical else (largeur_utile, hauteur_utile)

    def _placer(feuille, largeur, hauteur):
        """Place une pièce (dimensions réelles largeur × hauteur) sur `feuille` et renvoie sa
        position réelle (x, y), en tenant compte de `direction` et `coin_depart`."""
        position = feuille.placer(hauteur, largeur, espacement_pieces_mm) if vertical else feuille.placer(
            largeur, hauteur, espacement_pieces_mm
        )
        if position is None:
            return None
        a, b = position
        x, y = (b, a) if vertical else (a, b)
        if coin_depart in ("bas_gauche", "bas_droite"):
            y = hauteur_utile - y - hauteur
        if coin_depart in ("bas_droite", "haut_droite"):
            x = largeur_utile - x - largeur
        return x, y

    unites = []
    pieces_non_placees = []
    surface_totale_pieces = 0.0
    for item in items:
        if not _orientations(item, largeur_utile, hauteur_utile):
            pieces_non_placees.append(item.piece_id)
            continue
        unites.extend([item] * item.quantite)
        surface_totale_pieces += item.surface_mm2 * item.quantite

    # Best-Fit Decreasing Height : les plus grandes pièces d'abord, pour limiter la fragmentation.
    unites.sort(key=lambda it: max(it.largeur_mm, it.hauteur_mm), reverse=True)

    feuilles = []
    placements = []

    for item in unites:
        options = _orientations(item, largeur_utile, hauteur_utile)
        place = False
        for numero_feuille, feuille in enumerate(feuilles, start=1):
            for largeur, hauteur, rotation in options:
                position = _placer(feuille, largeur, hauteur)
                if position is not None:
                    x, y = position
                    placements.append(
                        Placement(
                            piece_id=item.piece_id,
                            numero_feuille=numero_feuille,
                            x_mm=marge_bord_mm + x,
                            y_mm=marge_bord_mm + y,
                            largeur_placee_mm=largeur,
                            hauteur_placee_mm=hauteur,
                            rotation_deg=rotation,
                        )
                    )
                    place = True
                    break
            if place:
                break

        if not place:
            feuille = _Feuille(largeur_algo, hauteur_algo)
            feuilles.append(feuille)
            # Une feuille neuve peut toujours accueillir la première orientation candidate,
            # puisque celle-ci a déjà été validée contre la zone utile complète.
            largeur, hauteur, rotation = options[0]
            x, y = _placer(feuille, largeur, hauteur)
            placements.append(
                Placement(
                    piece_id=item.piece_id,
                    numero_feuille=len(feuilles),
                    x_mm=marge_bord_mm + x,
                    y_mm=marge_bord_mm + y,
                    largeur_placee_mm=largeur,
                    hauteur_placee_mm=hauteur,
                    rotation_deg=rotation,
                )
            )

    nb_feuilles = len(feuilles)
    surface_feuilles = nb_feuilles * largeur_feuille_mm * longueur_feuille_mm
    taux = (surface_totale_pieces / surface_feuilles * 100) if surface_feuilles else 0.0

    return ResultatImbrication(
        placements=placements,
        nb_feuilles=nb_feuilles,
        surface_pieces_mm2=surface_totale_pieces,
        surface_feuilles_mm2=surface_feuilles,
        taux_utilisation_pct=taux,
        pieces_non_placees=pieces_non_placees,
    )


# ---------------------------------------------------------------------------------------------------------------------
# Placement plus dense : MaxRects (rectangles libres maximaux), pour le chiffrage de la matière.
#
# Même principe que l'étagère (pièces représentées par le rectangle englobant de leur orientation), mais chaque pièce
# est posée dans le meilleur espace libre de la feuille entière (« best short side fit ») et non seulement dans la
# ligne courante : les creux laissés par les pièces plus petites sont comblés. `imbriquer_meilleur` essaie plusieurs
# variantes (étagères et MaxRects, plusieurs tris) et retient la plus économe — jamais pire que l'étagère seule.
# ---------------------------------------------------------------------------------------------------------------------


class _MaxRects:
    def __init__(self, largeur, hauteur, score="bssf"):
        self.libres = [(0.0, 0.0, largeur, hauteur)]
        self.score = score

    def placer(self, options, espacement):
        """Pose la pièce dans le meilleur rectangle libre parmi ses orientations ; renvoie (x, y, largeur, hauteur, rotation) ou None."""
        meilleur = None
        for largeur, hauteur, rotation in options:
            lw, lh = largeur + espacement, hauteur + espacement
            for (x, y, w, h) in self.libres:
                if lw <= w + 1e-6 and lh <= h + 1e-6:
                    if self.score == "bl":  # « bas-gauche » : on tasse les pièces vers un bord (la feuille se vide d'un seul côté)
                        score = (y + lh, x)
                    else:  # « best short side fit » : espace libre le mieux ajusté
                        score = (min(w - lw, h - lh), max(w - lw, h - lh))
                    if meilleur is None or score < meilleur[0]:
                        meilleur = (score, x, y, lw, lh, largeur, hauteur, rotation)
        if meilleur is None:
            return None
        _, x, y, lw, lh, largeur, hauteur, rotation = meilleur
        self._decouper(x, y, lw, lh)
        return x, y, largeur, hauteur, rotation

    def _decouper(self, px, py, pw, ph):
        nouveaux = []
        for (x, y, w, h) in self.libres:
            if px >= x + w - 1e-9 or px + pw <= x + 1e-9 or py >= y + h - 1e-9 or py + ph <= y + 1e-9:
                nouveaux.append((x, y, w, h))  # pas de chevauchement : inchangé
                continue
            if px > x + 1e-9:
                nouveaux.append((x, y, px - x, h))
            if px + pw < x + w - 1e-9:
                nouveaux.append((px + pw, y, x + w - (px + pw), h))
            if py > y + 1e-9:
                nouveaux.append((x, y, w, py - y))
            if py + ph < y + h - 1e-9:
                nouveaux.append((x, py + ph, w, y + h - (py + ph)))
        # on retire les rectangles entièrement contenus dans un autre
        nouveaux.sort(key=lambda r: -r[2] * r[3])
        gardes = []
        for r in nouveaux:
            if not any(
                g[0] <= r[0] + 1e-9 and g[1] <= r[1] + 1e-9 and g[0] + g[2] >= r[0] + r[2] - 1e-9 and g[1] + g[3] >= r[1] + r[3] - 1e-9
                for g in gardes
            ):
                gardes.append(r)
        self.libres = gardes


def _unites(items, largeur_utile, hauteur_utile):
    unites, non_placees, surface = [], [], 0.0
    for item in items:
        if not _orientations(item, largeur_utile, hauteur_utile):
            non_placees.append(item.piece_id)
            continue
        unites.extend([item] * item.quantite)
        surface += item.surface_mm2 * item.quantite
    return unites, non_placees, surface


def _resultat(placements, nb_feuilles, surface_pieces, largeur_feuille, longueur_feuille, non_placees):
    surface_feuilles = nb_feuilles * largeur_feuille * longueur_feuille
    return ResultatImbrication(
        placements=placements, nb_feuilles=nb_feuilles, surface_pieces_mm2=surface_pieces, surface_feuilles_mm2=surface_feuilles,
        taux_utilisation_pct=(surface_pieces / surface_feuilles * 100) if surface_feuilles else 0.0, pieces_non_placees=non_placees,
    )


def _imbriquer_maxrects(items, largeur_feuille_mm, longueur_feuille_mm, marge_bord_mm, espacement_pieces_mm, tri, score="bssf"):
    largeur_utile = largeur_feuille_mm - 2 * marge_bord_mm
    hauteur_utile = longueur_feuille_mm - 2 * marge_bord_mm
    unites, non_placees, surface = _unites(items, largeur_utile, hauteur_utile)
    cle = (lambda it: -it.largeur_mm * it.hauteur_mm) if tri == "aire" else (lambda it: -max(it.largeur_mm, it.hauteur_mm))
    unites.sort(key=cle)
    options_par_piece = {}
    feuilles, placements = [], []
    for item in unites:
        options = options_par_piece.get(item.piece_id)
        if options is None:
            options = options_par_piece[item.piece_id] = _orientations(item, largeur_utile, hauteur_utile)
        resultat = None
        for numero, feuille in enumerate(feuilles, start=1):
            resultat = feuille.placer(options, espacement_pieces_mm)
            if resultat:
                break
        if not resultat:
            feuille = _MaxRects(largeur_utile + espacement_pieces_mm, hauteur_utile + espacement_pieces_mm, score)
            feuilles.append(feuille)
            numero = len(feuilles)
            resultat = feuille.placer(options, espacement_pieces_mm)
        x, y, largeur, hauteur, rotation = resultat
        placements.append(Placement(
            piece_id=item.piece_id, numero_feuille=numero, x_mm=marge_bord_mm + x, y_mm=marge_bord_mm + y,
            largeur_placee_mm=largeur, hauteur_placee_mm=hauteur, rotation_deg=rotation,
        ))
    return _resultat(placements, len(feuilles), surface, largeur_feuille_mm, longueur_feuille_mm, non_placees)


def etendue_derniere_feuille_mm(resultat, marge_bord_mm=0.0):
    """Hauteur utilisée (bord compris) de la dernière feuille : le reste de la feuille est une chute réutilisable."""
    if not resultat.nb_feuilles:
        return 0.0
    derniere = [p for p in resultat.placements if p.numero_feuille == resultat.nb_feuilles]
    if not derniere:
        return 0.0
    # bande occupée (les pièces sont tassées contre un bord de la feuille) + les deux marges de bord
    return max(p.y_mm + p.hauteur_placee_mm for p in derniere) - min(p.y_mm for p in derniere) + 2 * marge_bord_mm


def imbriquer_meilleur(items, largeur_feuille_mm, longueur_feuille_mm, marge_bord_mm=0.0, espacement_pieces_mm=0.0):
    """Meilleur placement parmi plusieurs variantes : le moins de feuilles, puis la plus petite étendue utilisée sur la
    dernière feuille (ce qui reste d'une feuille entamée se récupère). Toujours au moins aussi bon que l'étagère."""
    candidats = [
        _imbriquer_maxrects(items, largeur_feuille_mm, longueur_feuille_mm, marge_bord_mm, espacement_pieces_mm, tri, score)
        for tri in ("aire", "dimension")
        for score in ("bssf", "bl")
    ]
    candidats.insert(0, calculer_imbrication(items, largeur_feuille_mm, longueur_feuille_mm, marge_bord_mm, espacement_pieces_mm))
    return min(candidats, key=lambda r: (r.nb_feuilles, etendue_derniere_feuille_mm(r, marge_bord_mm)))
