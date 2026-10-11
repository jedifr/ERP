"""Colonne « Prochaine action » des listes (devis, commandes, livraisons, factures) : ce qu'il reste à faire sur chaque document, dans
les mots du métier, avec une couleur selon l'urgence. Le calcul ne fait que lire (aucune écriture) ; la décision est dans les fonctions
`action_*` ci-dessous, testées une à une."""

import datetime
from collections import namedtuple

from django.contrib import admin
from django.utils import timezone
from django.utils.html import format_html

Action = namedtuple("Action", ["texte", "ton"])  # ton : ko (en retard) | attention (à faire) | normal | calme (rien à faire)
RIEN = Action("—", "calme")


def _date(d):
    return f"{d:%d/%m}"


def action_devis(devis):
    from chiffrage import etapes
    from chiffrage.models import Devis

    aujourdhui = timezone.localdate()
    if devis.statut == Devis.Statut.BROUILLON:
        return Action("Ajouter des lignes" if not devis.lignes.exists() else "Terminer et valider le devis", "attention")
    if devis.issue in (Devis.Issue.REFUSE, Devis.Issue.REMPLACE):
        return RIEN
    if devis.issue == Devis.Issue.ACCEPTE:
        etape = etapes.etape_devis(devis)
        return Action(etape.libelle, "attention") if etape and etape.cle == "creer_commande" else Action("Commande en cours", "calme")
    # validé, en attente de la réponse du client
    if devis.date_validite:
        if devis.date_validite < aujourdhui:
            return Action("Offre expirée : relancer ou refuser", "ko")
        if devis.date_validite <= aujourdhui + datetime.timedelta(days=7):
            return Action(f"Relancer le client avant le {_date(devis.date_validite)}", "attention")
        return Action(f"Attendre la réponse (offre jusqu'au {_date(devis.date_validite)})", "normal")
    return Action("Attendre la réponse du client", "normal")


def action_commande(commande):
    from chiffrage import etapes
    from chiffrage.models import Commande

    if commande.statut != Commande.Statut.EN_COURS:
        return RIEN
    etape = etapes.etape_commande(commande)
    if etape is not None:
        return Action(etape.libelle, "attention")
    return Action("Suivre la fabrication et les livraisons", "normal")


def action_livraison(livraison):
    from chiffrage import etapes

    etape = etapes.etape_livraison(livraison)
    return Action(etape.libelle, "attention") if etape else RIEN


def action_facture(facture):
    from facturation.models import Facture

    if facture.est_avoir or facture.statut_paiement == Facture.StatutPaiement.PAYE:
        return RIEN
    niveau = min(facture.relances.count() + 1, 3)
    noms = {1: "Envoyer un rappel", 2: "Envoyer la relance", 3: "Envoyer la dernière relance"}
    echeance = facture.date_echeance
    if facture.est_en_retard:
        return Action(f"{noms[niveau]} (échéance dépassée : {_date(echeance)})", "ko")
    if echeance:
        return Action(f"Attendre le règlement (échéance {_date(echeance)})", "normal")
    return Action("Suivre le règlement", "normal")


class ProchaineActionMixin:
    """À mélanger aux ModelAdmin : fournit la colonne `prochaine_action` (à ajouter à `list_display`) ; `fonction_prochaine_action`
    désigne la règle du document (une des fonctions `action_*`)."""

    fonction_prochaine_action = None

    @admin.display(description="Prochaine action")
    def prochaine_action(self, obj):
        try:
            action = type(self).fonction_prochaine_action(obj)
        except Exception:  # une règle en échec ne doit jamais empêcher d'afficher la liste
            return "—"
        return format_html('<span class="pa pa-{}">{}</span>', action.ton, action.texte)
