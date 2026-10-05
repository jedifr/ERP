"""Coût matière d'une pièce découpée dans une tôle, par imbrication.

Principe : pour une quantité donnée, les pièces sont imbriquées dans le format de tôle retenu (`imbriquer_meilleur`).
- **surface consommée** : les feuilles entières sauf la dernière, dont on ne compte que la bande réellement entamée (le
  reste est une chute réutilisable, rendue au stock) ;
- **chutes** = surface consommée − surface des pièces (découpes, vides entre pièces, marges) ;
- **surface facturée** = surface des pièces + chutes × (1 − part de chute récupérable) : la chute réutilisée ailleurs
  n'est pas imputée à la pièce ;
- **coût** = surface facturée × prix de la tôle au mm² (tôle vendue au m², au kg — épaisseur et densité — ou à la feuille).

Le coût par pièce dépend de la quantité : 10 pièces coûtent plus cher l'unité que 500 (la dernière feuille pèse moins)."""

import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal

from comptes.montants import D, ZERO, arrondir, arrondir_prix
from technique.models import Article

from .imbrication import ItemANester, etendue_derniere_feuille_mm, imbriquer_meilleur

_CACHE = {}
_CACHE_MAX = 256


class ErreurMatiere(Exception):
    """Calcul de matière impossible (pièce non importée, tôle sans prix, pièce trop grande pour la tôle…)."""


@dataclass
class CoutMatiere:
    quantite: int
    largeur_mm: float
    longueur_mm: float
    nb_feuilles: int
    taux_utilisation_pct: float
    surface_pieces_mm2: float
    surface_consommee_mm2: float
    surface_facturee_mm2: float
    cout_total: Decimal
    cout_unitaire: Decimal
    placements: list = field(default_factory=list)
    avertissements: list = field(default_factory=list)


def espacement_pieces_mm(piece):
    """Écart entre pièces à l'imbrication : le plus grand de l'intervalle du paramètre de coupe (qui croît avec l'épaisseur
    au laser) et de l'écart minimal du procédé (jet d'eau : 6 mm, donc constant tant qu'aucun paramètre ne demande plus ;
    laser : 10 mm au moins)."""
    from .parametres import meilleur_parametre, reglage

    espacement = float(reglage(piece.procede).espacement_minimum_mm)
    if piece.matiere_id and piece.epaisseur:
        parametre = meilleur_parametre(piece.matiere, piece.epaisseur, piece.procede, piece.gaz_coupe if piece.procede == "laser" else "")
        if parametre:
            espacement = max(espacement, float(parametre.intervalle_pieces_mm))
    return espacement


def prix_au_mm2(tole, largeur_mm, longueur_mm):
    """Prix de la tôle au mm² (Decimal), selon son unité de coût."""
    if tole.cout_unitaire is None:
        raise ErreurMatiere(f"La tôle « {tole} » n'a pas de coût unitaire renseigné.")
    cout = D(tole.cout_unitaire)
    if tole.unite_cout == Article.UniteCout.SURFACE:
        return cout / D(1_000_000)  # € / m²
    if tole.unite_cout == Article.UniteCout.POIDS:
        if not tole.epaisseur or not tole.matiere_id:
            raise ErreurMatiere(f"Épaisseur et matière requises pour la tôle « {tole} » (unité : poids).")
        kg_par_mm2 = D(tole.epaisseur) * D(tole.matiere.densite) / D(1_000_000)  # épaisseur (mm) × densité (kg/dm³) → kg par mm²
        return cout * kg_par_mm2
    if tole.unite_cout == Article.UniteCout.PIECE:
        return cout / D(largeur_mm * longueur_mm)  # prix de la feuille entière, réparti sur sa surface
    raise ErreurMatiere(f"Unité de coût non adaptée à une tôle pour « {tole} » (surface, poids ou pièce).")


def _empreinte(piece, quantite, largeur, longueur, espacement):
    contour = json.dumps(piece.contour_json, sort_keys=True)
    brut = f"{piece.pk}|{quantite}|{largeur}|{longueur}|{piece.marge_bord_mm}|{espacement}|{piece.pas_rotation_deg}|{piece.surface_mm2}|{contour}"
    return hashlib.sha1(brut.encode()).hexdigest()


def imbriquer_piece(piece, quantite, largeur_mm, longueur_mm):
    """Imbrication de `quantite` pièces dans une tôle (mise en cache : même pièce, même quantité, même format)."""
    if piece.statut != piece.Statut.OK or not piece.contour_json or not piece.surface_mm2:
        raise ErreurMatiere("La géométrie de la pièce n'est pas encore importée.")
    espacement = espacement_pieces_mm(piece)
    cle = _empreinte(piece, quantite, largeur_mm, longueur_mm, espacement)
    if cle not in _CACHE:
        item = ItemANester(
            piece_id=piece.pk or 0, largeur_mm=piece.largeur_mm, hauteur_mm=piece.hauteur_mm, surface_mm2=piece.surface_mm2,
            quantite=int(quantite), pas_rotation_deg=piece.pas_rotation_deg, symetrie_autorisee=piece.symetrie_autorisee,
            exterieur=(piece.contour_json or {}).get("exterieur") or [],
        )
        resultat = imbriquer_meilleur(
            [item], largeur_mm, longueur_mm, marge_bord_mm=piece.marge_bord_mm, espacement_pieces_mm=espacement
        )
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[cle] = resultat
    return _CACHE[cle]


def cout_matiere_imbrication(piece, quantite, tole=None, format_tole=None, taux_chute_recuperable=None, avec_placements=False):
    """Coût matière de `quantite` pièces (voir le module). Par défaut la tôle, le format et le taux de chute récupérable
    sont ceux retenus sur la pièce."""
    tole = tole or piece.tole
    format_tole = format_tole or piece.format_tole
    if tole is None or format_tole is None:
        raise ErreurMatiere("Choisissez la tôle et son format (simulation d'imbrication) pour chiffrer la matière.")
    quantite = int(quantite)
    if quantite <= 0:
        raise ErreurMatiere("La quantité doit être positive.")
    taux = D(piece.taux_chute_recuperable if taux_chute_recuperable is None else taux_chute_recuperable)
    resultat = imbriquer_piece(piece, quantite, format_tole.largeur_mm, format_tole.longueur_mm)
    if resultat.pieces_non_placees:
        raise ErreurMatiere(f"La pièce est trop grande pour la tôle {format_tole} (marges de bord comprises).")

    surface_feuille = format_tole.largeur_mm * format_tole.longueur_mm
    entieres = max(resultat.nb_feuilles - 1, 0)
    etendue = min(etendue_derniere_feuille_mm(resultat, piece.marge_bord_mm), format_tole.longueur_mm)
    consommee = entieres * surface_feuille + format_tole.largeur_mm * etendue
    consommee = min(consommee, resultat.nb_feuilles * surface_feuille)
    chutes = max(consommee - resultat.surface_pieces_mm2, 0.0)
    facturee = resultat.surface_pieces_mm2 + chutes * float(D(1) - taux / 100)
    prix = prix_au_mm2(tole, format_tole.largeur_mm, format_tole.longueur_mm)
    total = arrondir(D(facturee) * prix)
    return CoutMatiere(
        quantite=quantite, largeur_mm=format_tole.largeur_mm, longueur_mm=format_tole.longueur_mm, nb_feuilles=resultat.nb_feuilles,
        taux_utilisation_pct=resultat.taux_utilisation_pct, surface_pieces_mm2=resultat.surface_pieces_mm2,
        surface_consommee_mm2=consommee, surface_facturee_mm2=facturee, cout_total=total,
        cout_unitaire=arrondir_prix(total / D(quantite)), placements=resultat.placements if avec_placements else [],
    )


def piece_de_chiffrage(article):
    """Pièce à découper dont l'imbrication sert au coût matière de l'article fabriqué (ou None)."""
    from decoupe.models import PieceDecoupe

    if article.nature != Article.Nature.FABRIQUE:
        return None
    return (
        PieceDecoupe.objects.filter(
            article=article, imbrication_chiffrage=True, tole__isnull=False, format_tole__isnull=False, statut=PieceDecoupe.Statut.OK
        )
        .select_related("tole", "format_tole")
        .first()
    )
