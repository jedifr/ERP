"""Imbrication des pièces d'un devis : toutes les pièces d'une même matière, épaisseur et procédé se placent ensemble sur les
mêmes tôles (une imbrication par groupe), avec comparaison des formats de tôle et répartition du coût matière.

Règles de coût (comme pour une pièce seule, voir matiere.py) : surface consommée = feuilles entières + bande entamée de la
dernière feuille ; chutes = consommée − surface des pièces ; surface facturée = pièces + chutes × (1 − part récupérable) ;
coût = surface facturée × prix de la tôle au mm². Le coût du lot est réparti entre les pièces au prorata de leur surface."""

import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal

from comptes.montants import D, arrondir, arrondir_prix
from technique.models import Article

from ..models import FormatTole
from .imbrication import ItemANester, etendue_derniere_feuille_mm, imbriquer_meilleur
from .matiere import ErreurMatiere, espacement_pieces_mm, prix_au_mm2
from .parametres import format_compatible
from .temps import ErreurTemps, parametre_pour

_CACHE = {}
_CACHE_MAX = 128
COULEURS = [("#fed7aa", "#9a3412"), ("#bfdbfe", "#1e40af"), ("#bbf7d0", "#166534"), ("#fbcfe8", "#9d174d"), ("#ddd6fe", "#5b21b6"),
            ("#fef08a", "#854d0e"), ("#a5f3fc", "#155e75"), ("#e5e7eb", "#374151")]


@dataclass
class Groupe:
    matiere: object
    epaisseur: float
    procede: str
    pieces: list = field(default_factory=list)
    exclues: list = field(default_factory=list)  # (pièce, raison)

    @property
    def cle(self):
        return f"{self.matiere.pk}|{self.epaisseur:g}|{self.procede}"

    @property
    def libelle(self):
        procede = "laser" if self.procede == "laser" else "jet d'eau"
        return f"{self.matiere.nom} · {self.epaisseur:g} mm · {procede}"


@dataclass
class ResultatGroupe:
    format: object
    nb_feuilles: int
    taux_utilisation_pct: float
    surface_pieces_mm2: float
    surface_consommee_mm2: float
    surface_facturee_mm2: float
    cout_total: Decimal | None
    par_piece: list
    placements: list
    espacement_mm: float

    @property
    def surface_consommee_m2(self):
        return self.surface_consommee_mm2 / 1_000_000


def grouper(pieces):
    """Groupes (matière, épaisseur, procédé) des pièces importées et réglées ; une pièce sans géométrie, sans matière ou
    épaisseur, ou non réalisable (laser hors base) est mise à part avec sa raison."""
    groupes, a_regler = {}, []
    for piece in pieces:
        if piece.statut != piece.Statut.OK or not piece.contour_json or not piece.surface_mm2:
            a_regler.append((piece, "géométrie non importée"))
            continue
        if not piece.matiere_id or not piece.epaisseur:
            a_regler.append((piece, "matière ou épaisseur à choisir"))
            continue
        cle = (piece.matiere_id, float(piece.epaisseur), piece.procede)
        groupe = groupes.setdefault(cle, Groupe(piece.matiere, float(piece.epaisseur), piece.procede))
        try:
            parametre_pour(piece)
        except ErreurTemps as exc:
            groupe.exclues.append((piece, str(exc)))
            continue
        groupe.pieces.append(piece)
    return list(groupes.values()), a_regler


def toles_possibles(groupe):
    """Tôles (articles matière première) de la matière — ou de sa famille — et de l'épaisseur du groupe."""
    matieres = {groupe.matiere.pk}
    if groupe.matiere.famille_id:
        matieres |= set(groupe.matiere.famille.nuances.values_list("pk", flat=True))
    candidates = Article.objects.filter(nature=Article.Nature.MATIERE_PREMIERE, epaisseur=groupe.epaisseur, matiere__in=matieres)
    exactes = [t for t in candidates.order_by("reference") if t.matiere_id == groupe.matiere.pk]
    return exactes or list(candidates.order_by("reference"))


def _empreinte(items, largeur, longueur, marge, espacement, forme):
    brut = json.dumps([[i.piece_id, i.quantite, i.largeur_mm, i.hauteur_mm, i.surface_mm2, i.pas_rotation_deg, i.symetrie_autorisee, i.exterieur, i.trous] for i in items])
    return hashlib.sha1(f"{brut}|{largeur}|{longueur}|{marge}|{espacement}|{forme}".encode()).hexdigest()


def imbriquer_groupe(groupe, format_tole, marge_mm, taux_chute, tole=None, quantites=None, forme=True):
    """Imbrication du groupe dans `format_tole` ; ErreurMatiere si une pièce ne tient pas. Sans tôle, pas de coût (surfaces seules).
    `quantites` : {id de pièce: quantité} pour calculer avec d'autres quantités que celles des pièces (ligne de devis modifiée)."""
    quantites = quantites or {}
    if not groupe.pieces:
        raise ErreurMatiere("Aucune pièce réalisable dans ce groupe.")
    compatible, motif = format_compatible(format_tole, groupe.procede)
    if not compatible:
        raise ErreurMatiere(motif)
    espacement = max(espacement_pieces_mm(p) for p in groupe.pieces)
    items = [
        ItemANester(
            piece_id=p.pk, largeur_mm=p.largeur_mm, hauteur_mm=p.hauteur_mm, surface_mm2=p.surface_mm2, quantite=int(quantites.get(p.pk, p.quantite)),
            pas_rotation_deg=p.pas_rotation_deg, symetrie_autorisee=p.symetrie_autorisee, exterieur=(p.contour_json or {}).get("exterieur") or [],
            trous=(p.contour_json or {}).get("trous") or [],
        )
        for p in groupe.pieces
    ]
    cle = _empreinte(items, format_tole.largeur_mm, format_tole.longueur_mm, marge_mm, espacement, forme)
    if cle not in _CACHE:
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[cle] = imbriquer_meilleur(items, format_tole.largeur_mm, format_tole.longueur_mm, marge_bord_mm=marge_mm, espacement_pieces_mm=espacement, forme=forme)
    resultat = _CACHE[cle]
    if resultat.pieces_non_placees:
        noms = ", ".join(sorted({p.nom for p in groupe.pieces if p.pk in set(resultat.pieces_non_placees)})) or "une pièce"
        raise ErreurMatiere(f"Ne tient pas sur {format_tole} (marges comprises) : {noms}.")

    surface_feuille = format_tole.largeur_mm * format_tole.longueur_mm
    entieres = max(resultat.nb_feuilles - 1, 0)
    etendue = min(etendue_derniere_feuille_mm(resultat, marge_mm), format_tole.longueur_mm)
    consommee = min(entieres * surface_feuille + format_tole.largeur_mm * etendue, resultat.nb_feuilles * surface_feuille)
    chutes = max(consommee - resultat.surface_pieces_mm2, 0.0)
    facturee = resultat.surface_pieces_mm2 + chutes * float(D(1) - D(taux_chute) / 100)
    cout_total = arrondir(D(facturee) * prix_au_mm2(tole, format_tole.largeur_mm, format_tole.longueur_mm)) if tole is not None else None
    par_piece = []
    for p in groupe.pieces:
        quantite = int(quantites.get(p.pk, p.quantite))
        surface = p.surface_mm2 * quantite
        part = surface / resultat.surface_pieces_mm2 if resultat.surface_pieces_mm2 else 0
        total = arrondir(cout_total * D(part)) if cout_total is not None else None
        par_piece.append({
            "piece": p, "quantite": quantite, "surface_mm2": surface, "total": total,
            "unitaire": arrondir_prix(total / D(quantite)) if total is not None else None,
        })
    return ResultatGroupe(
        format=format_tole, nb_feuilles=resultat.nb_feuilles, taux_utilisation_pct=resultat.taux_utilisation_pct,
        surface_pieces_mm2=resultat.surface_pieces_mm2, surface_consommee_mm2=consommee, surface_facturee_mm2=facturee,
        cout_total=cout_total, par_piece=par_piece, placements=resultat.placements, espacement_mm=espacement,
    )


def comparer_formats(groupe, formats, marge_mm, taux_chute, tole=None, forme=True):
    """[(format, ResultatGroupe | None, message d'erreur)] pour chaque format ; le moins cher (à défaut le moins consommateur) est
    marqué en tête par `meilleur`. Retourne (lignes, format_meilleur)."""
    lignes = []
    for f in formats:
        try:
            lignes.append((f, imbriquer_groupe(groupe, f, marge_mm, taux_chute, tole, forme=forme), ""))
        except ErreurMatiere as exc:
            lignes.append((f, None, str(exc)))
    valides = [r for _, r, _ in lignes if r is not None]
    if not valides:
        return lignes, None
    meilleur = min(valides, key=lambda r: (r.cout_total if r.cout_total is not None else Decimal(0), r.surface_consommee_mm2, r.nb_feuilles))
    return lignes, meilleur.format


def formats_compatibles(procede, formats=None):
    """Formats actifs qui tiennent dans la machine du procédé."""
    return [f for f in (formats if formats is not None else formats_actifs()) if format_compatible(f, procede)[0]]


def formats_actifs():
    return list(FormatTole.objects.filter(actif=True))


def cout_matiere_piece_devis(piece, quantite):
    """Coût matière (Decimal) de `quantite` exemplaires de `piece` dans l'imbrication retenue de son groupe, les autres pièces du
    groupe gardant leur quantité : la ligne de devis d'une pièce n'est donc pas chiffrée comme si elle était seule sur la tôle.
    Lève ErreurMatiere si la pièce n'est pas dans un groupe réalisable ou si tôle et format ne sont pas retenus."""
    from .devis_pieces import pieces_du_devis

    if piece.devis_id is None:
        raise ErreurMatiere("La pièce n'est pas rattachée à un devis.")
    if piece.tole_id is None or piece.format_tole_id is None:
        raise ErreurMatiere("Retenez la tôle et le format de l'imbrication (panneau « Pièces à découper » du devis).")
    compatible, motif = format_compatible(piece.format_tole, piece.procede)
    if not compatible:
        raise ErreurMatiere(f"Le format retenu ({piece.format_tole}) ne convient plus : {motif[0].lower()}{motif[1:]}")
    groupes, _ = grouper(pieces_du_devis(piece.devis))
    groupe = next((g for g in groupes if any(p.pk == piece.pk for p in g.pieces)), None)
    if groupe is None:
        raise ErreurMatiere("La pièce n'est dans aucun groupe d'imbrication réalisable (matière, épaisseur ou procédé à revoir).")
    resultat = imbriquer_groupe(
        groupe, piece.format_tole, piece.marge_bord_mm, float(piece.taux_chute_recuperable), piece.tole, quantites={piece.pk: int(quantite)},
        forme=piece.imbrication_forme,
    )
    return next(r["total"] for r in resultat.par_piece if r["piece"].pk == piece.pk)
