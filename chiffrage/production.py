"""Chaîne devis validé -> commande -> ordre(s) de fabrication.

La création de la commande et des OF est toujours locale et ne dépend jamais
de la disponibilité du planning atelier (la synchronisation est tentée après
coup, voir planning_sync.py).
"""

from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from comptes.montants import arrondir_prix_vente, pourcent
from technique.models import Article, PosteTravail

from .models import (
    ComposantOF, Commande, CommandeLigne, CommandeLigneModification, Devis, OperationOF, OrdreFabrication, indice_pour,
)
from .moteur import ChiffrageError, gamme_active, previsualiser_ligne_commande, resoudre_taux_tva
from .planning_sync import tenter_synchronisation

# Champs de CommandeLigne qui sont des surcharges du devis (valeur de départ
# recopiée à la création, modifiable ensuite) — chaque changement sur l'un
# d'eux est tracé dans CommandeLigneModification par les ModelAdmin
# (chiffrage/admin.py, qui a accès à request.user).
CHAMPS_SUIVIS_COMMANDE_LIGNE = ("quantite_commandee", "prix_vente_unitaire", "taux_tva", "designation")


def prix_unitaire_commande(ligne_devis):
    """Prix unitaire HT repris sur la commande : celui de la ligne de devis (remise de ligne déduite), diminué de la remise
    globale du devis au prorata, pour que la commande (et ses factures) totalise le montant HT du devis."""
    prix = ligne_devis.prix_vente_unitaire
    devis = ligne_devis.devis
    if prix is None or not devis.remise_globale:
        return prix
    return arrondir_prix_vente(prix * devis.coefficient_remise_globale)


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
        from django.urls import reverse

        raise ChiffrageError(
            f"Aucune adresse de {libelle} principale pour le client « {client} ». "
            "Ajoutez-en une avant de lancer en production.",
            lien=(reverse("admin:commercial_tiers_change", args=[client.pk]), f"Ajouter l'adresse de {libelle} du client"),
        )
    return adresse


def _generer_numero_commande(devis):
    return f"CDE-{devis.numero}"


def _prochain_numero_of(commande):
    """OF-<commande>-<n>, premier numéro libre (jamais de collision, même après suppression d'un OF)."""
    n = commande.ordres_fabrication.count() + 1
    while OrdreFabrication.objects.filter(pk=f"OF-{commande.numero}-{n}").exists():
        n += 1
    return f"OF-{commande.numero}-{n}"


def _date_reference(ligne):
    """Date à laquelle la gamme et les tarifs sont lus : celle du devis d'origine, sinon de la commande."""
    return ligne.devis_ligne.devis.date_creation if ligne.devis_ligne_id else ligne.commande.date_commande


def _lignes_a_fabriquer(commande, lignes=None):
    """Lignes de commande d'articles fabriqués qui n'ont pas encore d'ordre de fabrication."""
    candidates = lignes if lignes is not None else commande.lignes.select_related("article", "devis_ligne__devis").all()
    return [
        ligne
        for ligne in candidates
        if ligne.article.nature == Article.Nature.FABRIQUE
        and ligne.quantite_commandee
        and not ligne.ordres_fabrication.exists()
    ]


def planifier_ordres_fabrication(commande, regrouper=False, lignes=None):
    """Ce qu'on créerait, sans rien écrire : [{"article", "lignes", "quantite", "date_livraison_prevue"}].
    Un ordre par ligne de commande ; avec `regrouper`, un seul ordre par article (quantités additionnées)."""
    a_fabriquer = _lignes_a_fabriquer(commande, lignes)
    groupes = {}
    for ligne in a_fabriquer:
        cle = ligne.article_id if regrouper else ligne.pk
        groupes.setdefault(cle, []).append(ligne)
    plan = []
    for lignes_groupe in groupes.values():
        dates = [l.date_livraison_prevue for l in lignes_groupe if l.date_livraison_prevue]
        plan.append(
            {
                "article": lignes_groupe[0].article,
                "lignes": lignes_groupe,
                "quantite": sum(l.quantite_commandee for l in lignes_groupe),
                "date_livraison_prevue": min(dates) if dates else None,
            }
        )
    return plan


def _creer_ordre_fabrication(commande, element_plan):
    """Crée l'OF, sa gamme (opérations), sa nomenclature (composants) et le lien avec les lignes
    de commande couvertes, pour un élément de `planifier_ordres_fabrication`."""
    article, quantite = element_plan["article"], element_plan["quantite"]
    of = OrdreFabrication.objects.create(
        numero=_prochain_numero_of(commande),
        commande=commande,
        article=article,
        quantite=quantite,
        date_lancement=timezone.now().date(),
        date_livraison_prevue=element_plan["date_livraison_prevue"],
    )
    of.lignes_commande.set(element_plan["lignes"])
    for etape in gamme_active(article, _date_reference(element_plan["lignes"][0])):
        temps_prevu = None
        if etape.poste.mode_calcul == PosteTravail.ModeCalcul.HORAIRE:
            temps_prevu = (etape.temps_fixe or 0) + (etape.temps_variable or 0) * quantite
        OperationOF.objects.create(
            ordre_fabrication=of,
            poste=etape.poste,
            ordre=etape.ordre,
            temps_prevu=temps_prevu,
        )
    for n in article.composants.select_related("article_composant"):
        ComposantOF.objects.create(
            ordre_fabrication=of,
            article=n.article_composant,
            quantite_par_unite=n.quantite,
            quantite_necessaire=n.quantite * quantite,
            longueur_mm=n.longueur_mm,
            largeur_mm=n.largeur_mm,
        )
    return of


def creer_ordres_fabrication(commande, regrouper=False, lignes=None):
    """Crée les ordres de fabrication d'une commande client : un par ligne d'article fabriqué
    (ou un par article avec `regrouper`), quantité commandée reprise, date de livraison prévue,
    gamme et nomenclature copiées. Rejouable : seules les lignes sans ordre sont traitées, donc
    jamais de doublon (une ligne ajoutée plus tard se lance en recliquant)."""
    with transaction.atomic():
        commande = Commande.objects.select_for_update().get(pk=commande.pk)
        if commande.statut == Commande.Statut.ANNULEE:
            raise ChiffrageError(f"La commande « {commande} » est annulée.")
        plan = planifier_ordres_fabrication(commande, regrouper, lignes)
        if not plan:
            raise ChiffrageError(
                "Aucun ordre de fabrication à créer : la commande n'a pas de ligne d'article fabriqué "
                "sans ordre (les lignes déjà couvertes par un ordre sont ignorées)."
            )
        ordres = [_creer_ordre_fabrication(commande, element) for element in plan]

    # Hors transaction : la synchro planning est un appel réseau, qui ne doit ni retenir
    # le verrou de la commande ni faire échouer la création locale.
    for of in ordres:
        tenter_synchronisation(of)
    return ordres


def lancer_en_production(devis):
    try:
        with transaction.atomic():
            commande = _creer_commande_et_ordres(devis)
    except IntegrityError as exc:
        # Filet de sécurité : le numéro de commande dérive du devis, donc un
        # numéro déjà pris ailleurs finit ici plutôt qu'en erreur 500.
        raise ChiffrageError(
            f"Impossible de créer la commande du devis « {devis} » : numéro déjà utilisé "
            "ou lancement déjà en cours."
        ) from exc

    # Les ordres de fabrication se créent ensuite depuis la commande (creer_ordres_fabrication).
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
        from django.urls import reverse

        raise ChiffrageError(
            f"L'offre « {devis} » a expiré le {devis.date_validite:%d/%m/%Y} : prolongez sa date de "
            "validité (si le client confirme) avant de la transformer en commande.",
            lien=(reverse("admin:chiffrage_devis_change", args=[devis.pk]), "Modifier la date de validité"),
        )

    commande = Commande.objects.create(
        numero=_generer_numero_commande(devis),
        devis=devis,
        client=devis.client,
        date_commande=timezone.now().date(),
        # Les adresses choisies sur le devis priment ; repli sur les adresses principales du client
        # pour les anciens devis enregistrés sans adresse.
        adresse_facturation=devis.adresse_facturation or _adresse_principale(devis.client, "est_facturation", "facturation"),
        adresse_livraison=devis.adresse_livraison or _adresse_principale(devis.client, "est_livraison", "livraison"),
        devise=devis.client.devise,
    )

    devis.issue = Devis.Issue.ACCEPTE
    devis._change_reason = f"Commande {commande.numero} créée"[:100]
    devis.save(update_fields=["issue"])

    for ordre, ligne in enumerate(devis.lignes.select_related("article").all()):
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
            designation=ligne.designation,
            prix_vente_unitaire=prix_unitaire_commande(ligne),
            taux_tva=ligne.taux_tva,
            ordre=ordre,
        )

    return commande


def lancer_ligne_en_production(commande_ligne):
    """Crée l'ordre de fabrication d'UNE ligne de commande précise (ligne ajoutée après coup pour
    une augmentation de quantité, par exemple). Refusé si la ligne est déjà couverte par un ordre."""
    article = commande_ligne.article
    if article.nature != Article.Nature.FABRIQUE:
        raise ChiffrageError(f"« {article} » n'est pas un article fabriqué : pas d'ordre de fabrication à créer.")
    if commande_ligne.ordres_fabrication.exists():
        raise ChiffrageError(
            f"La ligne « {article} » de la commande « {commande_ligne.commande} » a déjà un ordre de fabrication "
            f"({', '.join(o.numero for o in commande_ligne.ordres_fabrication.all())})."
        )
    return creer_ordres_fabrication(commande_ligne.commande, lignes=[commande_ligne])[0]


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
    ordre = CommandeLigne.prochain_ordre(commande)
    for ligne in commande.devis.lignes.select_related("article").all():
        if ligne.article_id in articles_presents:
            continue
        lignes_creees.append(
            CommandeLigne.objects.create(
                commande=commande,
                article=ligne.article,
                quantite_commandee=ligne.quantite,
                devis_ligne=ligne,
                designation=ligne.designation,
                prix_vente_unitaire=prix_unitaire_commande(ligne),
                taux_tva=ligne.taux_tva,
                ordre=ordre,
            )
        )
        ordre += 1
    return lignes_creees


def devis_importables(commande):
    """Devis du client de la commande dont on peut reprendre des lignes : validés (prix engagé auprès du client),
    ni refusés ni remplacés par une révision. Les offres expirées restent listées mais sont refusées à l'import
    (voir importer_lignes_devis), comme pour le lancement en production."""
    return (
        Devis.objects.filter(
            client=commande.client,
            statut=Devis.Statut.VALIDE,
            issue__in=[Devis.Issue.EN_ATTENTE, Devis.Issue.ACCEPTE],
        )
        .prefetch_related("lignes__article", "lignes__taux_tva")
        .order_by("-date_creation", "-numero")
    )


def importer_lignes_devis(commande, lignes_devis):
    """Ajoute à la commande une ligne par ligne de devis choisie (dans l'ordre reçu), à la suite des lignes
    existantes, avec les mêmes valeurs de départ qu'au lancement en production. Les devis encore en attente de
    réponse passent à « Accepté ». Une même ligne de devis peut être reprise plusieurs fois (commande en plusieurs
    fois) : c'est à l'utilisateur de décocher les lignes déjà présentes."""
    if commande.statut == Commande.Statut.ANNULEE:
        raise ChiffrageError(f"La commande « {commande} » est annulée : on ne peut plus y ajouter de lignes.")
    autorises = {d.pk: d for d in devis_importables(commande)}
    for ligne in lignes_devis:
        devis = autorises.get(ligne.devis_id)
        if devis is None:
            raise ChiffrageError(
                f"Le devis « {ligne.devis} » ne peut pas être repris sur cette commande "
                "(autre client, pas validé, refusé ou remplacé par une révision)."
            )
        if devis.est_expire:
            raise ChiffrageError(
                f"L'offre « {devis} » a expiré le {devis.date_validite:%d/%m/%Y} : prolongez sa date de "
                "validité (si le client confirme) avant d'en reprendre les lignes."
            )

    creees = []
    with transaction.atomic():
        ordre = CommandeLigne.prochain_ordre(commande)
        for ligne in lignes_devis:
            creees.append(
                CommandeLigne.objects.create(
                    commande=commande,
                    article=ligne.article,
                    quantite_commandee=ligne.quantite,
                    devis_ligne=ligne,
                    designation=ligne.designation,
                    prix_vente_unitaire=prix_unitaire_commande(ligne),
                    taux_tva=ligne.taux_tva,
                    ordre=ordre,
                )
            )
            ordre += 1
        for devis_id in {ligne.devis_id for ligne in lignes_devis}:
            devis = autorises[devis_id]
            if devis.issue == Devis.Issue.EN_ATTENTE:
                devis.issue = Devis.Issue.ACCEPTE
                devis._change_reason = f"Lignes reprises sur la commande {commande.numero}"[:100]
                devis.save(update_fields=["issue"])
        commande.mettre_a_jour_statut_livraison()
    return creees


def ajouter_ligne_commande(commande, article, quantite, designation=""):
    """Ajoute en fin de commande une ligne sans devis d'origine (assistant « Composer un article » de la fiche
    Commande) : prix calculé comme une ligne saisie à la main (nomenclature + gamme, marges par défaut de l'article
    et des postes, voir previsualiser_ligne_commande) et TVA selon le régime du client."""
    if commande.statut == Commande.Statut.ANNULEE:
        raise ChiffrageError(f"La commande « {commande} » est annulée : on ne peut plus y ajouter de lignes.")
    if not quantite or quantite <= 0:
        raise ChiffrageError("La quantité doit être strictement positive.")
    prix = previsualiser_ligne_commande(article, quantite, commande.date_commande)
    ligne = CommandeLigne.objects.create(
        commande=commande,
        article=article,
        quantite_commandee=quantite,
        designation=designation,
        prix_vente_unitaire=prix["prix_vente_unitaire"],
        taux_tva=resoudre_taux_tva(article, commande.client),
        ordre=CommandeLigne.prochain_ordre(commande),
    )
    commande.mettre_a_jour_statut_livraison()
    return ligne


def reviser_devis(devis, motif=""):
    """Crée un nouvel indice (brouillon) d'un devis validé resté sans commande : même en-tête,
    mêmes lignes, numéro `<devis d'origine>-<indice>` (B, C…). L'indice précédent est marqué
    « remplacé » (il ne peut plus être transformé en commande) et reste consultable ; le prix déjà
    envoyé au client n'est jamais réécrit. Le motif (ce qui change) est obligatoire : il figure
    sur l'offre et dans l'historique des indices."""
    from .models import DevisLigne

    motif = (motif or "").strip()
    if not motif:
        raise ChiffrageError("Indiquez le motif de la révision (ce qui change par rapport à l'indice précédent).")
    with transaction.atomic():
        devis = Devis.objects.select_for_update().get(pk=devis.pk)
        if devis.statut != Devis.Statut.VALIDE:
            raise ChiffrageError("Seul un devis validé se révise : un brouillon se modifie directement.")
        if Commande.objects.filter(devis=devis).exists():
            raise ChiffrageError(
                f"Le devis « {devis} » est déjà devenu une commande : il ne se révise plus "
                "(modifiez les lignes de la commande, les changements sont tracés)."
            )
        if devis.issue == Devis.Issue.REMPLACE:
            raise ChiffrageError(f"Le devis « {devis} » a déjà été remplacé par une révision.")
        base = devis.racine.numero
        revision = devis.revision + 1
        numero = f"{base}-{indice_pour(revision)}"
        while Devis.objects.filter(pk=numero).exists():
            revision += 1
            numero = f"{base}-{indice_pour(revision)}"
        nouveau = Devis.objects.create(
            numero=numero,
            client=devis.client,
            adresse_facturation=devis.adresse_facturation,
            adresse_livraison=devis.adresse_livraison,
            contact=devis.contact,
            date_creation=timezone.localdate(),
            statut=Devis.Statut.BROUILLON,
            taux_marge_globale=devis.taux_marge_globale,
            remise_globale=devis.remise_globale,
            remise_globale_type=devis.remise_globale_type,
            delai=devis.delai,
            devis_origine=devis,
            revision=revision,
            motif_revision=motif[:200],
        )
        for ligne in devis.lignes.all():
            DevisLigne.objects.create(
                devis=nouveau,
                article=ligne.article,
                ordre=ligne.ordre,
                designation=ligne.designation,
                quantite=ligne.quantite,
                taux_marge_matiere_applique=ligne.taux_marge_matiere_applique,
                prix_vente_unitaire_force=ligne.prix_vente_unitaire_force,
                remise_pct=ligne.remise_pct,
                taux_tva=ligne.taux_tva,
            )
        devis.issue = Devis.Issue.REMPLACE
        devis._change_reason = f"Remplacé par {numero} : {motif}"[:100]
        devis.save(update_fields=["issue"])
    return nouveau


def repasser_devis_en_brouillon(devis):
    """Rouvre un devis validé pour le modifier SANS créer de nouvel indice (correction avant ou juste
    après l'envoi) : même numéro, même indice, statut « brouillon ». Il faudra le revalider ; sa date
    de validité sera reposée à ce moment-là. Un refus noté est effacé (l'offre corrigée repart en attente)."""
    with transaction.atomic():
        devis = Devis.objects.select_for_update().get(pk=devis.pk)
        if devis.statut != Devis.Statut.VALIDE:
            raise ChiffrageError("Ce devis est déjà en brouillon : il se modifie directement.")
        if Commande.objects.filter(devis=devis).exists():
            raise ChiffrageError(
                f"Le devis « {devis} » est déjà devenu une commande : il ne se modifie plus "
                "(modifiez les lignes de la commande, les changements sont tracés)."
            )
        if devis.issue == Devis.Issue.REMPLACE:
            raise ChiffrageError(f"Le devis « {devis} » a été remplacé par un indice plus récent : modifiez ce dernier.")
        devis.statut = Devis.Statut.BROUILLON
        if devis.issue == Devis.Issue.REFUSE:
            devis.issue = Devis.Issue.EN_ATTENTE
            devis.motif_refus = ""
        devis._change_reason = "Repassé en brouillon pour modification (même indice)"
        devis.save()
    return devis


def comparer_indices(precedent, courant):
    """Ce qui change entre deux indices d'un devis, ligne par ligne (par article) :
    [{"article", "etat": ajoutee|supprimee|modifiee|identique, "avant": (qté, prix HT), "apres": (qté, prix HT)}],
    plus les totaux HT. Un prix est None tant que l'indice n'est pas (re)chiffré."""
    def indexer(devis):
        lignes = {}
        for ligne in devis.lignes.select_related("article").all():
            cle = ligne.article_id
            while cle in lignes:  # même article sur deux lignes : on les distingue
                cle = f"{cle}#"
            lignes[cle] = ligne
        return lignes

    avant, apres = indexer(precedent), indexer(courant)
    resultat = []
    for cle in list(avant) + [c for c in apres if c not in avant]:
        a, b = avant.get(cle), apres.get(cle)
        va = (a.quantite, a.prix_vente_total) if a else None
        vb = (b.quantite, b.prix_vente_total) if b else None
        if a is None:
            etat = "ajoutee"
        elif b is None:
            etat = "supprimee"
        else:
            etat = "identique" if va == vb else "modifiee"
        resultat.append({"article": (a or b).article.reference, "etat": etat, "avant": va, "apres": vb})
    return {
        "lignes": resultat,
        "total_avant": precedent.montant_total_ht,
        "total_apres": courant.montant_total_ht if all(l.prix_vente_total is not None for l in courant.lignes.all()) else None,
    }
