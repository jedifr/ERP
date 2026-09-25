"""Extraction de la géométrie d'une pièce à découper depuis un fichier DXF (ou DWG converti).

Principe : chaque entité géométrique (LINE, LWPOLYLINE/POLYLINE, ARC, CIRCLE, ELLIPSE, SPLINE)
de l'espace objet est discrétisée en segments. Les segments sont regroupés par connexité
(extrémités communes) : chaque groupe fermé forme un anneau simple. Les anneaux imbriqués
(trous, îlots dans les trous...) sont ensuite combinés par profondeur d'imbrication — une
règle pair/impair, comme un remplissage "even-odd" — pour reconstituer la silhouette pleine
de la pièce avec ses trous, quel que soit le nombre de niveaux d'imbrication.

Les références de bloc (INSERT) et les entités non géométriques (texte, cotes, hachures...)
ne sont pas prises en charge et sont ignorées (avec avertissement pour les INSERT).

Rôles de calque : sans profil d'import (`regles_calques=None`), toutes les entités sont
traitées comme de la découpe — comportement historique, inchangé. Avec un profil (un dict
`{nom de calque en minuscules: rôle}`), chaque entité est répartie selon le rôle de son
calque : seules les entités "découpe" alimentent la reconstruction de la silhouette (contour
+ trous, inchangée) ; les entités "gravure"/"pliage" sont extraites à part, comme de simples
tracés (pas de reconstruction de contour fermé — une gravure ou une ligne de pliage n'a pas
de raison d'être une boucle fermée) ; les entités "ignore" sont écartées. Un calque présent
dans le fichier mais absent du profil est traité comme découpe, avec un avertissement (plutôt
que silencieusement ignoré, pour ne pas faire disparaître de la matière à découper).
"""

from dataclasses import dataclass, field

import ezdxf
from shapely.affinity import translate
from shapely.geometry import LineString, Polygon
from shapely.ops import polygonize

PRECISION_MM = 4  # arrondi des coordonnées pour fiabiliser la détection des contours fermés
SAGITTE_MM = 0.05  # tolérance de discrétisation des arcs / cercles / ellipses / splines

ENTITES_GEOMETRIQUES = {"LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "ELLIPSE", "SPLINE"}

ROLE_DECOUPE = "decoupe"
ROLE_GRAVURE = "gravure"
ROLE_PLIAGE = "pliage"
ROLE_IGNORE = "ignore"


class ErreurImportGeometrie(Exception):
    """Levée quand un fichier DXF/DWG ne peut pas être exploité pour en extraire une pièce."""


@dataclass
class GeometrieResultat:
    exterior: list
    holes: list
    surface_mm2: float
    perimetre_mm: float
    largeur_mm: float
    hauteur_mm: float
    avertissements: list = field(default_factory=list)
    gravure: list = field(default_factory=list)
    pliage: list = field(default_factory=list)
    longueur_gravure_mm: float = 0.0
    calques: list = field(default_factory=list)


def _point(vecteur):
    return (round(vecteur.x, PRECISION_MM), round(vecteur.y, PRECISION_MM))


def _vers_lignes(entite, avertissements):
    dxftype = entite.dxftype()
    if dxftype == "LINE":
        return [LineString([_point(entite.dxf.start), _point(entite.dxf.end)])]
    if dxftype in ("LWPOLYLINE", "POLYLINE"):
        lignes = []
        for sous_entite in entite.virtual_entities():
            lignes.extend(_vers_lignes(sous_entite, avertissements))
        return lignes
    if dxftype in ("CIRCLE", "ARC", "ELLIPSE", "SPLINE"):
        points = [_point(p) for p in entite.flattening(SAGITTE_MM)]
        return [LineString(points)] if len(points) >= 2 else []
    if dxftype == "INSERT":
        avertissements.append(
            "Une référence de bloc (INSERT) a été ignorée : les blocs ne sont pas pris en charge, "
            "explosez-les dans votre logiciel de CAO avant export."
        )
    return []


def _role_calque(nom_calque, regles_calques, avertissements):
    if regles_calques is None:
        return ROLE_DECOUPE
    role = regles_calques.get((nom_calque or "").lower())
    if role is None:
        avertissements.append(
            f"Le calque « {nom_calque} » n'est couvert par aucune règle du profil d'import : "
            "traité comme découpe par défaut."
        )
        return ROLE_DECOUPE
    return role


class _UnionFind:
    """Structure union-find minimaliste pour regrouper les segments par extrémités communes."""

    def __init__(self):
        self._parent = {}

    def find(self, x):
        self._parent.setdefault(x, x)
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]
            x = self._parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[ra] = rb


def _contours_fermes(lignes, avertissements):
    """Regroupe les segments par connexité et transforme chaque groupe fermé en anneau(x) simple(s)."""
    uf = _UnionFind()
    for ligne in lignes:
        coords = list(ligne.coords)
        uf.union(coords[0], coords[-1])

    groupes = {}
    for ligne in lignes:
        coords = list(ligne.coords)
        racine = uf.find(coords[0])
        groupes.setdefault(racine, []).append(ligne)

    anneaux = []
    for segments in groupes.values():
        polygones = list(polygonize(segments))
        if not polygones:
            avertissements.append(
                "Un contour non fermé a été ignoré (les extrémités des segments ne se rejoignent pas)."
            )
            continue
        anneaux.extend(polygones)
    return anneaux


def _profondeur(anneau, tous_les_anneaux):
    # Un anneau ne peut être imbriqué que dans un anneau de surface strictement plus grande :
    # cela évite les faux positifs quand le point représentatif d'un grand anneau tombe,
    # par coïncidence géométrique, à l'intérieur d'un petit anneau qu'il contient lui-même.
    return sum(
        1
        for autre in tous_les_anneaux
        if autre is not anneau and autre.area > anneau.area and autre.contains(anneau.representative_point())
    )


def _assembler_silhouette(anneaux):
    profondeurs = [(anneau, _profondeur(anneau, anneaux)) for anneau in anneaux]
    # Traitement dans l'ordre des profondeurs croissantes : chaque anneau est retranché ou
    # rajouté au résultat courant (et non combiné en vrac), pour que les îlots situés dans un
    # trou soient bien restitués.
    profondeurs.sort(key=lambda t: t[1])
    resultat = Polygon()
    for anneau, profondeur in profondeurs:
        resultat = resultat.union(anneau) if profondeur % 2 == 0 else resultat.difference(anneau)
    return resultat


def _extraire_polygone_principal(geometrie, avertissements):
    if geometrie.is_empty:
        raise ErreurImportGeometrie("Aucun contour fermé n'a été trouvé dans le fichier.")

    if geometrie.geom_type == "Polygon":
        return geometrie

    polygones = [g for g in geometrie.geoms if g.area > 1e-6]
    if not polygones:
        raise ErreurImportGeometrie("Aucun contour fermé n'a été trouvé dans le fichier.")

    principal = max(polygones, key=lambda p: p.area)
    ignores = len(polygones) - 1
    if ignores:
        avertissements.append(
            f"Le fichier contient {ignores} contour(s) fermé(s) supplémentaire(s), disjoints de la "
            "silhouette principale ; seule la silhouette de plus grande surface a été retenue comme pièce."
        )
    return principal


def _charger_document(chemin, format_source):
    if format_source == "dwg":
        from ezdxf.addons import odafc

        if not odafc.is_installed():
            raise ErreurImportGeometrie(
                "La lecture des fichiers DWG nécessite l'utilitaire externe 'ODA File Converter' "
                "(Open Design Alliance, gratuit), non installé sur ce serveur. Installez-le et "
                "assurez-vous qu'il est accessible dans le PATH, ou exportez le fichier au format "
                "DXF depuis votre logiciel de CAO."
            )
        try:
            return odafc.readfile(chemin)
        except Exception as exc:  # odafc peut lever plusieurs types d'erreurs selon la plateforme
            raise ErreurImportGeometrie(f"Échec de la conversion DWG → DXF : {exc}") from exc

    try:
        return ezdxf.readfile(chemin)
    except ezdxf.DXFStructureError as exc:
        raise ErreurImportGeometrie(f"Fichier DXF invalide ou corrompu : {exc}") from exc
    except OSError as exc:
        raise ErreurImportGeometrie(f"Impossible de lire le fichier : {exc}") from exc


def _traits_locaux(traits, minx, miny):
    resultat = []
    for ligne in traits:
        decalee = translate(ligne, xoff=-minx, yoff=-miny)
        resultat.append([(round(x, 3), round(y, 3)) for x, y in decalee.coords])
    return resultat


def extraire_geometrie(chemin, format_source, regles_calques=None):
    """Lit un fichier DXF/DWG et renvoie la silhouette pleine (avec trous) de la pièce qu'il contient.

    `format_source` vaut "dxf" ou "dwg". `regles_calques` est optionnel : `None` traite tout le
    fichier comme de la découpe (comportement historique) ; sinon un dict
    `{nom de calque en minuscules: rôle}` (voir le docstring du module) répartit les entités par
    calque. Renvoie un `GeometrieResultat` dont les coordonnées sont exprimées dans un repère
    local à la pièce (origine = coin inférieur gauche du rectangle englobant de la découpe),
    prêt à être réutilisé tel quel pour l'imbrication.
    """
    document = _charger_document(chemin, format_source)
    espace_objet = document.modelspace()

    avertissements = []
    calques_detectes = set()
    lignes_decoupe = []
    traits_gravure = []
    traits_pliage = []
    for entite in espace_objet:
        if entite.dxftype() not in ENTITES_GEOMETRIQUES and entite.dxftype() != "INSERT":
            continue
        nom_calque = entite.dxf.layer
        calques_detectes.add(nom_calque)
        role = _role_calque(nom_calque, regles_calques, avertissements)
        if role == ROLE_IGNORE:
            continue
        lignes = _vers_lignes(entite, avertissements)
        if role == ROLE_GRAVURE:
            traits_gravure.extend(lignes)
        elif role == ROLE_PLIAGE:
            traits_pliage.extend(lignes)
        else:
            lignes_decoupe.extend(lignes)

    if not lignes_decoupe:
        raise ErreurImportGeometrie(
            "Aucune entité géométrique exploitable (ligne, polyligne, arc, cercle...) n'a été trouvée "
            "sur un calque de découpe."
        )

    anneaux = _contours_fermes(lignes_decoupe, avertissements)
    if not anneaux:
        raise ErreurImportGeometrie("Aucun contour fermé n'a été trouvé dans le fichier.")

    silhouette = _assembler_silhouette(anneaux)
    piece = _extraire_polygone_principal(silhouette, avertissements)

    minx, miny, maxx, maxy = piece.bounds
    piece = translate(piece, xoff=-minx, yoff=-miny)

    exterior = [(round(x, 3), round(y, 3)) for x, y in piece.exterior.coords]
    holes = [[(round(x, 3), round(y, 3)) for x, y in interieur.coords] for interieur in piece.interiors]
    perimetre = piece.exterior.length + sum(interieur.length for interieur in piece.interiors)

    return GeometrieResultat(
        exterior=exterior,
        holes=holes,
        surface_mm2=piece.area,
        perimetre_mm=perimetre,
        largeur_mm=maxx - minx,
        hauteur_mm=maxy - miny,
        avertissements=avertissements,
        gravure=_traits_locaux(traits_gravure, minx, miny),
        pliage=_traits_locaux(traits_pliage, minx, miny),
        longueur_gravure_mm=sum(ligne.length for ligne in traits_gravure),
        calques=sorted(calques_detectes),
    )
