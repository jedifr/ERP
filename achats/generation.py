"""Génération d'écritures comptables depuis les documents d'achat.

Pendant de comptabilite.generation côté achat : Fournisseurs (401) au
crédit, Achats + TVA déductible au débit — même structure en miroir
(débit/crédit inversés), voir generer_ecriture_facture pour le détail des
choix de conception.
"""

from django.db import transaction

from comptabilite.models import EcritureComptable, LigneEcriture, ParametresComptables


class GenerationEcritureAchatError(Exception):
    """Donnée manquante ou incohérente empêchant la génération de l'écriture."""


def _repartition_lignes(facture_fournisseur, parametres):
    """Regroupe les lignes de la commande fournisseur facturée par (taux de
    TVA, compte d'achat, code analytique), en sommant leurs montants
    HT/TTC. Le compte d'achat est celui d'ArticleCompteAchat si l'article
    en a un (résolu selon le régime fiscal du fournisseur), celui du poste
    de gestion pour une ligne de charge générale sans article, sinon le
    compte d'achat par défaut des paramètres. Repli sur les montants
    globaux de la facture fournisseur (compte par défaut, sans détail par
    ligne) si la commande n'a aucune ligne."""
    regime_fiscal = facture_fournisseur.commande_fournisseur.fournisseur.regime_fiscal
    groupes = {}
    lignes = facture_fournisseur.commande_fournisseur.lignes.select_related(
        "taux_tva",
        "article__compte_achat_override__compte_achat",
        "article__compte_achat_override__code_analytique",
        "article__compte_achat_override__poste_gestion",
        "poste_gestion",
        "poste_gestion__code_analytique",
    )
    for ligne in lignes.all():
        taux = ligne.taux_tva.taux if ligne.taux_tva_id else 0
        if ligne.article_id:
            override = getattr(ligne.article, "compte_achat_override", None)
            if override is not None:
                compte_achat = override.resoudre_compte_achat(regime_fiscal)
                if compte_achat is None:
                    raise GenerationEcritureAchatError(
                        f"« {ligne.article} » : le poste de gestion « {override.poste_gestion} » n'a pas "
                        f"de compte d'achat configuré pour le régime fiscal « {regime_fiscal} »."
                    )
                code_analytique = override.resoudre_code_analytique()
            else:
                compte_achat = parametres.compte_achat_defaut
                code_analytique = None
        else:
            # Ligne "poste de gestion" (charge générale sans article, ex.
            # assurance) : LigneCommandeFournisseur.clean() garantit que
            # poste_gestion est renseigné dans ce cas.
            compte_achat = ligne.poste_gestion.compte_achat_pour_regime(regime_fiscal)
            if compte_achat is None:
                raise GenerationEcritureAchatError(
                    f"« {ligne.poste_gestion} » n'a pas de compte d'achat configuré pour le "
                    f"régime fiscal « {regime_fiscal} »."
                )
            code_analytique = ligne.poste_gestion.code_analytique

        cle = (taux, compte_achat.pk, code_analytique.pk if code_analytique else None)
        groupe = groupes.setdefault(
            cle,
            {"taux": taux, "compte_achat": compte_achat, "code_analytique": code_analytique, "ht": 0.0, "ttc": 0.0},
        )
        groupe["ht"] += ligne.montant_ht
        groupe["ttc"] += ligne.montant_ttc

    if groupes:
        return groupes

    if facture_fournisseur.montant_ht is None or facture_fournisseur.montant_ttc is None:
        raise GenerationEcritureAchatError(
            f"« {facture_fournisseur} » n'a ni lignes de commande ni montants HT/TTC renseignés : "
            "impossible de générer l'écriture."
        )
    return {
        (None, parametres.compte_achat_defaut.pk, None): {
            "taux": None,
            "compte_achat": parametres.compte_achat_defaut,
            "code_analytique": None,
            "ht": facture_fournisseur.montant_ht,
            "ttc": facture_fournisseur.montant_ttc,
        }
    }


def generer_ecriture_achat(facture_fournisseur):
    """Génère l'écriture comptable d'une facture fournisseur : Fournisseurs
    au crédit (montant TTC — compte spécifique du fournisseur si
    TiersCompteComptable en a un, sinon compte fournisseur par défaut),
    Achats + TVA déductible au débit (une paire de lignes par groupe
    (taux de TVA, compte d'achat, code analytique) distinct présent sur la
    commande fournisseur facturée — voir ArticleCompteAchat pour surcharger
    le compte d'achat/code analytique d'un article donné, éventuellement
    résolu selon le régime fiscal du fournisseur via un poste de gestion ;
    le code analytique n'est jamais posé sur la ligne Fournisseurs/TVA,
    seulement sur la ligne d'achat). Idempotent — ne génère jamais deux
    écritures pour la même facture fournisseur, la renvoie simplement si
    elle existe déjà. Renvoie (ecriture, creee)."""
    ecriture_existante = EcritureComptable.objects.filter(facture_fournisseur=facture_fournisseur).first()
    if ecriture_existante is not None:
        return ecriture_existante, False

    parametres = ParametresComptables.charger()
    for champ, libelle in (
        ("journal_achats", "journal des achats"),
        ("compte_fournisseur_defaut", "compte fournisseur par défaut"),
        ("compte_achat_defaut", "compte d'achat par défaut"),
        ("compte_tva_deductible_defaut", "compte de TVA déductible par défaut"),
    ):
        if getattr(parametres, champ) is None:
            raise GenerationEcritureAchatError(
                f"Aucun {libelle} configuré (Paramètres comptables), et le code PCG usuel "
                "correspondant n'existe pas en base — importez le plan comptable officiel ou "
                "configurez ce compte manuellement."
            )

    groupes = _repartition_lignes(facture_fournisseur, parametres)
    total_ttc = sum(g["ttc"] for g in groupes.values())

    fournisseur = facture_fournisseur.commande_fournisseur.fournisseur
    comptes_tiers = getattr(fournisseur, "comptes_comptables", None)
    compte_fournisseur = (
        comptes_tiers.compte_fournisseur
        if comptes_tiers is not None and comptes_tiers.compte_fournisseur_id
        else parametres.compte_fournisseur_defaut
    )

    with transaction.atomic():
        ecriture = EcritureComptable.objects.create(
            journal=parametres.journal_achats,
            date_ecriture=facture_fournisseur.date_facture,
            piece=facture_fournisseur.reference_fournisseur or facture_fournisseur.numero,
            libelle=f"Facture fournisseur {facture_fournisseur.numero}",
            facture_fournisseur=facture_fournisseur,
        )
        LigneEcriture.objects.create(
            ecriture=ecriture,
            compte=compte_fournisseur,
            libelle=f"Facture fournisseur {facture_fournisseur.numero}",
            credit=total_ttc,
        )
        for cle in sorted(groupes, key=lambda c: (c[0] is None, c[0] or 0, c[1], c[2] or "")):
            montants = groupes[cle]
            taux = montants["taux"]
            libelle = (
                f"Facture fournisseur {facture_fournisseur.numero} — TVA {taux:g}%"
                if taux
                else f"Facture fournisseur {facture_fournisseur.numero}"
            )
            LigneEcriture.objects.create(
                ecriture=ecriture,
                compte=montants["compte_achat"],
                code_analytique=montants["code_analytique"],
                libelle=libelle,
                debit=montants["ht"],
            )
            tva = montants["ttc"] - montants["ht"]
            if tva:
                LigneEcriture.objects.create(
                    ecriture=ecriture,
                    compte=parametres.compte_tva_deductible_defaut,
                    libelle=libelle,
                    debit=tva,
                )

    return ecriture, True
