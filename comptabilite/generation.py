"""Génération d'écritures comptables depuis les documents commerciaux.

Pour l'instant : uniquement les factures de vente (facturation.Facture) ->
journal des ventes. L'app achats n'a pas de document "facture fournisseur"
(seulement des commandes/réceptions, logistiques) : la génération des
écritures d'achat serait une évolution ultérieure séparée — voir
ArticleCompteAchat, purement déclaratif en attendant.
"""

from django.db import transaction

from comptes.montants import ZERO, arrondir, pourcent

from .models import EcritureComptable, LigneEcriture, ParametresComptables


class GenerationEcritureError(Exception):
    """Donnée manquante ou incohérente empêchant la génération de l'écriture."""


def _ttc_par_taux(groupes):
    """TVA calculée **par taux sur la base HT totale** puis arrondie (règle EN 16931, celle du PDF et de Factur-X) et non ligne à ligne : le
    total TTC de l'écriture est celui de la facture. Quand plusieurs comptes de vente partagent un taux, l'éventuel centime d'arrondi va au
    groupe le plus important. Les montants négatifs d'un avoir gardent leur signe."""
    par_taux = {}
    for g in groupes.values():
        if g["taux"] is not None:
            par_taux.setdefault(g["taux"], []).append(g)
    for taux, liste in par_taux.items():
        signe = -1 if sum((g["ht"] for g in liste), ZERO) < 0 else 1
        tva_totale = arrondir(abs(sum((g["ht"] for g in liste), ZERO)) * taux / 100)
        tvas = [arrondir(abs(g["ht"]) * taux / 100) for g in liste]
        plus_gros = max(range(len(liste)), key=lambda i: abs(liste[i]["ht"]))
        tvas[plus_gros] += tva_totale - sum(tvas, ZERO)
        for g, tva in zip(liste, tvas):
            g["ttc"] = signe * (abs(g["ht"]) + tva)
    return groupes


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
    regime_fiscal = facture.commande.client.regime_fiscal
    groupes = {}
    # Facture avec ses propres lignes (ce qu'elle facture réellement, signé pour un
    # avoir) : on s'appuie sur elles. Sinon — facture ancienne sans lignes — repli
    # historique sur les lignes de la commande.
    lignes_facture = list(
        facture.lignes.select_related(
            "commande_ligne__article__compte_vente_override__compte_vente",
            "commande_ligne__article__compte_vente_override__code_analytique",
            "commande_ligne__article__compte_vente_override__poste_gestion",
            "commande_ligne__taux_tva",
        )
    )
    if lignes_facture:
        sources = [
            (ligne.commande_ligne.article, ligne._taux(), ligne.montant_ht, ligne.montant_ttc)
            for ligne in lignes_facture
        ]
    else:
        sources = [
            (ligne.article, ligne.taux_tva.taux if ligne.taux_tva_id else 0, ligne.montant_ht, ligne.montant_ttc)
            for ligne in facture.commande.lignes.select_related(
                "taux_tva",
                "article__compte_vente_override__compte_vente",
                "article__compte_vente_override__code_analytique",
                "article__compte_vente_override__poste_gestion",
            )
            if ligne.montant_ht is not None and ligne.montant_ttc is not None
        ]
    for article, taux, montant_ht, montant_ttc in sources:
        override = getattr(article, "compte_vente_override", None)
        if override is not None:
            compte_vente = override.resoudre_compte_vente(regime_fiscal)
            if compte_vente is None:
                raise GenerationEcritureError(
                    f"« {article} » : le poste de gestion « {override.poste_gestion} » n'a pas de "
                    f"compte de vente configuré pour le régime fiscal « {regime_fiscal} »."
                )
            code_analytique = override.resoudre_code_analytique()
        else:
            compte_vente = parametres.compte_vente_defaut
            code_analytique = None
        cle = (taux, compte_vente.pk, code_analytique.pk if code_analytique else None)
        groupe = groupes.setdefault(
            cle,
            {"taux": taux, "compte_vente": compte_vente, "code_analytique": code_analytique, "ht": ZERO, "ttc": ZERO},
        )
        groupe["ht"] += montant_ht
        groupe["ttc"] += montant_ttc

    if groupes:
        return _ttc_par_taux(groupes)

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
    débit (montant TTC — compte spécifique du client si TiersCompteComptable
    en a un, sinon compte client par défaut), Ventes + TVA collectée au
    crédit (une paire de
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
    total_ttc = arrondir(sum((g["ttc"] for g in groupes.values()), ZERO))
    # Un avoir (montants négatifs) passe en sens inverse d'une facture : Clients au
    # crédit, Ventes et TVA au débit. Les montants des lignes restent positifs.
    avoir = total_ttc < 0
    sens_client, sens_vente = ("credit", "debit") if avoir else ("debit", "credit")

    client = facture.commande.client
    comptes_client = getattr(client, "comptes_comptables", None)
    compte_client = (
        comptes_client.compte_client
        if comptes_client is not None and comptes_client.compte_client_id
        else parametres.compte_client_defaut
    )

    with transaction.atomic():
        ecriture = EcritureComptable.objects.create(
            journal=parametres.journal_ventes,
            date_ecriture=facture.date_facturation,
            piece=facture.numero,
            libelle=f"{'Avoir' if avoir else 'Facture'} {facture.numero}",
            facture=facture,
        )
        LigneEcriture.objects.create(
            ecriture=ecriture,
            compte=compte_client,
            libelle=f"{'Avoir' if avoir else 'Facture'} {facture.numero}",
            **{sens_client: abs(total_ttc)},
        )
        for cle in sorted(groupes, key=lambda c: (c[0] is None, c[0] or 0, c[1], c[2] or "")):
            montants = groupes[cle]
            taux = montants["taux"]
            nature = "Avoir" if avoir else "Facture"
            libelle = f"{nature} {facture.numero} — TVA {pourcent(taux)}%" if taux else f"{nature} {facture.numero}"
            ht = arrondir(abs(montants["ht"]))
            if ht:
                LigneEcriture.objects.create(
                    ecriture=ecriture,
                    compte=montants["compte_vente"],
                    code_analytique=montants["code_analytique"],
                    libelle=libelle,
                    **{sens_vente: ht},
                )
            tva = arrondir(abs(montants["ttc"]) - abs(montants["ht"]))
            if tva:
                LigneEcriture.objects.create(
                    ecriture=ecriture,
                    compte=parametres.compte_tva_collectee_defaut,
                    libelle=libelle,
                    **{sens_vente: tva},
                )

    return ecriture, True
