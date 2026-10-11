"""Client de synchronisation avec l'outil de planification d'atelier (NAS Synology).

Architecture retenue au cahier des charges : deux bases distinctes,
synchronisées via API, l'ERP restant la source de vérité pour les postes et
leurs tarifs. Aucune API n'est encore définie côté planning atelier : ce
module est le point d'intégration unique à adapter le jour où le contrat
d'API sera fixé — tant que PLANNING_API_URL n'est pas configuré, toute
tentative échoue proprement (l'OF reste "en_attente", jamais bloquant pour sa
création).
"""

import datetime
import hashlib
import json
import logging

import requests
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


class PlanningSyncError(Exception):
    """La synchronisation a échoué (réseau, HTTP, ou API non configurée)."""


def construire_payload(of):
    payload = {
        "numero": of.numero,
        "article": of.article_id,
        "quantite": float(of.quantite),
        "date_lancement": of.date_lancement.isoformat(),
        "operations": [
            {"poste": op.poste_id, "ordre": op.ordre, "temps_prevu": None if op.temps_prevu is None else float(op.temps_prevu)}
            for op in of.operations.order_by("ordre")
        ],
    }
    # Ajoutés seulement s'ils existent : le contenu (donc l'empreinte) des OF antérieurs ne change pas.
    if of.date_livraison_prevue:
        payload["date_livraison_prevue"] = of.date_livraison_prevue.isoformat()
    composants = [
        {"article": c.article_id, "quantite": float(c.quantite_necessaire)} for c in of.composants.order_by("id")
    ]
    if composants:
        payload["composants"] = composants
    return payload


def empreinte(payload):
    """Empreinte stable du contenu envoyé : sert de clé d'idempotence et à
    détecter un OF modifié depuis son dernier envoi réussi."""
    brut = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(brut.encode()).hexdigest()[:16]


def delai_reprise(tentatives):
    """Attente avant la prochaine reprise automatique : 5 min, 10, 20… plafonnée à 6 h."""
    return datetime.timedelta(minutes=min(5 * 2 ** max(tentatives - 1, 0), 360))


def a_resynchroniser(of):
    """Vrai si l'OF est marqué synchronisé mais a changé depuis l'envoi.
    Une empreinte vide (OF synchronisé avant cette version) n'est pas considérée périmée."""
    return (
        of.statut_synchro == of.StatutSynchro.SYNCHRONISE
        and bool(of.empreinte_envoyee)
        and of.empreinte_envoyee != empreinte(construire_payload(of))
    )


class PlanningSyncClient:
    def __init__(self):
        self.base_url = settings.PLANNING_API_URL
        self.api_key = settings.PLANNING_API_KEY

    def envoyer_ordre_fabrication(self, of, payload=None):
        if not self.base_url:
            raise PlanningSyncError("PLANNING_API_URL non configuré.")

        payload = payload or construire_payload(of)
        # Même contenu = même clé (un renvoi après timeout ne crée pas de doublon
        # côté planning) ; contenu modifié = nouvelle clé (la mise à jour passe).
        headers = {"Idempotency-Key": f"{of.numero}-{empreinte(payload)}"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

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
    maintenant = timezone.now()
    of.date_derniere_tentative = maintenant.date()
    payload = construire_payload(of)
    try:
        client.envoyer_ordre_fabrication(of, payload)
    except PlanningSyncError as exc:
        of.nombre_tentatives += 1
        of.derniere_erreur = str(exc)[:500]
        if of.nombre_tentatives >= settings.PLANNING_SYNC_MAX_TENTATIVES:
            of.statut_synchro = of.StatutSynchro.ECHEC_PERSISTANT
            of.prochaine_tentative = None
        else:
            of.statut_synchro = of.StatutSynchro.EN_ATTENTE
            of.prochaine_tentative = maintenant + delai_reprise(of.nombre_tentatives)
        of.save(
            update_fields=[
                "statut_synchro",
                "nombre_tentatives",
                "date_derniere_tentative",
                "derniere_erreur",
                "prochaine_tentative",
            ]
        )
        logger.warning("Échec de synchronisation de l'OF %s : %s", of.numero, exc)
        return False

    of.statut_synchro = of.StatutSynchro.SYNCHRONISE
    of.derniere_erreur = ""
    of.prochaine_tentative = None
    of.empreinte_envoyee = empreinte(payload)
    of.save(
        update_fields=[
            "statut_synchro",
            "date_derniere_tentative",
            "derniere_erreur",
            "prochaine_tentative",
            "empreinte_envoyee",
        ]
    )
    return True


def resynchroniser(of):
    """Action manuelle "Resynchroniser" : remet le compteur de tentatives à zéro."""
    of.nombre_tentatives = 0
    of.statut_synchro = of.StatutSynchro.EN_ATTENTE
    of.prochaine_tentative = None
    of.save(update_fields=["statut_synchro", "nombre_tentatives", "prochaine_tentative"])
    return tenter_synchronisation(of)
