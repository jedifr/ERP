"""Chaîne devis validé -> commande -> ordre(s) de fabrication.

La création de la commande et des OF est toujours locale et ne dépend jamais
de la disponibilité du planning atelier (la synchronisation est tentée après
coup, voir planning_sync.py).
"""

from django.db import transaction
from django.utils import timezone

from commercial.models import Adresse
from technique.models import Article, PosteTravail

from .models import Commande, CommandeLigne, Devis, OperationOF, OrdreFabrication
from .moteur import ChiffrageError, gamme_active
from .planning_sync import tenter_synchronisation


def _adresse_principale(client, type_adresse):
    adresse = client.adresses.filter(type_adresse=type_adresse, est_principale=True).first()
    if adresse is None:
        raise ChiffrageError(
            f"Aucune adresse de {type_adresse} principale pour le client « {client} ». "
            "Ajoutez-en une avant de lancer en production."
        )
    return adresse


def _generer_numero_commande(devis):
    return f"CDE-{devis.numero}"


def _generer_numero_of(commande, index):
    return f"OF-{commande.numero}-{index}"


def lancer_en_production(devis):
    if devis.statut != Devis.Statut.VALIDE:
        raise ChiffrageError("Seul un devis validé peut être lancé en production.")
    if Commande.objects.filter(devis=devis).exists():
        raise ChiffrageError(f"Le devis « {devis} » a déjà été lancé en production.")

    with transaction.atomic():
        commande = Commande.objects.create(
            numero=_generer_numero_commande(devis),
            devis=devis,
            date_commande=timezone.now().date(),
            adresse_facturation=_adresse_principale(devis.client, Adresse.TypeAdresse.FACTURATION),
            adresse_livraison=_adresse_principale(devis.client, Adresse.TypeAdresse.LIVRAISON),
        )

        ordres_crees = []
        index = 1
        for ligne in devis.lignes.select_related("article").all():
            # Une ligne de commande par ligne de devis, quelle que soit la
            # nature de l'article : c'est elle qui porte le suivi de
            # livraison (partielle, article par article) — indépendant des
            # ordres de fabrication, qui ne concernent que les FABRIQUE.
            CommandeLigne.objects.create(
                commande=commande,
                article=ligne.article,
                quantite_commandee=ligne.quantite,
                devis_ligne=ligne,
            )

            if ligne.article.nature != Article.Nature.FABRIQUE:
                continue

            of = OrdreFabrication.objects.create(
                numero=_generer_numero_of(commande, index),
                commande=commande,
                article=ligne.article,
                quantite=ligne.quantite,
                date_lancement=timezone.now().date(),
            )
            index += 1

            for etape in gamme_active(ligne.article, devis.date_creation):
                temps_prevu = None
                if etape.poste.mode_calcul == PosteTravail.ModeCalcul.HORAIRE:
                    temps_prevu = (etape.temps_fixe or 0) + (etape.temps_variable or 0) * ligne.quantite
                OperationOF.objects.create(
                    ordre_fabrication=of,
                    poste=etape.poste,
                    ordre=etape.ordre,
                    temps_prevu=temps_prevu,
                )
            ordres_crees.append(of)

    for of in ordres_crees:
        tenter_synchronisation(of)

    return commande


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
            )
        )
    return lignes_creees
