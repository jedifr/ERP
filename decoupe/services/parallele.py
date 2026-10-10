"""Calcul parallèle des imbrications : les formats de tôle d'une comparaison se calculent chacun dans un processus, donc sur plusieurs
cœurs du processeur (un calcul d'imbrication, seul, n'en utilise qu'un).

Nombre de processus : `ERP_IMBRICATION_PROCESSUS` (variable d'environnement, voir config/settings.py) ; 0 ou absent = automatique : nombre de
cœurs moins un (on en laisse un à la base de données et au système), plafonné à 6. 1 = pas de parallélisme.
Les processus sont créés une fois par processus du serveur (`fork` : ils héritent des bibliothèques déjà chargées) et ne font que du calcul
pur : jamais d'accès à la base de données. En cas de problème (processus tué, mémoire), on retombe sur le calcul séquentiel."""

import logging
import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool

from django.conf import settings

logger = logging.getLogger(__name__)
PLAFOND_AUTO = 6
_executeur = None
_executeur_pid = None


def processus():
    """Nombre de processus de calcul à utiliser (1 = séquentiel)."""
    voulu = int(getattr(settings, "IMBRICATION_PROCESSUS", 0) or 0)
    if voulu >= 1:
        return voulu
    return max(1, min((os.cpu_count() or 1) - 1, PLAFOND_AUTO))


def _executeur_pret():
    global _executeur, _executeur_pid
    # Le serveur peut se dupliquer (gunicorn) : chaque processus du serveur a son propre ensemble de processus de calcul.
    if _executeur is None or _executeur_pid != os.getpid():
        _executeur = ProcessPoolExecutor(max_workers=processus(), mp_context=multiprocessing.get_context("fork"))
        _executeur_pid = os.getpid()
    return _executeur


def _tache(arguments):
    from .imbrication import imbriquer_meilleur

    items, largeur_x, hauteur_y, marge, espacement, forme, axe = arguments
    return imbriquer_meilleur(items, largeur_x, hauteur_y, marge_bord_mm=marge, espacement_pieces_mm=espacement, forme=forme, axe=axe)


def calculer(taches):
    """{clé: arguments d'imbriquer_meilleur} → {clé: résultat} calculés en parallèle. Une tâche en échec est omise (calculée ensuite en
    séquentiel, qui lève la même erreur lisible) ; si les processus sont inutilisables, retourne {} (tout se fera en séquentiel)."""
    global _executeur
    try:
        executeur = _executeur_pret()
        futurs = {cle: executeur.submit(_tache, args) for cle, args in taches.items()}
        resultats = {}
        for cle, futur in futurs.items():
            try:
                resultats[cle] = futur.result()
            except BrokenProcessPool:
                raise
            except Exception:  # noqa: BLE001 — l'erreur sera reproduite (et expliquée) par le calcul séquentiel
                logger.info("Imbrication parallèle : tâche en échec, reprise en séquentiel", exc_info=True)
        return resultats
    except (BrokenProcessPool, OSError, RuntimeError):
        logger.warning("Imbrication parallèle indisponible : calcul séquentiel", exc_info=True)
        _executeur = None
        return {}
