"""Chiffrage des pièces à découper d'un devis : aperçu des prix et ajout aux lignes du devis (étape 3).

Une pièce est prête à être chiffrée quand : sa matière, son épaisseur et son procédé donnent une découpe réalisable, la tôle et
le format de son groupe ont été retenus (imbrication), et la gamme de son article a un poste de travail (temps de coupe). La
matière de chaque pièce est celle que lui répartit l'imbrication de son groupe (chiffrage/moteur.cout_matiere_article)."""

from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction

from decoupe.services import devis_pieces, imbrication_devis as imb
from decoupe.services.temps import ErreurTemps, estimer_temps_decoupe

from .builder import ajouter_ligne_devis
from .moteur import ChiffrageError, calculer_ligne, gamme_active, previsualiser_ligne
from .models import DevisLigne


@dataclass
class LigneChiffrage:
    piece: object
    pret: bool
    raison: str = ""
    temps_min: float | None = None
    prix: dict | None = None
    ligne: object = None  # ligne du devis déjà liée à l'article


def _gamme_prete(piece, devis):
    """Raison pour laquelle la gamme de l'article ne peut pas chiffrer (chaîne vide si tout va bien)."""
    if not piece.article_id:
        return "pas d'article fabriqué"
    if not gamme_active(piece.article, devis.date_creation).exists():
        devis_pieces.verdict(piece, alimenter=True)  # (ré)alimente la gamme depuis le temps de coupe si le poste est connu
        if not gamme_active(piece.article, devis.date_creation).exists():
            return "poste de travail à renseigner sur le paramètre de coupe (la gamme de l'article est vide)"
    return ""


def apercu(devis, avec_gamme=False):
    """Une LigneChiffrage par pièce réalisable du devis, avec son prix quand tout est prêt. Ne modifie rien (sauf, si
    `avec_gamme`, la gamme de l'article, pour la création effective des lignes)."""
    groupes, _ = imb.grouper(devis_pieces.pieces_du_devis(devis))
    lignes_existantes = {l.article_id: l for l in devis.lignes.select_related("article")}
    resultat = []
    for groupe in groupes:
        for piece in groupe.pieces:
            ligne = LigneChiffrage(piece=piece, pret=False, ligne=lignes_existantes.get(piece.article_id))
            resultat.append(ligne)
            if piece.tole_id is None or piece.format_tole_id is None:
                ligne.raison = "retenez la tôle et le format de l'imbrication"
                continue
            if avec_gamme:
                ligne.raison = _gamme_prete(piece, devis)
            elif not piece.article_id or not gamme_active(piece.article, devis.date_creation).exists():
                ligne.raison = "poste de travail à renseigner sur le paramètre de coupe (la gamme de l'article est vide)"
            if ligne.raison:
                continue
            try:
                ligne.temps_min = round(estimer_temps_decoupe(piece).total_min, 2)
                ligne.prix = previsualiser_ligne(devis, piece.article, piece.quantite)
                ligne.pret = True
            except (ChiffrageError, ErreurTemps) as exc:
                ligne.raison = str(exc)
    return resultat


def ajouter_au_devis(devis):
    """Crée (ou met à jour) la ligne de devis de chaque pièce prête, avec sa quantité, et la chiffre. Retourne
    [(LigneChiffrage, "ajoutée" | "mise à jour" | "ignorée")]. Le devis doit être en brouillon (ChiffrageError sinon)."""
    if devis.statut != devis.Statut.BROUILLON:
        raise ChiffrageError("Seul un devis en brouillon peut recevoir de nouvelles lignes : repassez-le en brouillon.")
    resultats = []
    for item in apercu(devis, avec_gamme=True):
        piece = item.piece
        if not item.pret:
            resultats.append((item, "ignorée"))
            continue
        try:
            with transaction.atomic():
                if not piece.imbrication_chiffrage:
                    piece.imbrication_chiffrage = True  # les autres chiffrages de l'article retrouvent ainsi la tôle imbriquée
                    piece.save(update_fields=["imbrication_chiffrage"])
                if item.ligne is not None:
                    ligne = item.ligne
                    ligne.quantite = piece.quantite
                    ligne.save(update_fields=["quantite"])
                    etat = "mise à jour"
                else:
                    ligne = ajouter_ligne_devis(devis, piece.article, piece.quantite)
                    etat = "ajoutée"
                calculer_ligne(devis, ligne)
        except ChiffrageError as exc:
            item.pret, item.raison = False, str(exc)
            resultats.append((item, "ignorée"))
            continue
        item.ligne = ligne
        resultats.append((item, etat))
    return resultats
