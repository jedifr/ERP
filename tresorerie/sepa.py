"""Fichier de virements SEPA (ISO 20022 pain.001.001.03, « SEPA Credit Transfer ») d'un lot de virements fournisseurs,
à déposer sur le site de la banque. Une transaction par facture, un seul ordre (un compte débité, une date d'exécution)."""

import unicodedata
import uuid
import xml.etree.ElementTree as ET

from django.db import transaction
from django.utils import timezone

from commercial.models import valider_iban
from comptes.models import Societe
from comptes.montants import ZERO

from .models import LotVirement, Reglement

NAMESPACE = "urn:iso:std:iso:20022:tech:xsd:pain.001.001.03"

# Jeu de caractères admis par les banques dans un virement SEPA (EPC217-08) : tout le reste est translittéré.
_ADMIS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/-?:().,'+ ")


class VirementError(Exception):
    """Lot incomplet ou incohérent : le fichier n'est pas généré."""


def texte_sepa(valeur, longueur):
    """Texte réduit au jeu de caractères SEPA (accents retirés, & → « et »), espaces normalisés, tronqué."""
    valeur = (valeur or "").replace("&", " et ").replace("_", "-")
    valeur = unicodedata.normalize("NFKD", valeur).encode("ascii", "ignore").decode()
    valeur = "".join(c if c in _ADMIS else " " for c in valeur)
    return " ".join(valeur.split())[:longueur]


def _identifiant(valeur):
    """Identifiant (MsgId, EndToEndId…) : 35 caractères, sans espace."""
    return texte_sepa(valeur, 35).replace(" ", "-") or "NOTPROVIDED"


def _iban(valeur):
    return (valeur or "").replace(" ", "").upper()


def _sous(parent, balise, texte=None, **attributs):
    element = ET.SubElement(parent, balise, attributs)
    if texte is not None:
        element.text = texte
    return element


def _agent(parent, balise, bic):
    finl = _sous(_sous(parent, balise), "FinInstnId")
    if bic:
        _sous(finl, "BIC", bic.replace(" ", "").upper())
    else:
        _sous(_sous(finl, "Othr"), "Id", "NOTPROVIDED")


def verifier(lot):
    """Liste des problèmes qui empêchent de générer le fichier (vide : tout est prêt)."""
    problemes = []
    societe = Societe.charger()
    if not societe.iban:
        problemes.append("L'IBAN de la société n'est pas renseigné (Paramétrage → Société).")
    else:
        try:
            valider_iban(societe.iban)
        except Exception:
            problemes.append("L'IBAN de la société est invalide (Paramétrage → Société).")
    if lot.statut == LotVirement.Statut.EXECUTE:
        problemes.append("Ce lot est déjà exécuté.")
    if lot.date_execution < timezone.localdate():
        problemes.append("La date d'exécution est passée : choisissez aujourd'hui ou une date future.")
    lignes = list(lot.lignes.select_related("facture_fournisseur__commande_fournisseur__fournisseur"))
    if not lignes:
        problemes.append("Le lot ne contient aucun virement.")
    for ligne in lignes:
        fournisseur = ligne.fournisseur
        if not fournisseur.iban:
            problemes.append(f"{ligne.facture_fournisseur} : le fournisseur « {fournisseur} » n'a pas d'IBAN.")
        else:
            try:
                valider_iban(fournisseur.iban)
            except Exception:
                problemes.append(f"{ligne.facture_fournisseur} : l'IBAN du fournisseur « {fournisseur} » est invalide.")
        if not ligne.montant or ligne.montant <= ZERO:
            problemes.append(f"{ligne.facture_fournisseur} : montant nul.")
    return problemes


def construire_xml(lot, message_id, maintenant):
    societe = Societe.charger()
    lignes = list(lot.lignes.select_related("facture_fournisseur__commande_fournisseur__fournisseur"))
    total = sum((ligne.montant for ligne in lignes), ZERO)
    nombre = str(len(lignes))
    somme = f"{total:.2f}"
    emetteur = texte_sepa(societe.raison_sociale, 70)

    document = ET.Element("Document", {"xmlns": NAMESPACE, "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance"})
    racine = _sous(document, "CstmrCdtTrfInitn")

    entete = _sous(racine, "GrpHdr")
    _sous(entete, "MsgId", message_id)
    _sous(entete, "CreDtTm", maintenant.strftime("%Y-%m-%dT%H:%M:%S"))
    _sous(entete, "NbOfTxs", nombre)
    _sous(entete, "CtrlSum", somme)
    _sous(_sous(entete, "InitgPty"), "Nm", emetteur)

    ordre = _sous(racine, "PmtInf")
    _sous(ordre, "PmtInfId", _identifiant(f"{message_id}-1"))
    _sous(ordre, "PmtMtd", "TRF")
    _sous(ordre, "BtchBookg", "true")
    _sous(ordre, "NbOfTxs", nombre)
    _sous(ordre, "CtrlSum", somme)
    _sous(_sous(_sous(ordre, "PmtTpInf"), "SvcLvl"), "Cd", "SEPA")
    _sous(ordre, "ReqdExctnDt", lot.date_execution.isoformat())
    _sous(_sous(ordre, "Dbtr"), "Nm", emetteur)
    _sous(_sous(_sous(ordre, "DbtrAcct"), "Id"), "IBAN", _iban(societe.iban))
    _agent(ordre, "DbtrAgt", societe.bic)
    _sous(ordre, "ChrgBr", "SLEV")

    for ligne in lignes:
        fournisseur = ligne.fournisseur
        virement = _sous(ordre, "CdtTrfTxInf")
        _sous(_sous(virement, "PmtId"), "EndToEndId", end_to_end_id(ligne))
        _sous(_sous(virement, "Amt"), "InstdAmt", f"{ligne.montant:.2f}", Ccy="EUR")
        if fournisseur.bic:
            _agent(virement, "CdtrAgt", fournisseur.bic)
        _sous(_sous(virement, "Cdtr"), "Nm", texte_sepa(fournisseur.raison_sociale, 70))
        _sous(_sous(_sous(virement, "CdtrAcct"), "Id"), "IBAN", _iban(fournisseur.iban))
        _sous(_sous(virement, "RmtInf"), "Ustrd", texte_sepa(ligne.libelle_effectif(), 140))

    ET.indent(document)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(document, encoding="unicode") + "\n"


def end_to_end_id(ligne):
    return _identifiant(f"L{ligne.lot_id}-{ligne.facture_fournisseur.reference_fournisseur or ligne.facture_fournisseur_id}")


def generer_fichier(lot):
    """Génère (ou régénère, avec un nouvel identifiant : la banque refuse un fichier déjà reçu) le XML du lot."""
    problemes = verifier(lot)
    if problemes:
        raise VirementError(" ".join(problemes))
    maintenant = timezone.localtime()
    message_id = _identifiant(f"VIR{lot.pk}-{maintenant:%Y%m%d%H%M%S}-{uuid.uuid4().hex[:6]}")
    lot.xml = construire_xml(lot, message_id, maintenant)
    lot.message_id = message_id
    lot.date_generation = maintenant
    lot.statut = LotVirement.Statut.GENERE
    lot.save(update_fields=["xml", "message_id", "date_generation", "statut"])
    return lot.xml


def nom_fichier(lot):
    return f"virements-lot{lot.pk}-{lot.date_execution:%Y%m%d}.xml"


def marquer_execute(lot, utilisateur=None):
    """La banque a exécuté le lot : un règlement par virement, les factures passent payées."""
    if lot.statut != LotVirement.Statut.GENERE:
        raise VirementError(
            "Ce lot est déjà exécuté." if lot.statut == LotVirement.Statut.EXECUTE
            else "Générez d'abord le fichier XML et remettez-le à la banque."
        )
    date = min(lot.date_execution, timezone.localdate())
    with transaction.atomic():
        for ligne in lot.lignes.select_related("facture_fournisseur"):
            reglement = Reglement(
                facture_fournisseur=ligne.facture_fournisseur, date_reglement=date, montant=ligne.montant,
                mode=Reglement.Mode.VIREMENT, reference=end_to_end_id(ligne), lot_virement=lot, utilisateur=utilisateur,
            )
            try:
                reglement.full_clean()
            except Exception as exc:
                raise VirementError(f"{ligne.facture_fournisseur} : {_message(exc)}") from exc
            reglement.save()
        lot.statut = LotVirement.Statut.EXECUTE
        lot.save(update_fields=["statut"])
    return lot


def _message(exc):
    messages = getattr(exc, "messages", None)
    return " ".join(messages) if messages else str(exc)
