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
    items, largeur_feuille_mm, longueur_feuille_mm, marge_bord_mm=0.0, espacement_pieces_mm=0.0
):
    """Place les `items` (avec leur quantité) sur autant de feuilles que nécessaire.

    `items` : itérable de `ItemANester`. Les pièces trop grandes pour tenir sur une feuille
    vide (même seules, sous quelque orientation autorisée que ce soit) sont écartées et
    listées dans `pieces_non_placees`.
    """
    largeur_utile = largeur_feuille_mm - 2 * marge_bord_mm
    hauteur_utile = longueur_feuille_mm - 2 * marge_bord_mm

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
                position = feuille.placer(largeur, hauteur, espacement_pieces_mm)
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
            feuille = _Feuille(largeur_utile, hauteur_utile)
            feuilles.append(feuille)
            # Une feuille neuve peut toujours accueillir la première orientation candidate,
            # puisque celle-ci a déjà été validée contre la zone utile complète.
            largeur, hauteur, rotation = options[0]
            x, y = feuille.placer(largeur, hauteur, espacement_pieces_mm)
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
