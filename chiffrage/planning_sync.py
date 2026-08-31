"""Client de synchronisation avec l'outil de planification d'atelier (NAS Synology).

Architecture retenue au cahier des charges : deux bases distinctes,
synchronisées via API, l'ERP restant la source de vérité pour les postes et
leurs tarifs. Aucune API n'est encore définie côté planning atelier : ce
module est le point d'intégration unique à adapter le jour où le contrat
d'API sera fixé — tant que PLANNING_API_URL n'est pas configuré, toute
tentative échoue proprement (l'OF reste "en_attente", jamais bloquant pour sa
création).
"""

import logging

import requests
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


class PlanningSyncError(Exception):
    """La synchronisation a échoué (réseau, HTTP, ou API non configurée)."""


class PlanningSyncClient:
    def __init__(self):
        self.base_url = settings.PLANNING_API_URL
        self.api_key = settings.PLANNING_API_KEY

    def envoyer_ordre_fabrication(self, of):
        if not self.base_url:
            raise PlanningSyncError("PLANNING_API_URL non configuré.")

        headers = {"Idempotency-Key": of.numero}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "numero": of.numero,
            "article": of.article_id,
            "quantite": of.quantite,
            "date_lancement": of.date_lancement.isoformat(),
            "operations": [
                {"poste": op.poste_id, "ordre": op.ordre, "temps_prevu": op.temps_prevu}
                for op in of.operations.order_by("ordre")
            ],
        }
        try:
            response = requests.post(
                f"{self.base_url.rstrip('/')}/ordres-fabrication",
                json=payload,
                headers=headers,
                timeout=10,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise PlanningSyncError(str(exc)) from exc


def tenter_synchronisation(of):
    """Tente une synchronisation et met à jour le statut de l'OF. Ne lève jamais."""
    client = PlanningSyncClient()
    of.date_derniere_tentative = timezone.now().date()
    try:
        client.envoyer_ordre_fabrication(of)
    except PlanningSyncError as exc:
        of.nombre_tentatives += 1
        if of.nombre_tentatives >= settings.PLANNING_SYNC_MAX_TENTATIVES:
            of.statut_synchro = of.StatutSynchro.ECHEC_PERSISTANT
        else:
            of.statut_synchro = of.StatutSynchro.EN_ATTENTE
        of.save(update_fields=["statut_synchro", "nombre_tentatives", "date_derniere_tentative"])
        logger.warning("Échec de synchronisation de l'OF %s : %s", of.numero, exc)
        return False

    of.statut_synchro = of.StatutSynchro.SYNCHRONISE
    of.save(update_fields=["statut_synchro", "date_derniere_tentative"])
    return True


def resynchroniser(of):
    """Action manuelle "Resynchroniser" : remet le compteur de tentatives à zéro."""
    of.nombre_tentatives = 0
    of.statut_synchro = of.StatutSynchro.EN_ATTENTE
    of.save(update_fields=["statut_synchro", "nombre_tentatives"])
    return tenter_synchronisation(of)
