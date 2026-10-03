"""Relances de paiement : choix du destinataire, texte selon le niveau, envoi et trace."""

import datetime

from django.conf import settings
from django.core.mail import EmailMessage
from django.utils import timezone

from comptes.models import Societe

from .models import Facture, RelanceFacture, facture_verrouillee


class RelanceError(Exception):
    """Relance impossible ou échouée."""


NIVEAUX = {
    1: ("Rappel : facture {numero} arrivée à échéance", (
        "Bonjour,\n\n"
        "Sauf erreur de notre part, la facture {numero} du {date_facture} d'un montant de {montant} TTC, "
        "échue le {echeance}, n'a pas encore été réglée.\n\n"
        "Si votre règlement est déjà parti, merci de ne pas tenir compte de ce message. Dans le cas "
        "contraire, nous vous remercions de bien vouloir y procéder dans les meilleurs délais.\n"
    )),
    2: ("Relance : facture {numero} impayée ({jours} jours de retard)", (
        "Bonjour,\n\n"
        "Malgré notre précédent rappel, la facture {numero} du {date_facture} ({montant} TTC), échue le "
        "{echeance}, reste impayée à ce jour, soit {jours} jours de retard.\n\n"
        "Nous vous prions de la régler sous huit jours, ou de nous contacter si un point bloque son paiement.\n"
    )),
    3: ("Dernière relance avant mesures de recouvrement : facture {numero}", (
        "Bonjour,\n\n"
        "La facture {numero} du {date_facture} ({montant} TTC), échue le {echeance}, demeure impayée "
        "malgré nos relances, soit {jours} jours de retard.\n\n"
        "À défaut de règlement sous huit jours, nous serons contraints d'engager les mesures de recouvrement "
        "appropriées ; les pénalités de retard prévues au contrat ou par la loi pourront s'appliquer.\n"
    )),
}


def _montant(valeur):
    return f"{valeur:,.2f} €".replace(",", " ").replace(".", ",") if valeur is not None else "—"


def choisir_destinataire(client):
    """Contact à relancer : celui dont la fonction évoque la comptabilité, sinon le contact principal,
    sinon le premier contact qui a une adresse e-mail."""
    contacts = [c for c in client.contacts.all() if c.email]
    if not contacts:
        return None
    for contact in contacts:
        if any(mot in (contact.fonction or "").lower() for mot in ("compta", "factur", "financ")):
            return contact
    principaux = [c for c in contacts if c.est_principal]
    return (principaux or contacts)[0]


def composer(facture, niveau):
    societe = Societe.charger()
    echeance = facture.date_echeance
    jours = (timezone.localdate() - echeance).days if echeance else 0
    valeurs = {
        "numero": facture.numero,
        "date_facture": f"{facture.date_facturation:%d/%m/%Y}",
        "echeance": f"{echeance:%d/%m/%Y}" if echeance else "—",
        "montant": _montant(facture.montant_ttc),
        "jours": jours,
    }
    objet, corps = NIVEAUX[niveau]
    corps = corps.format(**valeurs)
    signature = f"\nCordialement,\n{societe.raison_sociale}"
    if societe.telephone:
        signature += f"\nTél. {societe.telephone}"
    if societe.iban:
        signature += f"\n\nPour régler par virement : IBAN {societe.iban}" + (f" — BIC {societe.bic}" if societe.bic else "")
    return objet.format(**valeurs), corps + signature


def niveau_suivant(facture):
    envoyees = facture.relances.filter(envoyee=True).count()
    return min(envoyees + 1, max(NIVEAUX))


def relancer_facture(facture, utilisateur=None):
    if facture.est_avoir:
        raise RelanceError("On ne relance pas un avoir.")
    if facture.statut_paiement == Facture.StatutPaiement.PAYE:
        raise RelanceError(f"« {facture} » est déjà payée.")
    if not facture_verrouillee(facture):
        raise RelanceError(f"« {facture} » n'est pas encore émise : il n'y a rien à relancer.")
    if not facture.est_en_retard:
        raise RelanceError(
            f"« {facture} » n'est pas en retard de paiement"
            + ("" if facture.date_echeance else " (échéance inconnue : renseignez les conditions de paiement du client)") + "."
        )
    derniere = facture.relances.filter(envoyee=True).order_by("-date_envoi").first()
    delai = datetime.timedelta(days=getattr(settings, "RELANCE_DELAI_MIN_JOURS", 7))
    if derniere and timezone.now() - derniere.date_envoi < delai:
        raise RelanceError(
            f"« {facture} » a déjà été relancée le {timezone.localtime(derniere.date_envoi):%d/%m/%Y} : "
            f"attendez {delai.days} jours entre deux relances."
        )
    contact = choisir_destinataire(facture.commande.client)
    if contact is None:
        raise RelanceError(f"Aucun contact avec une adresse e-mail pour le client « {facture.commande.client} ».")

    niveau = niveau_suivant(facture)
    objet, corps = composer(facture, niveau)
    societe = Societe.charger()
    message = EmailMessage(
        subject=objet, body=corps, from_email=settings.DEFAULT_FROM_EMAIL, to=[contact.email],
        reply_to=[societe.email] if societe.email else None,
    )
    relance = RelanceFacture(
        facture=facture, niveau=niveau, destinataire=contact.email, objet=objet, corps=corps, utilisateur=utilisateur
    )
    try:
        message.send(fail_silently=False)
    except Exception as exc:  # serveur SMTP injoignable, refus d'authentification...
        relance.envoyee = False
        relance.erreur = str(exc)[:255]
        relance.save()
        raise RelanceError(f"L'envoi à {contact.email} a échoué : {exc}") from exc
    relance.save()
    return relance
