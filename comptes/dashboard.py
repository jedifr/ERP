"""Tableau de bord de la page d'accueil de l'admin (UNFOLD["DASHBOARD_CALLBACK"]).

Chiffres calculés à la volée à partir des données déjà en base — aucune
table dédiée, à l'image de l'app pilotage.
"""

import datetime

from django.db.models import Sum
from django.utils import timezone

from chiffrage.models import Devis, OrdreFabrication
from decoupe.models import PieceDecoupe
from facturation.models import Facture
from stock.models import AlerteStock

from .securite import diagnostics


def dashboard_callback(request, context):
    aujourdhui = timezone.localdate()
    debut_mois = aujourdhui.replace(day=1)

    devis_brouillon = Devis.objects.filter(statut=Devis.Statut.BROUILLON).count()
    devis_valides_mois = Devis.objects.filter(
        statut=Devis.Statut.VALIDE, date_creation__gte=debut_mois
    ).count()
    il_y_a_90_jours = aujourdhui - datetime.timedelta(days=90)
    offres = Devis.objects.filter(statut=Devis.Statut.VALIDE, date_creation__gte=il_y_a_90_jours).exclude(
        issue=Devis.Issue.REMPLACE
    )
    nb_offres = offres.count()
    taux_transformation = (
        f"{100 * offres.filter(issue=Devis.Issue.ACCEPTE).count() / nb_offres:.0f} %" if nb_offres else "—"
    )
    en_attente = Devis.objects.filter(statut=Devis.Statut.VALIDE, issue=Devis.Issue.EN_ATTENTE)
    a_relancer = en_attente.filter(date_validite__isnull=False, date_validite__lte=aujourdhui + datetime.timedelta(days=7))
    ca_facture_mois = (
        Facture.objects.filter(date_facturation__gte=debut_mois).aggregate(total=Sum("montant_ht"))["total"] or 0
    )
    factures_en_retard = [
        f
        for f in Facture.objects.filter(type_document=Facture.TypeDocument.FACTURE)
        .exclude(statut_paiement=Facture.StatutPaiement.PAYE)
        .select_related("commande__client__conditions_paiement")
        if f.est_en_retard
    ]
    montant_en_retard = sum(f.montant_ttc or 0 for f in factures_en_retard)
    alertes_stock_actives = AlerteStock.objects.filter(statut=AlerteStock.Statut.ACTIVE).count()
    of_lances_mois = OrdreFabrication.objects.filter(date_lancement__gte=debut_mois).count()
    pieces_decoupe_mois = PieceDecoupe.objects.filter(
        statut=PieceDecoupe.Statut.OK, date_import__gte=debut_mois
    ).count()

    context["kpis"] = [
        {
            "title": "Devis en brouillon",
            "value": devis_brouillon,
            "icon": "request_quote",
            "hint": "à traiter",
            "link": "admin:chiffrage_devis_changelist",
            "link_query": "?statut__exact=brouillon",
        },
        {
            "title": "Devis validés",
            "value": devis_valides_mois,
            "icon": "task_alt",
            "hint": "ce mois-ci",
            "link": "admin:chiffrage_devis_changelist",
            "link_query": "?statut__exact=valide",
        },
        {
            "title": "Devis sans réponse",
            "value": en_attente.count(),
            "icon": "hourglass_top",
            "hint": f"{a_relancer.count()} à relancer (expirés ou sous 7 jours)",
            "link": "admin:chiffrage_devis_changelist",
            "link_query": "?statut__exact=valide&issue__exact=en_attente",
            "attention": a_relancer.exists(),
        },
        {
            "title": "Taux de transformation",
            "value": taux_transformation,
            "icon": "trending_up",
            "hint": f"devis acceptés sur {nb_offres} envoyés (90 j)",
            "link": "admin:chiffrage_devis_changelist",
            "link_query": "?statut__exact=valide",
        },
        {
            "title": "CA facturé",
            "value": f"{ca_facture_mois:,.0f} €".replace(",", " "),
            "icon": "payments",
            "hint": "ce mois-ci (HT, net d'avoirs)",
            "link": "admin:facturation_facture_changelist",
            "link_query": "",
        },
        {
            "title": "Factures en retard",
            "value": len(factures_en_retard),
            "icon": "schedule",
            "hint": f"{montant_en_retard:,.0f} € TTC à relancer".replace(",", " "),
            "link": "admin:facturation_facture_changelist",
            "link_query": "?retard=en_retard",
            "attention": bool(factures_en_retard),
        },
        {
            "title": "Alertes de stock",
            "value": alertes_stock_actives,
            "icon": "warning",
            "hint": "actives",
            "link": "admin:stock_alertestock_changelist",
            "link_query": "?statut__exact=active",
            "attention": alertes_stock_actives > 0,
        },
        {
            "title": "Ordres de fabrication",
            "value": of_lances_mois,
            "icon": "build",
            "hint": "lancés ce mois-ci",
            "link": "admin:chiffrage_ordrefabrication_changelist",
            "link_query": "",
        },
        {
            "title": "Pièces à découper",
            "value": pieces_decoupe_mois,
            "icon": "content_cut",
            "hint": "importées ce mois-ci",
            "link": "admin:decoupe_piecedecoupe_changelist",
            "link_query": "",
        },
    ]
    context["dashboard_date"] = aujourdhui
    if getattr(getattr(request, "user", None), "is_superuser", False):
        context["alertes_securite"] = diagnostics()

    return context
