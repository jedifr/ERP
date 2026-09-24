"""Imbrication (nesting) rectangulaire de pièces dans des feuilles de dimensions données.

Approche retenue : chaque pièce est représentée par son rectangle englobant, avec rotation
possible par pas de 90° si autorisée (`PieceDecoupe.rotation_autorisee` — à décocher quand le
sens de la matière ou une contrainte d'orientation l'impose). Le compactage utilise un
algorithme d'étagères ("shelf packing", variante Best-Fit Decreasing Height) : simple,
déterministe, et qui se généralise naturellement à plusieurs feuilles.

Ce n'est donc pas une imbrication polygonale exacte (No-Fit-Polygon) : le remplissage réel
serait meilleur avec des pièces pivotées à un angle quelconque et glissées entre les
concavités. Cette approximation par rectangle englobant est cependant standard pour un
chiffrage — et le taux d'utilisation retourné est calculé à partir de la surface réelle des
pièces (issue de leur contour, pas de leur rectangle englobant), ce qui donne une estimation
matière prudente et réaliste malgré l'algorithme de placement simplifié.
"""

from dataclasses import dataclass


@dataclass
class ItemANester:
    piece_id: int
    largeur_mm: float
    hauteur_mm: float
    surface_mm2: float
    rotation_autorisee: bool
    quantite: int


@dataclass
class Placement:
    piece_id: int
    numero_feuille: int
    x_mm: float
    y_mm: float
    largeur_placee_mm: float
    hauteur_placee_mm: float
    rotation_deg: int


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


def _orientations(item, largeur_utile, hauteur_utile):
    """Renvoie les (largeur, hauteur, rotation) sous lesquelles la pièce tient dans une feuille vide."""
    options = []
    if item.largeur_mm <= largeur_utile + 1e-6 and item.hauteur_mm <= hauteur_utile + 1e-6:
        options.append((item.largeur_mm, item.hauteur_mm, 0))
    if (
        item.rotation_autorisee
        and item.hauteur_mm <= largeur_utile + 1e-6
        and item.largeur_mm <= hauteur_utile + 1e-6
    ):
        options.append((item.hauteur_mm, item.largeur_mm, 90))
    return options


def calculer_imbrication(
    items, largeur_feuille_mm, longueur_feuille_mm, marge_bord_mm=0.0, espacement_pieces_mm=0.0
):
    """Place les `items` (avec leur quantité) sur autant de feuilles que nécessaire.

    `items` : itérable de `ItemANester`. Les pièces trop grandes pour tenir sur une feuille
    vide (même seules) sont écartées et listées dans `pieces_non_placees`.
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
            # Une feuille neuve peut toujours accueillir la première orientation valide,
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
