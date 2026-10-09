"""Chronologie d'affaire : le fil de l'offre à l'encaissement (devis et indices, commande, ordres de fabrication, livraisons,
factures, avoirs, relances, paiements), reconstitué à partir des documents existants et de leur historique.

On part d'un devis, d'une commande, d'une livraison ou d'une facture : on remonte à l'affaire (toute la chaîne des indices du devis
et ses commandes) puis on liste les événements par date, ceux de demain et d'après (échéances, livraisons prévues) à part.
Chaque événement mène au document concerné ; le document affiché est repéré. Aucune donnée n'est stockée : tout est recalculé."""

import datetime
from dataclasses import dataclass

from django.urls import reverse
from django.utils import timezone


@dataclass
class Evenement:
    date: datetime.date
    ordre: int  # départage les événements d'un même jour dans l'ordre logique du flux
    icone: str
    titre: str
    detail: str = ""
    url: str = ""
    ton: str = "normal"  # normal | ok | attention | ko
    courant: bool = False
    futur: bool = False


def _en_date(valeur):
    if isinstance(valeur, datetime.datetime):
        return timezone.localtime(valeur).date() if timezone.is_aware(valeur) else valeur.date()
    return valeur


def _url(nom, pk):
    return reverse(f"admin:{nom}", args=[pk])


def _euros(valeur):
    return "" if valeur is None else f"{valeur:,.2f} €".replace(",", " ").replace(".", ",")


def _transitions(objet, champ):
    """[(date, ancienne valeur, nouvelle valeur)] du champ d'après l'historique (simple_history)."""
    try:
        lignes = list(objet.history.order_by("history_date", "history_id").values_list("history_date", champ))
    except Exception:
        return []
    resultat, precedent = [], None
    for date, valeur in lignes:
        if valeur != precedent:
            resultat.append((_en_date(date), precedent, valeur))
        precedent = valeur
    return resultat


def _racine(objet):
    """(devis racine ou None, commandes, document courant) de l'affaire de l'objet."""
    from chiffrage.models import Commande, Devis, Livraison, OrdreFabrication
    from facturation.models import Facture

    if isinstance(objet, Devis):
        return objet, None
    commande = objet
    if isinstance(objet, (Livraison, OrdreFabrication, Facture)):
        commande = objet.commande
    if isinstance(commande, Commande):
        return (commande.devis.racine if commande.devis_id else None), commande
    return None, None


def evenements(objet):
    from chiffrage.models import Commande, Devis
    from facturation.models import Facture

    aujourdhui = timezone.localdate()
    devis_racine, commande_courante = _racine(objet)
    evts = []

    def ajouter(date, ordre, icone, titre, detail="", url="", ton="normal", courant=False, futur=None):
        if date is None:
            return
        evts.append(Evenement(date, ordre, icone, titre, detail, url, ton, courant, date > aujourdhui if futur is None else futur))

    if devis_racine is not None:
        versions = devis_racine.versions()
        commandes = list(Commande.objects.filter(devis__in=versions).order_by("date_commande"))
    elif commande_courante is not None:
        versions, commandes = [], [commande_courante]
    else:
        return []

    for devis in versions:
        est_courant = isinstance(objet, Devis) and devis.pk == objet.pk
        lien = _url("chiffrage_devis_change", devis.pk)
        indice = f" (indice {devis.indice})" if len(versions) > 1 else ""
        ajouter(devis.date_creation, 10, "edit_note", f"Devis {devis.numero} créé{indice}",
                devis.motif_revision if devis.revision > 1 else "", lien, courant=est_courant)
        for date, avant, apres in _transitions(devis, "statut"):
            if apres == Devis.Statut.VALIDE:
                ajouter(date, 11, "send", f"Devis {devis.numero} validé", "Offre prête à envoyer au client", lien, "ok", est_courant)
        for date, avant, apres in _transitions(devis, "issue"):
            if apres == Devis.Issue.ACCEPTE:
                ajouter(date, 12, "task_alt", f"Devis {devis.numero} accepté", "", lien, "ok", est_courant)
            elif apres == Devis.Issue.REFUSE:
                ajouter(date, 12, "cancel", f"Devis {devis.numero} refusé", devis.motif_refus, lien, "ko", est_courant)
            elif apres == Devis.Issue.REMPLACE:
                ajouter(date, 12, "swap_horiz", f"Devis {devis.numero} remplacé par une révision", "", lien, "normal", est_courant)
        if devis.statut == Devis.Statut.VALIDE and devis.issue == Devis.Issue.EN_ATTENTE and devis.date_validite:
            ton = "ko" if devis.date_validite < aujourdhui else "attention"
            ajouter(devis.date_validite, 13, "hourglass_top", f"Fin de validité de l'offre {devis.numero}",
                    "Expirée : à relancer ou à refuser" if devis.date_validite < aujourdhui else "Sans réponse du client : à relancer",
                    lien, ton, est_courant, futur=devis.date_validite >= aujourdhui)

    for commande in commandes:
        est_courante = isinstance(objet, Commande) and commande.pk == objet.pk
        lien = _url("chiffrage_commande_change", commande.pk)
        ajouter(commande.date_commande, 20, "shopping_cart", f"Commande {commande.numero}", commande.reference_client and f"Réf. client {commande.reference_client}", lien, "ok", est_courante)
        for date, _avant, apres in _transitions(commande, "statut"):
            if apres == Commande.Statut.SOLDEE:
                ajouter(date, 29, "check_circle", f"Commande {commande.numero} soldée", "", lien, "ok", est_courante)
            elif apres == Commande.Statut.ANNULEE:
                ajouter(date, 29, "block", f"Commande {commande.numero} annulée", "", lien, "ko", est_courante)
        for of in commande.ordres_fabrication.select_related("article"):
            lien_of = _url("chiffrage_ordrefabrication_change", of.pk)
            courant_of = isinstance(objet, type(of)) and objet.pk == of.pk
            ajouter(of.date_lancement, 30, "build", f"OF {of.numero} lancé", f"{of.article_id} × {of.quantite:g}", lien_of, courant=courant_of)
            if of.statut_synchro == of.StatutSynchro.ECHEC_PERSISTANT:
                ajouter(of.date_derniere_tentative or of.date_lancement, 31, "sync_problem", f"OF {of.numero} non transmis au planning", of.derniere_erreur, lien_of, "ko", courant_of)
        for livraison in commande.livraisons.all():
            lien_bl = _url("chiffrage_livraison_change", livraison.pk)
            courant_bl = isinstance(objet, type(livraison)) and objet.pk == livraison.pk
            ajouter(livraison.date_livraison, 40, "local_shipping", f"Livraison {livraison.numero}", "", lien_bl, "ok", courant_bl)
            if livraison.date_annulation:
                ajouter(livraison.date_annulation, 41, "undo", f"Livraison {livraison.numero} annulée", livraison.motif_annulation, lien_bl, "ko", courant_bl)
        livrees = {d for d in commande.livraisons.values_list("date_livraison", flat=True)}
        prevues = {of.date_livraison_prevue for of in commande.ordres_fabrication.all() if of.date_livraison_prevue}
        if commande.statut == Commande.Statut.EN_COURS and not livrees:
            for date in sorted(prevues):
                ajouter(date, 39, "event", "Livraison prévue", f"Commande {commande.numero}", lien, "attention" if date < aujourdhui else "normal", est_courante, futur=date >= aujourdhui)

        for facture in commande.factures.prefetch_related("relances"):
            lien_f = _url("facturation_facture_change", facture.pk)
            courante_f = isinstance(objet, Facture) and objet.pk == facture.pk
            nom = "Avoir" if facture.est_avoir else "Facture"
            ajouter(facture.date_facturation, 50, "receipt_long", f"{nom} {facture.numero}", _euros(facture.montant_ttc) + " TTC" + (f" · {facture.motif}" if facture.motif else ""), lien_f, "attention" if facture.est_avoir else "normal", courante_f)
            for relance in facture.relances.all():
                ajouter(_en_date(relance.date_envoi), 52, "mark_email_unread", f"Relance {relance.niveau} de {facture.numero}", relance.destinataire if relance.envoyee else f"Échec d'envoi : {relance.erreur}", lien_f, "attention" if relance.envoyee else "ko", courante_f)
            if facture.statut_paiement == facture.StatutPaiement.PAYE and facture.date_paiement:
                ajouter(facture.date_paiement, 53, "payments", f"Facture {facture.numero} payée", "", lien_f, "ok", courante_f)
            elif not facture.est_avoir and facture.statut_paiement != facture.StatutPaiement.PAYE and facture.date_echeance:
                ajouter(facture.date_echeance, 51, "schedule", f"Échéance de {facture.numero}", "En retard : à relancer" if facture.est_en_retard else "Règlement attendu",
                        lien_f, "ko" if facture.est_en_retard else "normal", courante_f, futur=facture.date_echeance >= aujourdhui)
    evts.sort(key=lambda e: (e.date, e.ordre))
    return evts


def chronologie(objet):
    """{"passes": [...], "a_venir": [...], "aujourdhui": date, "titre": …} prêt à afficher ; None si l'objet n'a pas d'affaire."""
    liste = evenements(objet)
    if not liste:
        return None
    return {
        "passes": [e for e in liste if not e.futur],
        "a_venir": [e for e in liste if e.futur],
        "aujourdhui": timezone.localdate(),
    }
