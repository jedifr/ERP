"""Génération d'écritures comptables depuis les documents commerciaux.

Pour l'instant : uniquement les factures de vente (facturation.Facture) ->
journal des ventes. L'app achats n'a pas de document "facture fournisseur"
(seulement des commandes/réceptions, logistiques) : la génération des
écritures d'achat serait une évolution ultérieure séparée — voir
ArticleCompteAchat, purement déclaratif en attendant.
"""

from django.db import transaction

from .models import EcritureComptable, LigneEcriture, ParametresComptables


class GenerationEcritureError(Exception):
    """Donnée manquante ou incohérente empêchant la génération de l'écriture."""


def _repartition_lignes(facture, parametres):
    """Regroupe les lignes de la commande facturée par (taux de TVA, compte
    de vente, code analytique), en sommant leurs montants HT/TTC courants
    (CommandeLigne.montant_ht/montant_ttc — les surcharges post-devis, pas
    les valeurs figées du devis d'origine). Le compte de vente est celui
    d'ArticleCompteVente si l'article en a un — résolu selon le régime
    fiscal du client (Tiers.regime_fiscal) s'il passe par un poste de
    gestion, sinon son compte fixe — à défaut le compte par défaut des
    paramètres. Repli sur les montants globaux de la facture (compte par
    défaut, sans détail par article) si la commande n'a aucune ligne
    chiffrée (ex. facture ancienne, ou lignes sans prix renseigné)."""
    regime_fiscal = facture.commande.devis.client.regime_fiscal
    groupes = {}
    lignes = facture.commande.lignes.select_related(
        "taux_tva",
        "article__compte_vente_override__compte_vente",
        "article__compte_vente_override__code_analytique",
        "article__compte_vente_override__poste_gestion",
    )
    for ligne in lignes.all():
        if ligne.montant_ht is None or ligne.montant_ttc is None:
            continue
        taux = ligne.taux_tva.taux if ligne.taux_tva_id else 0
        override = getattr(ligne.article, "compte_vente_override", None)
        if override is not None:
            compte_vente = override.resoudre_compte_vente(regime_fiscal)
            if compte_vente is None:
                raise GenerationEcritureError(
                    f"« {ligne.article} » : le poste de gestion « {override.poste_gestion} » n'a pas de "
                    f"compte de vente configuré pour le régime fiscal « {regime_fiscal} »."
                )
            code_analytique = override.resoudre_code_analytique()
        else:
            compte_vente = parametres.compte_vente_defaut
            code_analytique = None
        cle = (taux, compte_vente.pk, code_analytique.pk if code_analytique else None)
        groupe = groupes.setdefault(
            cle,
            {"taux": taux, "compte_vente": compte_vente, "code_analytique": code_analytique, "ht": 0.0, "ttc": 0.0},
        )
        groupe["ht"] += ligne.montant_ht
        groupe["ttc"] += ligne.montant_ttc

    if groupes:
        return groupes

    if facture.montant_ht is None or facture.montant_ttc is None:
        raise GenerationEcritureError(
            f"« {facture} » n'a ni lignes de commande chiffrées ni montants HT/TTC renseignés : "
            "impossible de générer l'écriture."
        )
    return {
        (None, parametres.compte_vente_defaut.pk, None): {
            "taux": None,
            "compte_vente": parametres.compte_vente_defaut,
            "code_analytique": None,
            "ht": facture.montant_ht,
            "ttc": facture.montant_ttc,
        }
    }


def generer_ecriture_facture(facture):
    """Génère l'écriture comptable d'une facture de vente : Clients au
    débit (montant TTC), Ventes + TVA collectée au crédit (une paire de
    lignes par groupe (taux de TVA, compte de vente, code analytique)
    distinct présent sur la commande facturée — voir ArticleCompteVente
    pour surcharger le compte de vente/code analytique d'un article
    donné, éventuellement résolu selon le régime fiscal du client via un
    poste de gestion ; le code analytique n'est jamais posé sur les
    lignes Clients/TVA, seulement sur la ligne de vente). Idempotent — ne
    génère jamais deux écritures pour la même facture, la renvoie
    simplement si elle existe déjà. Renvoie (ecriture, creee)."""
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

    groupes = _repartition_lignes(facture, parametres)
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
        for cle in sorted(groupes, key=lambda c: (c[0] is None, c[0] or 0, c[1], c[2] or "")):
            montants = groupes[cle]
            taux = montants["taux"]
            libelle = f"Facture {facture.numero} — TVA {taux:g}%" if taux else f"Facture {facture.numero}"
            LigneEcriture.objects.create(
                ecriture=ecriture,
                compte=montants["compte_vente"],
                code_analytique=montants["code_analytique"],
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
