"""Débits de profilés d'un devis : création (avec l'article fabriqué), modification des cotes, suppression."""

from django.db import transaction
from django.db.models import ProtectedError

from codification.models import RegleCodification
from codification.services import enregistrer_code_utilise
from technique.models import Article

from ..models import PieceProfile, ProfileSection
from .devis_pieces import ErreurPieceDevis, _reference_article
from .profiles import ErreurProfile, verifier_piece


def _nombre(valeur, libelle):
    try:
        return float(str(valeur).replace(",", "."))
    except (TypeError, ValueError):
        raise ErreurPieceDevis(f"« {libelle} » doit être un nombre.")


def _lire(section_id, longueur, coupe_a, coupe_b, quantite):
    section = ProfileSection.objects.filter(pk=section_id).first()
    if section is None:
        raise ErreurPieceDevis("Choisissez une section de profilé.")
    quantite = int(_nombre(quantite or 1, "Quantité"))
    if quantite < 1:
        raise ErreurPieceDevis("La quantité doit être au moins 1.")
    return section, _nombre(longueur, "Longueur"), _nombre(coupe_a or 90, "Coupe A"), _nombre(coupe_b or 90, "Coupe B"), quantite


def nom_suggere(section, longueur):
    return f"{section.designation} L={longueur:g}"


@transaction.atomic
def creer(devis, section_id, longueur, coupe_a=90, coupe_b=90, quantite=1, nom=None):
    """Débit de profilé du devis et son article fabriqué ; ErreurPieceDevis si les cotes sont invalides."""
    section, longueur, coupe_a, coupe_b, quantite = _lire(section_id, longueur, coupe_a, coupe_b, quantite)
    piece = PieceProfile(
        devis=devis, section=section, nom=(nom or nom_suggere(section, longueur))[:200], longueur_mm=longueur, coupe_a_deg=coupe_a,
        coupe_b_deg=coupe_b, quantite=quantite,
    )
    try:
        verifier_piece(piece)
    except ErreurProfile as exc:
        raise ErreurPieceDevis(str(exc))
    reference, par_regle = _reference_article(devis)
    article = Article.objects.create(reference=reference, libelle=piece.nom, nature=Article.Nature.FABRIQUE)
    if par_regle:
        enregistrer_code_utilise(RegleCodification.Entite.ARTICLE, reference)
    piece.article, piece.article_cree_automatiquement = article, True
    piece.save()
    return piece


@transaction.atomic
def modifier(piece, donnees):
    """Met à jour un débit (section, longueur, coupes, quantité, nom, trait de scie, chute de tête, chute récupérable)."""
    ancien_nom = nom_suggere(piece.section, piece.longueur_mm)
    if "section" in donnees:
        piece.section = ProfileSection.objects.filter(pk=donnees["section"]).first() or piece.section
    for champ, libelle in (("longueur", "Longueur"), ("coupe_a", "Coupe A"), ("coupe_b", "Coupe B"), ("trait_scie", "Trait de scie"), ("marge_bout", "Chute de tête")):
        if champ in donnees:
            attribut = {"longueur": "longueur_mm", "coupe_a": "coupe_a_deg", "coupe_b": "coupe_b_deg", "trait_scie": "trait_scie_mm", "marge_bout": "marge_bout_mm"}[champ]
            valeur = _nombre(donnees[champ], libelle)
            if champ in ("trait_scie", "marge_bout") and valeur < 0:
                raise ErreurPieceDevis(f"« {libelle} » ne peut pas être négatif.")
            setattr(piece, attribut, valeur)
    if "quantite" in donnees:
        piece.quantite = int(_nombre(donnees["quantite"], "Quantité"))
        if piece.quantite < 1:
            raise ErreurPieceDevis("La quantité doit être au moins 1.")
    if "chute" in donnees:
        piece.taux_chute_recuperable = max(0, min(100, _nombre(donnees["chute"], "Chute récupérable")))
    if "nom" in donnees:
        if not donnees["nom"].strip():
            raise ErreurPieceDevis("Le nom ne peut pas être vide.")
        piece.nom = donnees["nom"].strip()[:200]
    elif piece.nom == ancien_nom:
        piece.nom = nom_suggere(piece.section, piece.longueur_mm)
    try:
        verifier_piece(piece)
    except ErreurProfile as exc:
        raise ErreurPieceDevis(str(exc))
    piece.save()
    if piece.article_id and piece.article_cree_automatiquement:
        piece.article.libelle = piece.nom
        piece.article.save(update_fields=["libelle"])
    return piece


def supprimer(piece):
    """Retire le débit ; son article est supprimé avec lui s'il n'est utilisé nulle part (sinon son nom est retourné)."""
    article = piece.article if piece.article_cree_automatiquement else None
    conserve = None
    with transaction.atomic():
        piece.delete()
        if article is not None:
            try:
                with transaction.atomic():
                    article.delete()
            except ProtectedError:
                conserve = article.pk
    return conserve


def pieces_du_devis(devis):
    return list(devis.pieces_profile.select_related("section", "section__article", "article").order_by("pk"))
