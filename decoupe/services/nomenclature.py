"""Nomenclature (matière première consommée) des articles fabriqués créés depuis le devis.

Une pièce découpée consomme la **tôle** retenue à l'imbrication (ou, à défaut, l'unique tôle de sa matière et de son épaisseur) :
la ligne de nomenclature porte son rectangle englobant. Un débit de profilé consomme l'**article d'achat de sa section**, pour sa
longueur. Les lignes sont créées ou mises à jour sans jamais toucher à celles saisies à la main ; le chiffrage par imbrication
(chiffrage/moteur.py) ignore la ligne de la tôle retenue pour ne pas compter la matière deux fois."""

from technique.models import Article, Nomenclature

TOLERANCE_MM = 0.01


def _est_tole(article):
    return article.nature == Article.Nature.MATIERE_PREMIERE and article.epaisseur is not None


def tole_de_la_piece(piece):
    """Tôle retenue, sinon l'unique tôle correspondant à la matière et à l'épaisseur ; None si on ne peut pas trancher."""
    if piece.tole_id:
        return piece.tole
    if not piece.matiere_id or not piece.epaisseur:
        return None
    from .imbrication_devis import Groupe, toles_possibles

    possibles = toles_possibles(Groupe(piece.matiere, float(piece.epaisseur), piece.procede))
    return possibles[0] if len(possibles) == 1 else None


def alimenter_piece(piece):
    """Ligne de nomenclature de la tôle d'une pièce découpée du devis. Retourne la ligne, ou None si rien à faire."""
    article = piece.article
    if article is None or article.nature != Article.Nature.FABRIQUE or not piece.largeur_mm or not piece.hauteur_mm:
        return None
    tole = tole_de_la_piece(piece)
    if tole is None:
        return None
    longueur, largeur = round(max(piece.largeur_mm, piece.hauteur_mm), 2), round(min(piece.largeur_mm, piece.hauteur_mm), 2)
    # Ligne d'une ancienne tôle (matière ou épaisseur changée) : reconnue à ses cotes, qui sont celles de la pièce.
    for ligne in article.composants.select_related("article_composant").exclude(article_composant=tole):
        if (_est_tole(ligne.article_composant) and ligne.longueur_mm is not None and ligne.largeur_mm is not None
                and abs(ligne.longueur_mm - longueur) < TOLERANCE_MM and abs(ligne.largeur_mm - largeur) < TOLERANCE_MM):
            ligne.delete()
    ligne, _ = Nomenclature.objects.update_or_create(
        article_parent=article, article_composant=tole, defaults={"longueur_mm": longueur, "largeur_mm": largeur, "quantite": 1},
    )
    return ligne


def alimenter_debit(debit):
    """Ligne de nomenclature du profilé d'un débit : l'article d'achat de sa section, pour la longueur du débit."""
    article = debit.article
    achat = debit.section.article
    if article is None or article.nature != Article.Nature.FABRIQUE or achat is None:
        return None
    for ligne in article.composants.exclude(article_composant=achat):
        if ligne.article_composant_id == getattr(debit, "_ancien_achat_id", None):
            ligne.delete()
    ligne, _ = Nomenclature.objects.update_or_create(
        article_parent=article, article_composant=achat, defaults={"longueur_mm": float(debit.longueur_mm), "largeur_mm": None, "quantite": 1},
    )
    return ligne
