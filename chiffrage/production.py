"""Chaîne devis validé -> commande -> ordre(s) de fabrication.

La création de la commande et des OF est toujours locale et ne dépend jamais
de la disponibilité du planning atelier (la synchronisation est tentée après
coup, voir planning_sync.py).
"""

from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from comptes.montants import pourcent
from technique.models import Article, PosteTravail

from .models import Commande, CommandeLigne, CommandeLigneModification, Devis, OperationOF, OrdreFabrication
from .moteur import ChiffrageError, gamme_active
from .planning_sync import tenter_synchronisation

# Champs de CommandeLigne qui sont des surcharges du devis (valeur de départ
# recopiée à la création, modifiable ensuite) — chaque changement sur l'un
# d'eux est tracé dans CommandeLigneModification par les ModelAdmin
# (chiffrage/admin.py, qui a accès à request.user).
CHAMPS_SUIVIS_COMMANDE_LIGNE = ("quantite_commandee", "prix_vente_unitaire", "taux_tva", "designation")


def _texte_valeur(valeur):
    """Valeur lisible et comparable : un Decimal s'écrit sans zéros inutiles (5, pas 5.000000)."""
    if valeur is None:
        return ""
    if isinstance(valeur, Decimal):
        return pourcent(valeur)
    return str(valeur)


def enregistrer_modification_ligne(commande_ligne, champ, ancienne_valeur, nouvelle_valeur, utilisateur):
    CommandeLigneModification.objects.create(
        commande_ligne=commande_ligne,
        champ=champ,
        ancienne_valeur=_texte_valeur(ancienne_valeur),
        nouvelle_valeur=_texte_valeur(nouvelle_valeur),
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
    try:
        with transaction.atomic():
            commande, ordres_crees = _creer_commande_et_ordres(devis)
    except IntegrityError as exc:
        # Filet de sécurité : le numéro de commande dérive du devis, donc un
        # numéro déjà pris ailleurs finit ici plutôt qu'en erreur 500.
        raise ChiffrageError(
            f"Impossible de créer la commande du devis « {devis} » : numéro déjà utilisé "
            "ou lancement déjà en cours."
        ) from exc

    # Hors transaction : la synchro planning est un appel réseau, qui ne doit
    # ni retenir le verrou du devis ni faire échouer la création locale.
    for of in ordres_crees:
        tenter_synchronisation(of)

    return commande


def _creer_commande_et_ordres(devis):
    # Verrou de ligne sur le devis : un second lancement simultané (double-clic)
    # attend la fin du premier, puis voit la commande déjà créée au lieu d'en
    # créer une deuxième.
    devis = Devis.objects.select_for_update().get(pk=devis.pk)
    if devis.statut != Devis.Statut.VALIDE:
        raise ChiffrageError("Seul un devis validé peut être lancé en production.")
    if Commande.objects.filter(devis=devis).exists():
        raise ChiffrageError(f"Le devis « {devis} » a déjà été lancé en production.")
    if devis.issue == Devis.Issue.REFUSE:
        raise ChiffrageError(f"Le devis « {devis} » a été refusé par le client ({devis.motif_refus}).")
    if devis.issue == Devis.Issue.REMPLACE:
        revision = devis.revisions.order_by("-revision").first()
        suite = f" : utilisez « {revision} »" if revision else ""
        raise ChiffrageError(f"Le devis « {devis} » a été remplacé par une révision{suite}.")
    if devis.est_expire:
        raise ChiffrageError(
            f"L'offre « {devis} » a expiré le {devis.date_validite:%d/%m/%Y} : prolongez sa date de "
            "validité (si le client confirme) avant de la transformer en commande."
        )

    commande = Commande.objects.create(
        numero=_generer_numero_commande(devis),
        devis=devis,
        client=devis.client,
        date_commande=timezone.now().date(),
        adresse_facturation=_adresse_principale(devis.client, "est_facturation", "facturation"),
        adresse_livraison=_adresse_principale(devis.client, "est_livraison", "livraison"),
        devise=devis.client.devise,
    )

    devis.issue = Devis.Issue.ACCEPTE
    devis._change_reason = f"Commande {commande.numero} créée"[:100]
    devis.save(update_fields=["issue"])

    ordres_crees = []
    index = 1
    for ligne in devis.lignes.select_related("article").all():
        # Une ligne de commande par ligne de devis, quelle que soit la nature
        # de l'article : c'est elle qui porte le suivi de livraison (partielle,
        # article par article) — indépendant des ordres de fabrication, qui ne
        # concernent que les FABRIQUE. prix_vente_unitaire/taux_tva : valeur de
        # départ recopiée du devis, surchargeable ensuite sur la commande (voir
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

    return commande, ordres_crees


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


def reviser_devis(devis):
    """Crée une révision (brouillon) d'un devis validé resté sans commande : même en-tête,
    mêmes lignes. L'original est marqué « remplacé » (il ne peut plus être transformé en
    commande) et reste consultable ; le prix déjà envoyé au client n'est jamais réécrit."""
    import re

    from .models import DevisLigne

    with transaction.atomic():
        devis = Devis.objects.select_for_update().get(pk=devis.pk)
        if devis.statut != Devis.Statut.VALIDE:
            raise ChiffrageError("Seul un devis validé se révise : un brouillon se modifie directement.")
        if Commande.objects.filter(devis=devis).exists():
            raise ChiffrageError(f"Le devis « {devis} » est déjà devenu une commande : il ne se révise plus.")
        if devis.issue == Devis.Issue.REMPLACE:
            raise ChiffrageError(f"Le devis « {devis} » a déjà été remplacé par une révision.")
        base = re.sub(r"-R\d+$", "", devis.numero)
        revision = devis.revision + 1
        numero = f"{base}-R{revision}"
        while Devis.objects.filter(pk=numero).exists():
            revision += 1
            numero = f"{base}-R{revision}"
        nouveau = Devis.objects.create(
            numero=numero,
            client=devis.client,
            adresse_facturation=devis.adresse_facturation,
            adresse_livraison=devis.adresse_livraison,
            contact=devis.contact,
            date_creation=timezone.localdate(),
            statut=Devis.Statut.BROUILLON,
            taux_marge_globale=devis.taux_marge_globale,
            delai=devis.delai,
            devis_origine=devis,
            revision=revision,
        )
        for ligne in devis.lignes.all():
            DevisLigne.objects.create(
                devis=nouveau,
                article=ligne.article,
                ordre=ligne.ordre,
                quantite=ligne.quantite,
                taux_marge_matiere_applique=ligne.taux_marge_matiere_applique,
                prix_vente_unitaire_force=ligne.prix_vente_unitaire_force,
                taux_tva=ligne.taux_tva,
            )
        devis.issue = Devis.Issue.REMPLACE
        devis._change_reason = f"Remplacé par {numero}"[:100]
        devis.save(update_fields=["issue"])
    return nouveau
