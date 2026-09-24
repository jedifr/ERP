"""Génération de codes/numéros automatiques (préfixe + numéro sur N chiffres),
configurable par entité via RegleCodification (voir modèle et README —
section "Codification paramétrable").
"""

import datetime
import re

from django.db import transaction

from .models import RegleCodification


def _formater(regle, numero, annee):
    chaine_numero = str(numero).zfill(regle.nombre_chiffres)
    if regle.reinitialisation == RegleCodification.Reinitialisation.ANNUELLE:
        return f"{regle.prefixe}{annee}-{chaine_numero}"
    return f"{regle.prefixe}{chaine_numero}"


def generer_code(entite):
    """Aperçu du prochain code de la règle `entite` (compteur_actuel + 1), ou
    None si aucune règle n'est configurée pour cette entité (dans ce cas
    l'appelant garde son comportement d'origine : champ laissé vide).

    Ne consomme PAS le compteur : un simple aperçu, pas une réservation.
    Rappeler cette fonction plusieurs fois sans jamais enregistrer d'objet
    renvoie toujours le même code — voir enregistrer_code_utilise(), qui
    fait réellement avancer le compteur, uniquement quand un objet est
    effectivement créé (CodificationInitialeMixin.save_model). Avant ce
    changement, le compteur avançait dès l'affichage du formulaire d'ajout :
    un formulaire consulté puis abandonné sans être enregistré « sautait »
    un numéro, produisant des trous dans la séquence."""
    try:
        regle = RegleCodification.objects.get(pk=entite)
    except RegleCodification.DoesNotExist:
        return None

    annee_actuelle = datetime.date.today().year
    compteur = regle.compteur_actuel
    if (
        regle.reinitialisation == RegleCodification.Reinitialisation.ANNUELLE
        and regle.annee_compteur != annee_actuelle
    ):
        compteur = 0

    return _formater(regle, compteur + 1, annee_actuelle)


@transaction.atomic
def enregistrer_code_utilise(entite, code):
    """Fait avancer le compteur de la règle `entite` pour qu'il reflète
    `code`, si celui-ci correspond au format de la règle (préfixe +
    éventuellement année + numéro) — ne fait rien sinon (l'utilisateur a
    remplacé le code proposé par tout autre chose, rien à synchroniser).

    À appeler uniquement après l'enregistrement RÉEL d'un nouvel objet,
    jamais à la prévisualisation (voir generer_code) : un formulaire rempli
    puis abandonné ne touche donc jamais le compteur. Un code tapé au-dessus
    de la suggestion fait quand même avancer le compteur, pour ne pas
    provoquer de collision au prochain aperçu."""
    try:
        regle = RegleCodification.objects.select_for_update().get(pk=entite)
    except RegleCodification.DoesNotExist:
        return

    prefixe = re.escape(regle.prefixe)
    if regle.reinitialisation == RegleCodification.Reinitialisation.ANNUELLE:
        motif = re.fullmatch(rf"{prefixe}(\d{{4}})-(\d+)", code)
        if not motif:
            return
        annee_code, numero_code = int(motif.group(1)), int(motif.group(2))
        if regle.annee_compteur != annee_code:
            # Le code utilisé porte une année différente de celle du
            # compteur stocké : les numéros ne se comparent pas d'une année
            # sur l'autre, on aligne simplement le compteur sur cette année.
            regle.annee_compteur = annee_code
            regle.compteur_actuel = numero_code
            regle.save()
        elif numero_code > regle.compteur_actuel:
            regle.compteur_actuel = numero_code
            regle.save()
        return

    motif = re.fullmatch(rf"{prefixe}(\d+)", code)
    if not motif:
        return
    numero_code = int(motif.group(1))
    if numero_code > regle.compteur_actuel:
        regle.compteur_actuel = numero_code
        regle.save()
