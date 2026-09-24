"""Chaîne devis validé -> commande -> ordre(s) de fabrication.

La création de la commande et des OF est toujours locale et ne dépend jamais
de la disponibilité du planning atelier (la synchronisation est tentée après
coup, voir planning_sync.py).
"""

from django.db import transaction
from django.utils import timezone

from technique.models import Article, PosteTravail

from .models import Commande, CommandeLigne, CommandeLigneModification, Devis, OperationOF, OrdreFabrication
from .moteur import ChiffrageError, gamme_active
from .planning_sync import tenter_synchronisation

# Champs de CommandeLigne qui sont des surcharges du devis (valeur de départ
# recopiée à la création, modifiable ensuite) — chaque changement sur l'un
# d'eux est tracé dans CommandeLigneModification par les ModelAdmin
# (chiffrage/admin.py, qui a accès à request.user).
CHAMPS_SUIVIS_COMMANDE_LIGNE = ("quantite_commandee", "prix_vente_unitaire", "taux_tva", "designation")


def enregistrer_modification_ligne(commande_ligne, champ, ancienne_valeur, nouvelle_valeur, utilisateur):
    CommandeLigneModification.objects.create(
        commande_ligne=commande_ligne,
        champ=champ,
        ancienne_valeur="" if ancienne_valeur is None else str(ancienne_valeur),
        nouvelle_valeur="" if nouvelle_valeur is None else str(nouvelle_valeur),
        utilisateur=utilisateur,
    )


def _adresse_principale(client, champ_type, libelle):
    adresse = client.adresses.filter(**{champ_type: True}, est_principale=True).first()
    if adresse is None:
        raise ChiffrageError(
            f"Aucune adresse de {libelle} principale pour le client « {client} ». "
            "Ajoutez-en une avant de lancer en production."
        )
    return adresse


def _generer_numero_commande(devis):
    return f"CDE-{devis.numero}"


def _generer_numero_of(commande, index):
    return f"OF-{commande.numero}-{index}"


def _creer_ordre_fabrication(commande, article, quantite, date_reference, index):
    """Crée l'OF et ses opérations de gamme pour (commande, article,
    quantite) — factorisé entre lancer_en_production (toutes les lignes
    d'un coup) et lancer_ligne_en_production (une seule ligne, ajoutée
    après coup sans repasser par tout le devis)."""
    of = OrdreFabrication.objects.create(
        numero=_generer_numero_of(commande, index),
        commande=commande,
        article=article,
        quantite=quantite,
        date_lancement=timezone.now().date(),
    )
    for etape in gamme_active(article, date_reference):
        temps_prevu = None
        if etape.poste.mode_calcul == PosteTravail.ModeCalcul.HORAIRE:
            temps_prevu = (etape.temps_fixe or 0) + (etape.temps_variable or 0) * quantite
        OperationOF.objects.create(
            ordre_fabrication=of,
            poste=etape.poste,
            ordre=etape.ordre,
            temps_prevu=temps_prevu,
        )
    return of


def lancer_en_production(devis):
    if devis.statut != Devis.Statut.VALIDE:
        raise ChiffrageError("Seul un devis validé peut être lancé en production.")
    if Commande.objects.filter(devis=devis).exists():
        raise ChiffrageError(f"Le devis « {devis} » a déjà été lancé en production.")

    with transaction.atomic():
        commande = Commande.objects.create(
            numero=_generer_numero_commande(devis),
            devis=devis,
            client=devis.client,
            date_commande=timezone.now().date(),
            adresse_facturation=_adresse_principale(devis.client, "est_facturation", "facturation"),
            adresse_livraison=_adresse_principale(devis.client, "est_livraison", "livraison"),
            devise=devis.client.devise,
        )

        ordres_crees = []
        index = 1
        for ligne in devis.lignes.select_related("article").all():
            # Une ligne de commande par ligne de devis, quelle que soit la
            # nature de l'article : c'est elle qui porte le suivi de
            # livraison (partielle, article par article) — indépendant des
            # ordres de fabrication, qui ne concernent que les FABRIQUE.
            # prix_vente_unitaire/taux_tva : valeur de départ recopiée du
            # devis, surchargeable ensuite sur la commande (voir
            # CommandeLigne) sans jamais modifier la ligne de devis.
            CommandeLigne.objects.create(
                commande=commande,
                article=ligne.article,
                quantite_commandee=ligne.quantite,
                devis_ligne=ligne,
                prix_vente_unitaire=ligne.prix_vente_unitaire,
                taux_tva=ligne.taux_tva,
            )

            if ligne.article.nature != Article.Nature.FABRIQUE:
                continue

            of = _creer_ordre_fabrication(commande, ligne.article, ligne.quantite, devis.date_creation, index)
            index += 1
            ordres_crees.append(of)

    for of in ordres_crees:
        tenter_synchronisation(of)

    return commande


def lancer_ligne_en_production(commande_ligne):
    """Crée l'ordre de fabrication d'UNE ligne de commande précise, sans
    repasser par lancer_en_production (qui traite tout le devis d'un coup).
    Sert notamment à une ligne ajoutée après coup pour représenter une
    augmentation de quantité (plutôt que de modifier une ligne dont l'OF a
    déjà été lancé, qui laisserait ses temps machine basés sur l'ancienne
    quantité — voir CommandeLigne)."""
    article = commande_ligne.article
    if article.nature != Article.Nature.FABRIQUE:
        raise ChiffrageError(f"« {article} » n'est pas un article fabriqué : pas d'ordre de fabrication à créer.")
    if OrdreFabrication.objects.filter(commande=commande_ligne.commande, article=article).exists():
        raise ChiffrageError(
            f"Un ordre de fabrication existe déjà pour « {article} » sur la commande "
            f"« {commande_ligne.commande} »."
        )

    date_reference = (
        commande_ligne.devis_ligne.devis.date_creation
        if commande_ligne.devis_ligne_id
        else commande_ligne.commande.date_commande
    )

    with transaction.atomic():
        index = commande_ligne.commande.ordres_fabrication.count() + 1
        of = _creer_ordre_fabrication(
            commande_ligne.commande, article, commande_ligne.quantite_commandee, date_reference, index
        )

    tenter_synchronisation(of)
    return of


def synchroniser_lignes_commande(commande):
    """Filet de sécurité, rejouable sans risque : recrée toute CommandeLigne
    manquante par rapport aux lignes du devis d'origine (article absent de la
    commande — ex. commande créée avant l'ajout de ce mécanisme, ou ligne de
    devis ajoutée après coup), et relie devis_ligne sur les lignes qui ne
    l'ont pas encore (pour que taux de TVA / prix redeviennent visibles sans
    dupliquer ces valeurs). Ne modifie jamais quantite_commandee sur une
    ligne existante : une divergence avec le devis reste une décision
    manuelle, pas automatique."""
    devis_lignes_par_article = {}
    for ligne in commande.devis.lignes.all():
        devis_lignes_par_article.setdefault(ligne.article_id, []).append(ligne)

    for commande_ligne in commande.lignes.filter(devis_ligne__isnull=True):
        candidates = devis_lignes_par_article.get(commande_ligne.article_id) or []
        if len(candidates) == 1:
            commande_ligne.devis_ligne = candidates[0]
            commande_ligne.save(update_fields=["devis_ligne"])

    articles_presents = set(commande.lignes.values_list("article_id", flat=True))
    lignes_creees = []
    for ligne in commande.devis.lignes.select_related("article").all():
        if ligne.article_id in articles_presents:
            continue
        lignes_creees.append(
            CommandeLigne.objects.create(
                commande=commande,
                article=ligne.article,
                quantite_commandee=ligne.quantite,
                devis_ligne=ligne,
                prix_vente_unitaire=ligne.prix_vente_unitaire,
                taux_tva=ligne.taux_tva,
            )
        )
    return lignes_creees
