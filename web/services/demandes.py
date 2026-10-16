"""Demande envoyée depuis le panier du site client : les prix sont recalculés ici, jamais repris du navigateur."""

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction

from comptes.montants import D, arrondir

from .prix import ErreurPrixWeb, chiffrer, chiffrer_jeu

LIGNES_MAX = 50


def enregistrer_demande(donnees):
    """Crée une DemandeWeb depuis {client: {nom, societe, email, telephone, adresse, message}, lignes: [demande de prix]}.
    Les lignes qui portent le même `jeu` (pièces d'un même dessin vectorisé) sont chiffrées ensemble (chiffrer_jeu) : chacune
    reçoit sa part du prix du jeu.
    Lève ErreurPrixWeb (message pour le client) si la demande est incomplète ou si une pièce n'a pas de prix en ligne."""
    from web.models import DemandeWeb, ReglageWeb

    client = donnees.get("client") or {}
    lignes = donnees.get("lignes") or []
    nom, email = str(client.get("nom") or "").strip(), str(client.get("email") or "").strip()
    if not nom or not email:
        raise ErreurPrixWeb("Indiquez votre nom et votre e-mail.")
    try:
        validate_email(email)
    except ValidationError:
        raise ErreurPrixWeb("Cet e-mail n'est pas valide.")
    if not isinstance(lignes, list) or not lignes:
        raise ErreurPrixWeb("Le panier est vide.")
    if len(lignes) > LIGNES_MAX:
        raise ErreurPrixWeb(f"Au-delà de {LIGNES_MAX} pièces différentes, envoyez une demande de devis.")

    for i, ligne in enumerate(lignes, start=1):
        if not isinstance(ligne, dict):
            raise ErreurPrixWeb(f"Pièce {i} illisible.")
    jeux = {}
    for ligne in lignes:
        if ligne.get("jeu"):
            jeux.setdefault(str(ligne["jeu"])[:64], []).append(ligne)
    prix_jeux = {}
    for cle, membres in jeux.items():
        j = chiffrer_jeu(membres)
        if j.statut == "refus":
            raise ErreurPrixWeb(f"Jeu de {len(membres)} pièces : pas de prix en ligne. " + " ".join(j.refus))
        for ligne, r in zip(membres, j.pieces):
            prix_jeux[id(ligne)] = r

    enregistrees, total, a_valider = [], D(0), False
    for i, ligne in enumerate(lignes, start=1):
        r = prix_jeux.get(id(ligne)) or chiffrer(
            ligne.get("matiere"), ligne.get("epaisseur"), ligne.get("quantite"), forme=ligne.get("forme"), cotes=ligne.get("cotes"),
            pliage=ligne.get("pliage"), libre=ligne.get("libre"), dxf=ligne.get("dxf"), echelle=ligne.get("echelle"),
            bibliotheque=ligne.get("bibliotheque"))
        if r.statut == "refus":
            raise ErreurPrixWeb(f"Pièce {i} ({r.designation or 'sans nom'}) : pas de prix en ligne. " + " ".join(r.refus))
        a_valider |= r.statut == "validation"
        total += r.prix_total_ht
        demande = {k: ligne.get(k) for k in ("matiere", "epaisseur", "quantite", "forme", "cotes", "pliage", "libre", "dxf", "echelle", "bibliotheque", "jeu") if ligne.get(k) is not None}
        enregistrees.append({
            "demande": demande, "designation": r.designation, "statut": r.statut, "validations": r.validations,
            "procede": r.procede, "prix_unitaire_ht": float(r.prix_unitaire_ht), "prix_total_ht": float(r.prix_total_ht),
            "cout_revient": float(r.cout_revient), "poids_kg": r.poids_kg,
            # contour découpé : de quoi sortir le DXF de fabrication depuis la demande (pièce libre ou forme)
            "contour": r.contour if not ligne.get("pliage") else None,
            **({"jeu": demande["jeu"], "format_tole": r.format_tole} if ligne.get("jeu") else {}),
        })
    minimum = ReglageWeb.charger().minimum_commande_ht
    champ = lambda cle, n: str(client.get(cle) or "").strip()[:n]
    with transaction.atomic():
        return DemandeWeb.objects.create(
            nom=nom[:120], societe=champ("societe", 160), email=email, telephone=champ("telephone", 30), adresse=champ("adresse", 1000),
            message=champ("message", 4000), lignes=enregistrees, total_pieces_ht=arrondir(total), total_ht=arrondir(max(total, minimum)),
            a_valider=a_valider,
        )
