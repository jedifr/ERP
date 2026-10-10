"""Prix et poids de matière par pièce d'une ligne de devis fabriquée, pour contrôle.

- prix matière par pièce : coût matière de la ligne (tôle avec sa part de chutes, plus les autres composants) divisé par la quantité ;
- poids net : poids de la pièce découpée (surface × épaisseur × densité) ;
- poids consommé : poids de la tôle consommée par l'imbrication, rapporté à la pièce (chutes entre pièces et chute de bout comprises,
  réparties au prorata de la surface des pièces du groupe) ;
- sans pièce imbriquée (article saisi à la main), poids de la nomenclature (longueur × largeur × épaisseur × densité des composants)."""

from dataclasses import dataclass
from decimal import Decimal

from decoupe.models import PieceDecoupe
from decoupe.services import imbrication_devis as imb
from decoupe.services.matiere import ErreurMatiere, cout_matiere_imbrication, piece_de_chiffrage


@dataclass
class MatierePiece:
    prix: Decimal | None = None  # € par pièce
    poids_net_kg: float | None = None
    poids_consomme_kg: float | None = None
    source: str = ""  # « imbrication » ou « nomenclature »

    @property
    def perte_pct(self):
        if not self.poids_net_kg or self.poids_consomme_kg is None:
            return None
        return max(self.poids_consomme_kg - self.poids_net_kg, 0.0) / self.poids_consomme_kg * 100 if self.poids_consomme_kg else None


def _kg(surface_mm2, epaisseur_mm, densite):
    """Surface (mm²) × épaisseur (mm) = mm³ ; ÷ 10⁶ = dm³ ; × densité (kg/dm³)."""
    return surface_mm2 * epaisseur_mm * densite / 1_000_000


def matiere_par_piece(devis, ligne):
    """MatierePiece de la ligne de devis `ligne` (article fabriqué), ou None si rien n'est calculable."""
    quantite = float(ligne.quantite or 0)
    if quantite <= 0:
        return None
    resultat = MatierePiece(prix=(Decimal(ligne.cout_matiere_calcule) / Decimal(str(quantite))) if ligne.cout_matiere_calcule is not None else None)
    article = ligne.article
    piece = PieceDecoupe.objects.filter(article=article, devis=devis, tole__isnull=False, format_tole__isnull=False).select_related("matiere", "tole", "format_tole").first()
    if piece is not None and piece.matiere_id and piece.epaisseur:
        try:
            groupe, entree = imb.imbrication_piece_devis(piece, max(1, round(quantite)))
        except ErreurMatiere:
            groupe = None
        if groupe is not None:
            densite, ep = piece.matiere.densite, float(piece.epaisseur)
            part = entree["surface_mm2"] / groupe.surface_pieces_mm2 if groupe.surface_pieces_mm2 else 0
            resultat.poids_net_kg = _kg(piece.surface_mm2, ep, densite)
            resultat.poids_consomme_kg = _kg(groupe.surface_consommee_mm2 * part / entree["quantite"], ep, densite)
            resultat.source = "imbrication"
            return resultat
    piece = piece_de_chiffrage(article)
    if piece is not None and piece.matiere_id and piece.epaisseur:
        try:
            cout = cout_matiere_imbrication(piece, max(1, round(quantite)))
        except ErreurMatiere:
            cout = None
        if cout is not None:
            densite, ep = piece.matiere.densite, float(piece.epaisseur)
            resultat.poids_net_kg = _kg(piece.surface_mm2, ep, densite)
            resultat.poids_consomme_kg = _kg(cout.surface_consommee_mm2 / quantite, ep, densite)
            resultat.source = "imbrication"
            return resultat
    total = 0.0
    for n in article.composants.select_related("article_composant__matiere"):
        c = n.article_composant
        if c.matiere_id and c.epaisseur and n.longueur_mm and n.largeur_mm:
            total += _kg(n.longueur_mm * n.largeur_mm, float(c.epaisseur), c.matiere.densite) * float(n.quantite)
    if total:
        resultat.poids_net_kg = total
        resultat.source = "nomenclature"
        return resultat
    return resultat if resultat.prix is not None else None
