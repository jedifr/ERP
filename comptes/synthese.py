"""Synthèse quotidienne : ce qui demande une action aujourd'hui, en un seul message.

Factures en retard, devis à relancer, alertes de stock, ordres de fabrication non transmis au
planning. Envoyée par `manage.py synthese_quotidienne` (à planifier chaque matin)."""

import datetime

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import EmailMessage
from django.utils import timezone

from chiffrage.models import Devis, OrdreFabrication
from facturation.models import Facture
from stock.models import AlerteStock

from .models import Societe


def _euros(valeur):
    return f"{valeur:,.0f} €".replace(",", " ")


def construire():
    """Sections de la synthèse : {titre: [lignes]} (les sections vides sont omises)."""
    aujourdhui = timezone.localdate()
    sections = {}

    en_retard = sorted(
        (
            f
            for f in Facture.objects.filter(type_document=Facture.TypeDocument.FACTURE)
            .exclude(statut_paiement=Facture.StatutPaiement.PAYE)
            .select_related("commande__client__conditions_paiement")
            if f.est_en_retard
        ),
        key=lambda f: f.date_echeance,
    )
    if en_retard:
        total = sum(f.montant_ttc or 0 for f in en_retard)
        lignes = [f"{len(en_retard)} facture(s) en retard, {_euros(total)} TTC à encaisser :"]
        for f in en_retard[:15]:
            jours = (aujourdhui - f.date_echeance).days
            lignes.append(f"  - {f.numero} — {f.commande.client.raison_sociale} — {_euros(f.montant_ttc or 0)} — {jours} j de retard")
        if len(en_retard) > 15:
            lignes.append(f"  … et {len(en_retard) - 15} autre(s).")
        sections["Factures en retard de paiement"] = lignes

    offres = Devis.objects.filter(
        statut=Devis.Statut.VALIDE, issue=Devis.Issue.EN_ATTENTE, date_validite__isnull=False,
        date_validite__lte=aujourdhui + datetime.timedelta(days=7),
    ).select_related("client").order_by("date_validite")
    if offres:
        sections["Devis à relancer (expirés ou expirant sous 7 jours)"] = [
            f"  - {d.numero} — {d.client.raison_sociale} — "
            + ("expiré le " if d.date_validite < aujourdhui else "expire le ") + f"{d.date_validite:%d/%m/%Y}"
            for d in offres
        ]

    alertes = AlerteStock.objects.filter(statut=AlerteStock.Statut.ACTIVE).select_related("article")
    if alertes:
        sections["Alertes de stock actives"] = [f"  - {a.article} depuis le {a.date_declenchement:%d/%m/%Y}" for a in alertes]

    non_transmis = OrdreFabrication.objects.exclude(statut_synchro=OrdreFabrication.StatutSynchro.SYNCHRONISE)
    if non_transmis:
        sections["Ordres de fabrication non transmis au planning"] = [
            f"  - {of.numero} ({of.get_statut_synchro_display()}, {of.nombre_tentatives} tentative(s))" for of in non_transmis[:20]
        ]
    return sections


def destinataires():
    if settings.SYNTHESE_DESTINATAIRES:
        return list(settings.SYNTHESE_DESTINATAIRES)
    return sorted(
        get_user_model().objects.filter(is_active=True, groups__name="Direction").exclude(email="").values_list("email", flat=True).distinct()
    )


def composer(sections):
    societe = Societe.charger()
    corps = [f"Synthèse du {timezone.localdate():%d/%m/%Y} — {societe.raison_sociale}", ""]
    for titre, lignes in sections.items():
        corps += [titre.upper(), *lignes, ""]
    return "\n".join(corps).rstrip() + "\n"


def envoyer(sections, a):
    message = EmailMessage(
        subject=f"[ERP] Synthèse du {timezone.localdate():%d/%m/%Y} : {len(sections)} point(s) à traiter",
        body=composer(sections), from_email=settings.DEFAULT_FROM_EMAIL, to=a,
    )
    return message.send(fail_silently=False)
