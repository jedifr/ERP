"""Création de factures et d'avoirs à partir des quantités réellement livrées et
facturées — jamais à partir du total de la commande."""

import datetime

from django.db import transaction
from django.utils import timezone

from codification.models import RegleCodification
from codification.services import enregistrer_code_utilise, generer_code

from .models import Facture, FactureLigne, facture_verrouillee


class FacturationError(Exception):
    """Opération de facturation impossible dans l'état actuel."""


def _numero_libre(base):
    numero, suffixe = base, 1
    while Facture.objects.filter(pk=numero).exists():
        suffixe += 1
        numero = f"{base}-{suffixe}"
    return numero


def reste_a_facturer(commande_ligne, anticipee=False):
    """Quantité qu'on peut encore facturer sur une ligne de commande : le livré (ou
    le commandé pour une facturation anticipée) moins le déjà facturé net d'avoirs."""
    plafond = commande_ligne.quantite_commandee if anticipee else min(
        commande_ligne.quantite_livree, commande_ligne.quantite_commandee
    )
    return max(round(plafond - FactureLigne.cumul_facture(commande_ligne), 6), 0)


def lignes_a_facturer(commande, anticipee=False):
    """[(ligne de commande, quantité restant à facturer)] — seulement celles > 0."""
    return [
        (ligne, reste)
        for ligne in commande.lignes.select_related("article", "taux_tva")
        if (reste := reste_a_facturer(ligne, anticipee)) > 0
    ]


def preparer_facture(commande, anticipee=False):
    """Crée une facture brouillon contenant le livré non encore facturé de la
    commande. Le numéro suit la règle de codification « Facture » si elle existe,
    sinon FAC-<commande>. Les montants sont déduits des lignes ; la référence Tiime
    reste à renseigner une fois la facture émise."""
    if commande.statut == commande.Statut.ANNULEE:
        raise FacturationError(f"La commande « {commande} » est annulée.")
    a_facturer = lignes_a_facturer(commande, anticipee)
    if not a_facturer:
        raise FacturationError(
            f"Rien à facturer sur « {commande} » : tout ce qui est livré est déjà facturé."
            if not anticipee
            else f"Rien à facturer sur « {commande} » : tout est déjà facturé."
        )
    with transaction.atomic():
        code = generer_code(RegleCodification.Entite.FACTURE)
        numero = _numero_libre(code or f"FAC-{commande.numero}")
        facture = Facture.objects.create(
            numero=numero,
            commande=commande,
            date_facturation=max(timezone.localdate(), commande.date_commande),
            mode_creation=Facture.ModeCreation.AUTOMATIQUE,
            anticipee=anticipee,
        )
        if code and numero == code:
            enregistrer_code_utilise(RegleCodification.Entite.FACTURE, numero)
        for ligne, quantite in a_facturer:
            FactureLigne.objects.create(facture=facture, commande_ligne=ligne, quantite=quantite)
        facture.remplir_montants_depuis_les_lignes()
    facture.refresh_from_db()
    return facture


def creer_avoir(facture, motif, utilisateur=None):
    """Avoir total (ou du solde restant à créditer) d'une facture émise ou
    comptabilisée : une ligne par ligne de la facture, pour ce qui n'a pas déjà
    été crédité. Pour un avoir partiel, saisir l'avoir à la main."""
    motif = (motif or "").strip()
    if not motif:
        raise FacturationError("Indiquez le motif de l'avoir.")
    if facture.est_avoir:
        raise FacturationError("On ne fait pas d'avoir sur un avoir.")
    if not facture_verrouillee(facture):
        raise FacturationError(
            f"« {facture} » n'est pas encore émise : modifiez-la ou supprimez-la plutôt que de la créditer."
        )
    a_crediter = []
    for ligne in facture.lignes.select_related("commande_ligne"):
        deja = sum(
            autre.quantite
            for autre in FactureLigne.objects.filter(facture__facture_origine=facture, commande_ligne=ligne.commande_ligne)
        )
        reste = round(ligne.quantite - deja, 6)
        if reste > 0:
            a_crediter.append((ligne, reste))
    if not facture.lignes.exists():
        raise FacturationError(
            f"« {facture} » n'a pas de lignes (facture antérieure aux lignes de facture) : saisissez l'avoir à la main."
        )
    if not a_crediter:
        raise FacturationError(f"« {facture} » est déjà intégralement créditée.")
    with transaction.atomic():
        avoir = Facture.objects.create(
            numero=_numero_libre(f"AV-{facture.numero}"),
            commande=facture.commande,
            type_document=Facture.TypeDocument.AVOIR,
            facture_origine=facture,
            motif=motif[:200],
            date_facturation=max(timezone.localdate(), facture.date_facturation),
            mode_creation=Facture.ModeCreation.AUTOMATIQUE,
        )
        for ligne, quantite in a_crediter:
            FactureLigne.objects.create(
                facture=avoir,
                commande_ligne=ligne.commande_ligne,
                quantite=quantite,
                prix_unitaire_ht=ligne.prix_unitaire_ht,
                taux_tva=ligne.taux_tva,
            )
        avoir._change_reason = f"Avoir créé : {motif}"[:100]
        avoir.remplir_montants_depuis_les_lignes()
    avoir.refresh_from_db()
    return avoir
