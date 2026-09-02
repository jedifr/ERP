"""Génération de codes/numéros automatiques (préfixe + numéro sur N chiffres),
configurable par entité via RegleCodification (voir modèle et README —
section "Codification paramétrable").
"""

import datetime

from django.db import transaction

from .models import RegleCodification


def _formater(regle, numero, annee):
    chaine_numero = str(numero).zfill(regle.nombre_chiffres)
    if regle.reinitialisation == RegleCodification.Reinitialisation.ANNUELLE:
        return f"{regle.prefixe}{annee}-{chaine_numero}"
    return f"{regle.prefixe}{chaine_numero}"


@transaction.atomic
def generer_code(entite):
    """Consomme le prochain numéro de la règle `entite` et retourne le code
    formaté, ou None si aucune règle n'est configurée pour cette entité (dans
    ce cas l'appelant garde son comportement d'origine : champ laissé vide).

    Le compteur est incrémenté immédiatement — y compris si le formulaire
    d'ajout est ensuite abandonné sans être enregistré, puisque le code
    proposé reste un champ modifiable. Un numéro peut donc être « sauté » ;
    c'est un compromis assumé pour ne pas avoir à gérer de réservation
    temporaire à nettoyer.
    """
    try:
        regle = RegleCodification.objects.select_for_update().get(pk=entite)
    except RegleCodification.DoesNotExist:
        return None

    annee_actuelle = datetime.date.today().year
    if (
        regle.reinitialisation == RegleCodification.Reinitialisation.ANNUELLE
        and regle.annee_compteur != annee_actuelle
    ):
        regle.annee_compteur = annee_actuelle
        regle.compteur_actuel = 0

    regle.compteur_actuel += 1
    regle.save()

    return _formater(regle, regle.compteur_actuel, annee_actuelle)
