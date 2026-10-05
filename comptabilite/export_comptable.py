"""Export des écritures vers le logiciel du comptable (format ISACOMPTA, voir isacompta.py).

Trois fichiers par période, comme l'export actuel : ventes, achats et banque.
- ventes et achats : les écritures comptables de la période (journaux de nature Ventes / Achats), rangées Produits / TVA / Tiers
  (ventes) et Charges / TVA / Tiers (achats) ; le mouvement du tiers porte l'échéance ;
- banque : une écriture par règlement de la période — encaissement d'une facture client (banque au débit, client au
  crédit) ou règlement d'une facture fournisseur (fournisseur au débit, banque au crédit), rangée Banque / Tiers."""

import datetime
import io
import zipfile
from decimal import Decimal

from . import isacompta
from .models import EcritureComptable, JournalComptable, ParametresComptables, ParametresExportComptable

PREFIXES_TIERS = ("40", "41")


class ErreurExport(Exception):
    pass


def bornes_du_mois(annee, mois):
    debut = datetime.date(annee, mois, 1)
    fin = (debut.replace(day=28) + datetime.timedelta(days=4)).replace(day=1) - datetime.timedelta(days=1)
    return debut, fin


def _assembler(elements, valeurs):
    texte = " ".join(valeurs.get(e, "") for e in elements if valeurs.get(e))
    return texte[: isacompta.LARGEUR_LIBELLE]


def _rang(code, banque):
    """Ordre des mouvements : ventes/achats → produits ou charges, TVA, tiers ; banque → banque, tiers."""
    tiers = code.startswith(PREFIXES_TIERS)
    if banque:
        return 1 if tiers else 0
    if tiers:
        return 2
    return 1 if code.startswith("445") else 0


def _ecritures_de_journal(nature, debut, fin, parametres, code_journal):
    achats = nature == JournalComptable.Nature.ACHATS
    elements = parametres.elements("libelle_achats" if achats else "libelle_ventes")
    qs = (
        EcritureComptable.objects.filter(journal__nature=nature, date_ecriture__range=(debut, fin))
        .select_related("facture__commande__client", "facture_fournisseur__commande_fournisseur__fournisseur")
        .prefetch_related("lignes__compte")
        .order_by("date_ecriture", "pk")
    )
    resultat = []
    for e in qs:
        facture = e.facture_fournisseur if achats else e.facture
        tiers = None
        if facture is not None:
            tiers = facture.commande_fournisseur.fournisseur if achats else facture.commande.client
        numero = facture.numero if facture is not None else e.piece
        valeurs = {
            "code_facture": numero, "nom_tiers": tiers.raison_sociale if tiers else "",
            "reference_fournisseur": getattr(facture, "reference_fournisseur", "") if achats else "",
        }
        libelle = _assembler(elements, valeurs) or e.libelle
        piece = numero
        if achats and parametres.piece_achats == "reference_fournisseur" and valeurs["reference_fournisseur"]:
            piece = valeurs["reference_fournisseur"]
        echeance = _echeance(facture, tiers, e.date_ecriture, achats)
        ecriture = isacompta.Ecriture(
            journal=code_journal, date=e.date_ecriture, piece=piece[-isacompta.LARGEUR_PIECE:], libelle=libelle, type_piece="fa",
            reference_externe=valeurs["reference_fournisseur"] if achats else "",
        )
        lignes = sorted(e.lignes.all(), key=lambda l: (_rang(l.compte.code, False), l.pk))
        for l in lignes:
            est_tiers = l.compte.code.startswith(PREFIXES_TIERS)
            ecriture.mouvements.append(isacompta.Mouvement(
                compte=l.compte.code, libelle=libelle, debit=Decimal(l.debit), credit=Decimal(l.credit), tiers=est_tiers,
                echeance=echeance if est_tiers else None,
            ))
        resultat.append(ecriture)
    return resultat


def _echeance(facture, tiers, date_ecriture, achats):
    if facture is None:
        return date_ecriture
    if not achats:
        return facture.date_echeance or date_ecriture
    conditions = getattr(tiers, "conditions_paiement", None)
    return conditions.calculer_echeance(facture.date_facture) if conditions is not None else date_ecriture


def _compte_tiers(tiers, client, comptes_defaut):
    comptes = getattr(tiers, "comptes_comptables", None)
    if client:
        compte = comptes.compte_client if comptes is not None and comptes.compte_client_id else comptes_defaut.compte_client_defaut
    else:
        compte = comptes.compte_fournisseur if comptes is not None and comptes.compte_fournisseur_id else comptes_defaut.compte_fournisseur_defaut
    if compte is None:
        raise ErreurExport(f"Aucun compte {'client' if client else 'fournisseur'} pour « {tiers} » (Comptes comptables de tiers, ou paramètres comptables).")
    return compte.code


def ecritures_banque(debut, fin, parametres):
    from achats.models import FactureFournisseur
    from facturation.models import Facture

    elements = parametres.elements("libelle_banque")
    defauts = ParametresComptables.charger()
    resultat = []

    def ajouter(date, numero, tiers, reference, montant, client):
        valeurs = {"code_facture": numero, "nom_tiers": tiers.raison_sociale, "reference_fournisseur": reference}
        libelle = _assembler(elements, valeurs)
        banque = isacompta.Mouvement(compte=parametres.compte_banque, libelle=libelle)
        compte_tiers = isacompta.Mouvement(
            compte=_compte_tiers(tiers, client, defauts), libelle=libelle, tiers=True, echeance=date if client else None
        )
        if client:  # encaissement
            banque.debit, compte_tiers.credit = montant, montant
        else:  # règlement fournisseur
            banque.credit, compte_tiers.debit = montant, montant
        resultat.append((date, numero, isacompta.Ecriture(
            journal=parametres.code_journal_banque, date=date, piece="", libelle=libelle, type_piece="re", mouvements=[banque, compte_tiers],
        )))

    for f in Facture.objects.filter(
        type_document=Facture.TypeDocument.FACTURE, statut_paiement=Facture.StatutPaiement.PAYE, date_paiement__range=(debut, fin)
    ).select_related("commande__client"):
        if f.montant_ttc:
            ajouter(f.date_paiement, f.numero, f.commande.client, "", Decimal(f.montant_ttc), True)
    for f in FactureFournisseur.objects.filter(date_paiement__range=(debut, fin)).select_related("commande_fournisseur__fournisseur"):
        if f.montant_ttc:
            ajouter(f.date_paiement, f.numero, f.commande_fournisseur.fournisseur, f.reference_fournisseur, Decimal(f.montant_ttc), False)
    return [e for _, _, e in sorted(resultat, key=lambda x: (x[0], x[1]))]


def exporter(debut, fin, date_export=None):
    """{"ventes": texte, "achats": texte, "banque": texte, "comptes": {nom: nombre d'écritures}} pour la période."""
    parametres = ParametresExportComptable.charger()
    date_export = date_export or datetime.date.today()
    jeux = {
        "ventes": _ecritures_de_journal(JournalComptable.Nature.VENTES, debut, fin, parametres, parametres.code_journal_ventes),
        "achats": _ecritures_de_journal(JournalComptable.Nature.ACHATS, debut, fin, parametres, parametres.code_journal_achats),
        "banque": ecritures_banque(debut, fin, parametres),
    }
    try:
        textes = {nom: isacompta.fichier(ecritures, date_export) for nom, ecritures in jeux.items()}
    except isacompta.ErreurFormat as exc:
        raise ErreurExport(str(exc)) from exc
    return {**textes, "comptes": {nom: len(ecritures) for nom, ecritures in jeux.items()}}


NOMS_FICHIERS = {"ventes": "vente", "achats": "achat", "banque": "banque"}


def nom_fichier(nom, debut):
    return f"{NOMS_FICHIERS[nom]}_{debut:%m-%Y}.txt"


def archive_zip(resultat, debut):
    """Archive ZIP des trois fichiers de la période (encodage ASCII, fins de ligne CRLF)."""
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w", zipfile.ZIP_DEFLATED) as z:
        for nom in NOMS_FICHIERS:
            z.writestr(nom_fichier(nom, debut), resultat[nom].encode("ascii", errors="replace"))
    return tampon.getvalue()
