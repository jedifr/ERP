"""Création de factures et d'avoirs à partir des quantités réellement livrées et
facturées — jamais à partir du total de la commande."""

import datetime

from django.db import transaction
from django.utils import timezone

from codification.models import RegleCodification
from codification.services import enregistrer_code_utilise, generer_code

from .models import Facture, FactureLigne, facture_verrouillee


class FacturationError(Exception):
    """Opération de facturation impossible dans l'état actuel."""


def _numero_libre(base):
    numero, suffixe = base, 1
    while Facture.objects.filter(pk=numero).exists():
        suffixe += 1
        numero = f"{base}-{suffixe}"
    return numero


def reste_a_facturer(commande_ligne, anticipee=False):
    """Quantité qu'on peut encore facturer sur une ligne de commande : le livré (ou
    le commandé pour une facturation anticipée) moins le déjà facturé net d'avoirs."""
    plafond = commande_ligne.quantite_commandee if anticipee else min(
        commande_ligne.quantite_livree, commande_ligne.quantite_commandee
    )
    return max(round(plafond - FactureLigne.cumul_facture(commande_ligne), 6), 0)


def lignes_a_facturer(commande, anticipee=False):
    """[(ligne de commande, quantité restant à facturer)] — seulement celles > 0."""
    return [
        (ligne, reste)
        for ligne in commande.lignes.select_related("article", "taux_tva")
        if (reste := reste_a_facturer(ligne, anticipee)) > 0
    ]


def preparer_facture(commande, anticipee=False):
    """Crée une facture brouillon contenant le livré non encore facturé de la
    commande. Le numéro suit la règle de codification « Facture » si elle existe,
    sinon FAC-<commande>. Les montants sont déduits des lignes ; la référence Tiime
    reste à renseigner une fois la facture émise."""
    if commande.statut == commande.Statut.ANNULEE:
        raise FacturationError(f"La commande « {commande} » est annulée.")
    a_facturer = lignes_a_facturer(commande, anticipee)
    if not a_facturer:
        raise FacturationError(
            f"Rien à facturer sur « {commande} » : tout ce qui est livré est déjà facturé."
            if not anticipee
            else f"Rien à facturer sur « {commande} » : tout est déjà facturé."
        )
    with transaction.atomic():
        code = generer_code(RegleCodification.Entite.FACTURE)
        numero = _numero_libre(code or f"FAC-{commande.numero}")
        facture = Facture.objects.create(
            numero=numero,
            commande=commande,
            date_facturation=max(timezone.localdate(), commande.date_commande),
            mode_creation=Facture.ModeCreation.AUTOMATIQUE,
            anticipee=anticipee,
        )
        if code and numero == code:
            enregistrer_code_utilise(RegleCodification.Entite.FACTURE, numero)
        for ligne, quantite in a_facturer:
            FactureLigne.objects.create(facture=facture, commande_ligne=ligne, quantite=quantite)
        facture.remplir_montants_depuis_les_lignes()
    facture.refresh_from_db()
    return facture


def creer_avoir(facture, motif, utilisateur=None):
    """Avoir total (ou du solde restant à créditer) d'une facture émise ou
    comptabilisée : une ligne par ligne de la facture, pour ce qui n'a pas déjà
    été crédité. Pour un avoir partiel, saisir l'avoir à la main."""
    motif = (motif or "").strip()
    if not motif:
        raise FacturationError("Indiquez le motif de l'avoir.")
    if facture.est_avoir:
        raise FacturationError("On ne fait pas d'avoir sur un avoir.")
    if not facture_verrouillee(facture):
        raise FacturationError(
            f"« {facture} » n'est pas encore émise : modifiez-la ou supprimez-la plutôt que de la créditer."
        )
    a_crediter = []
    for ligne in facture.lignes.select_related("commande_ligne"):
        autres = FactureLigne.objects.filter(facture__facture_origine=facture, commande_ligne=ligne.commande_ligne)
        if ligne.commande_ligne_id is None:
            # Ligne directe : même article, désignation et prix.
            autres = autres.filter(article=ligne.article, designation=ligne.designation, prix_unitaire_ht=ligne.prix_unitaire_ht)
        deja = sum(autre.quantite for autre in autres)
        reste = round(ligne.quantite - deja, 6)
        if reste > 0:
            a_crediter.append((ligne, reste))
    if not facture.lignes.exists():
        raise FacturationError(
            f"« {facture} » n'a pas de lignes (facture antérieure aux lignes de facture) : saisissez l'avoir à la main."
        )
    if not a_crediter:
        raise FacturationError(f"« {facture} » est déjà intégralement créditée.")
    with transaction.atomic():
        avoir = Facture.objects.create(
            numero=_numero_libre(f"AV-{facture.numero}"),
            commande=facture.commande,
            client=facture.client,
            nom_client=facture.nom_client,
            adresse_client=facture.adresse_client,
            code_postal_client=facture.code_postal_client,
            ville_client=facture.ville_client,
            pays_client=facture.pays_client,
            type_document=Facture.TypeDocument.AVOIR,
            facture_origine=facture,
            motif=motif[:200],
            date_facturation=max(timezone.localdate(), facture.date_facturation),
            mode_creation=Facture.ModeCreation.AUTOMATIQUE,
        )
        for ligne, quantite in a_crediter:
            FactureLigne.objects.create(
                facture=avoir,
                commande_ligne=ligne.commande_ligne,
                article=ligne.article,
                designation=ligne.designation,
                livraison=ligne.livraison,
                quantite=quantite,
                prix_unitaire_ht=ligne.prix_unitaire_ht,
                taux_tva=ligne.taux_tva,
            )
        avoir._change_reason = f"Avoir créé : {motif}"[:100]
        avoir.remplir_montants_depuis_les_lignes()
    avoir.refresh_from_db()
    return avoir


def reste_a_facturer_livraison(livraison_ligne, anticipee=False):
    """Quantité d'une ligne de bon de livraison qu'on peut encore facturer : sa quantité livrée moins ce qui en a déjà été
    importé (net d'avoirs), sans dépasser le reste à facturer de la ligne de commande."""
    importe = 0.0
    for ligne in FactureLigne.objects.filter(
        livraison_id=livraison_ligne.livraison_id, commande_ligne_id=livraison_ligne.commande_ligne_id
    ).select_related("facture"):
        importe += -ligne.quantite if ligne.facture.est_avoir else ligne.quantite
    reste_bl = max(round(livraison_ligne.quantite_livree - importe, 6), 0)
    return min(reste_bl, reste_a_facturer(livraison_ligne.commande_ligne, anticipee))


def livraisons_a_facturer(client):
    """[(bon de livraison, [(ligne de livraison, quantité restant à facturer)])] du client : bons validés dont il reste
    quelque chose à facturer, du plus ancien au plus récent."""
    from chiffrage.models import Livraison

    resultat = []
    livraisons = (
        Livraison.objects.filter(commande__client=client, statut=Livraison.Statut.VALIDEE)
        .select_related("commande")
        .prefetch_related("lignes__commande_ligne__article")
        .order_by("date_livraison", "numero")
    )
    for livraison in livraisons:
        lignes = [(l, reste) for l in livraison.lignes.all() if (reste := reste_a_facturer_livraison(l)) > 0]
        if lignes:
            resultat.append((livraison, lignes))
    return resultat


def importer_livraisons(facture, livraisons):
    """Ajoute à la facture (brouillon) une ligne par ligne des bons de livraison donnés, pour ce qui en reste à facturer. Les
    bons doivent être validés et porter sur des commandes du client de la facture ; la première commande devient celle de la
    facture si elle n'en a pas. Les montants suivent le total des lignes s'ils le suivaient déjà. Renvoie le nombre de lignes
    ajoutées."""
    if facture.est_avoir:
        raise FacturationError("On n'importe pas un bon de livraison dans un avoir.")
    if facture_verrouillee(facture):
        raise FacturationError(f"« {facture} » est émise ou comptabilisée : on n'y ajoute plus de lignes.")
    if not livraisons:
        raise FacturationError("Choisissez au moins un bon de livraison.")
    for livraison in livraisons:
        if livraison.statut != livraison.Statut.VALIDEE:
            raise FacturationError(f"Le bon de livraison « {livraison} » est annulé.")
        if livraison.commande.client_id != facture.client_id:
            raise FacturationError(f"Le bon de livraison « {livraison} » n'est pas un bon de ce client.")
    suivait = facture.montants_suivent_les_lignes
    ajoutees = 0
    with transaction.atomic():
        if not facture.commande_id:
            facture.commande = livraisons[0].commande
            facture.save()
        for livraison in livraisons:
            for ligne in livraison.lignes.select_related("commande_ligne"):
                quantite = reste_a_facturer_livraison(ligne, facture.anticipee)
                if quantite <= 0:
                    continue
                nouvelle = FactureLigne(facture=facture, commande_ligne=ligne.commande_ligne, livraison=livraison, quantite=quantite)
                nouvelle.full_clean()
                nouvelle.save()
                ajoutees += 1
        if not ajoutees:
            raise FacturationError("Tout ce que portent ces bons de livraison est déjà facturé.")
        facture.suivre_montants_des_lignes(suivait)
    facture.refresh_from_db()
    return ajoutees
