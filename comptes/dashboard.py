"""Tableau de bord de la page d'accueil de l'admin (UNFOLD["DASHBOARD_CALLBACK"]).

Chiffres calculés à la volée à partir des données déjà en base — aucune
table dédiée, à l'image de l'app pilotage.
"""

import datetime

from django.db.models import Sum
from django.utils import timezone

from chiffrage.models import Devis, OrdreFabrication
from facturation.models import Facture
from stock.models import AlerteStock


def dashboard_callback(request, context):
    aujourdhui = timezone.localdate()
    debut_mois = aujourdhui.replace(day=1)

    devis_brouillon = Devis.objects.filter(statut=Devis.Statut.BROUILLON).count()
    devis_valides_mois = Devis.objects.filter(
        statut=Devis.Statut.VALIDE, date_creation__gte=debut_mois
    ).count()
    ca_facture_mois = (
        Facture.objects.filter(date_facturation__gte=debut_mois).aggregate(total=Sum("montant_ht"))["total"] or 0
    )
    alertes_stock_actives = AlerteStock.objects.filter(statut=AlerteStock.Statut.ACTIVE).count()
    of_lances_mois = OrdreFabrication.objects.filter(date_lancement__gte=debut_mois).count()

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
            "title": "CA facturé",
            "value": f"{ca_facture_mois:,.0f} €".replace(",", " "),
            "icon": "payments",
            "hint": "ce mois-ci (HT)",
            "link": "admin:facturation_facture_changelist",
            "link_query": "",
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
    ]
    context["dashboard_date"] = aujourdhui

    return context
