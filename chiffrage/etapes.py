"""« Étape suivante » : à chaque stade du cycle devis → commande → ordres de fabrication → livraison →
facture, la fiche propose la prochaine action utile, sans retourner dans les listes.

Ce module ne fait que *décider* l'étape (sans rien écrire) ; l'exécution est dans les admins
(`EtapeSuivanteMixin`), qui vérifient les droits de l'utilisateur."""

from collections import namedtuple

from facturation.services import lignes_a_facturer

from .models import Commande, Devis, Livraison
from .production import planifier_ordres_fabrication

Etape = namedtuple("Etape", ["cle", "libelle"])

CREER_COMMANDE = Etape("creer_commande", "Créer la commande")
OUVRIR_COMMANDE = Etape("ouvrir_commande", "Ouvrir la commande")
CREER_ORDRES = Etape("creer_ordres", "Créer les ordres de fabrication")
CREER_LIVRAISON = Etape("creer_livraison", "Créer la livraison")
PREPARER_FACTURE = Etape("preparer_facture", "Préparer la facture")


def lignes_a_livrer(commande):
    """Lignes de la commande qui ont un reliquat : [(ligne, quantité restant à livrer)]."""
    return [(l, l.reliquat) for l in commande.lignes.select_related("article") if l.reliquat is not None and l.reliquat > 0]


def etape_devis(devis):
    if devis.statut != Devis.Statut.VALIDE or devis.issue in (Devis.Issue.REFUSE, Devis.Issue.REMPLACE):
        return None
    return OUVRIR_COMMANDE if Commande.objects.filter(devis=devis).exists() else CREER_COMMANDE


def etape_commande(commande):
    if commande.statut == Commande.Statut.ANNULEE:
        return None
    if planifier_ordres_fabrication(commande, regrouper=False):
        return CREER_ORDRES
    if lignes_a_livrer(commande):
        return CREER_LIVRAISON
    if lignes_a_facturer(commande):
        return PREPARER_FACTURE
    return None


def etape_livraison(livraison):
    if livraison.statut != Livraison.Statut.VALIDEE or livraison.commande.statut == Commande.Statut.ANNULEE:
        return None
    return PREPARER_FACTURE if lignes_a_facturer(livraison.commande) else None
