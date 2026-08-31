"""Service de création à la volée d'un article fabriqué (nomenclature + gamme)
et de sa ligne de devis, en une seule transaction — utilisé par le
constructeur de devis (voir builder_views.py).

Réutilise systématiquement `full_clean()` sur chaque objet créé, pour ne
jamais dupliquer les règles métier déjà posées sur les modèles (Phase 1/2).
"""

from django.db import transaction

from technique.models import Article, Gamme, Nomenclature

from .models import Devis, DevisLigne
from .moteur import ChiffrageError


def _valider_et_sauver(instance):
    try:
        instance.full_clean()
    except Exception as exc:  # ValidationError Django
        raise ChiffrageError(_erreur_lisible(exc)) from exc
    instance.save()
    return instance


def _erreur_lisible(exc):
    message_dict = getattr(exc, "message_dict", None)
    if message_dict:
        return " ; ".join(f"{champ} : {', '.join(msgs)}" for champ, msgs in message_dict.items())
    messages = getattr(exc, "messages", None)
    if messages:
        return " ; ".join(messages)
    return str(exc)


@transaction.atomic
def creer_article_fabrique(reference, taux_marge_defaut, composants, etapes):
    """Crée un article fabriqué avec sa nomenclature et sa gamme.

    `composants` : liste de dicts {article_composant, quantite, longueur_mm, largeur_mm}
    `etapes` : liste de dicts {poste, ordre, temps_fixe, temps_variable, cout_forfaitaire, date_debut}
    """
    if Article.objects.filter(pk=reference).exists():
        raise ChiffrageError(f"La référence « {reference} » existe déjà.")
    if not composants:
        raise ChiffrageError("Au moins un composant de nomenclature est requis.")
    if not etapes:
        raise ChiffrageError("Au moins une étape de gamme est requise.")

    article = Article(
        reference=reference,
        nature=Article.Nature.FABRIQUE,
        taux_marge_defaut=taux_marge_defaut,
    )
    _valider_et_sauver(article)

    for composant in composants:
        ligne = Nomenclature(
            article_parent=article,
            article_composant=composant["article_composant"],
            quantite=composant["quantite"],
            longueur_mm=composant.get("longueur_mm"),
            largeur_mm=composant.get("largeur_mm"),
        )
        _valider_et_sauver(ligne)

    for etape in etapes:
        gamme = Gamme(
            article=article,
            poste=etape["poste"],
            ordre=etape["ordre"],
            temps_fixe=etape.get("temps_fixe"),
            temps_variable=etape.get("temps_variable"),
            cout_forfaitaire=etape.get("cout_forfaitaire"),
            date_debut=etape["date_debut"],
        )
        _valider_et_sauver(gamme)

    return article


@transaction.atomic
def ajouter_ligne_devis(devis, article, quantite):
    if devis.statut != Devis.Statut.BROUILLON:
        raise ChiffrageError("Seul un devis en brouillon peut être modifié depuis le constructeur.")
    ligne = DevisLigne(devis=devis, article=article, quantite=quantite)
    return _valider_et_sauver(ligne)
