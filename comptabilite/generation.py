"""Génération d'écritures comptables depuis les documents commerciaux.

Pour l'instant : uniquement les factures de vente (facturation.Facture) ->
journal des ventes. L'app achats n'a pas de document "facture fournisseur"
(seulement des commandes/réceptions, logistiques) : la génération des
écritures d'achat serait une évolution ultérieure séparée.
"""

from django.db import transaction

from .models import EcritureComptable, LigneEcriture, ParametresComptables


class GenerationEcritureError(Exception):
    """Donnée manquante ou incohérente empêchant la génération de l'écriture."""


def _repartition_par_taux_tva(facture):
    """Regroupe les lignes de la commande facturée par taux de TVA, en
    sommant leurs montants HT/TTC courants (CommandeLigne.montant_ht/
    montant_ttc — les surcharges post-devis, pas les valeurs figées du
    devis d'origine). Repli sur les montants globaux de la facture si la
    commande n'a aucune ligne chiffrée (ex. facture ancienne, ou lignes
    sans prix renseigné)."""
    groupes = {}
    for ligne in facture.commande.lignes.select_related("taux_tva").all():
        if ligne.montant_ht is None or ligne.montant_ttc is None:
            continue
        taux = ligne.taux_tva.taux if ligne.taux_tva_id else 0
        groupe = groupes.setdefault(taux, {"ht": 0.0, "ttc": 0.0})
        groupe["ht"] += ligne.montant_ht
        groupe["ttc"] += ligne.montant_ttc

    if groupes:
        return groupes

    if facture.montant_ht is None or facture.montant_ttc is None:
        raise GenerationEcritureError(
            f"« {facture} » n'a ni lignes de commande chiffrées ni montants HT/TTC renseignés : "
            "impossible de générer l'écriture."
        )
    return {None: {"ht": facture.montant_ht, "ttc": facture.montant_ttc}}


def generer_ecriture_facture(facture):
    """Génère l'écriture comptable d'une facture de vente : Clients au
    débit (montant TTC), Ventes + TVA collectée au crédit (une paire de
    lignes par taux de TVA distinct présent sur la commande facturée).
    Idempotent — ne génère jamais deux écritures pour la même facture, la
    renvoie simplement si elle existe déjà. Renvoie (ecriture, creee)."""
    ecriture_existante = EcritureComptable.objects.filter(facture=facture).first()
    if ecriture_existante is not None:
        return ecriture_existante, False

    parametres = ParametresComptables.charger()
    for champ, libelle in (
        ("journal_ventes", "journal des ventes"),
        ("compte_client_defaut", "compte client par défaut"),
        ("compte_vente_defaut", "compte de vente par défaut"),
        ("compte_tva_collectee_defaut", "compte de TVA collectée par défaut"),
    ):
        if getattr(parametres, champ) is None:
            raise GenerationEcritureError(
                f"Aucun {libelle} configuré (Paramètres comptables), et le code PCG usuel "
                "correspondant n'existe pas en base — importez le plan comptable officiel ou "
                "configurez ce compte manuellement."
            )

    groupes = _repartition_par_taux_tva(facture)
    total_ttc = sum(g["ttc"] for g in groupes.values())

    with transaction.atomic():
        ecriture = EcritureComptable.objects.create(
            journal=parametres.journal_ventes,
            date_ecriture=facture.date_facturation,
            piece=facture.numero,
            libelle=f"Facture {facture.numero}",
            facture=facture,
        )
        LigneEcriture.objects.create(
            ecriture=ecriture,
            compte=parametres.compte_client_defaut,
            libelle=f"Facture {facture.numero}",
            debit=total_ttc,
        )
        for taux, montants in sorted(groupes.items(), key=lambda kv: (kv[0] is None, kv[0])):
            libelle = f"Facture {facture.numero} — TVA {taux:g}%" if taux else f"Facture {facture.numero}"
            LigneEcriture.objects.create(
                ecriture=ecriture,
                compte=parametres.compte_vente_defaut,
                libelle=libelle,
                credit=montants["ht"],
            )
            tva = montants["ttc"] - montants["ht"]
            if tva:
                LigneEcriture.objects.create(
                    ecriture=ecriture,
                    compte=parametres.compte_tva_collectee_defaut,
                    libelle=libelle,
                    credit=tva,
                )

    return ecriture, True
