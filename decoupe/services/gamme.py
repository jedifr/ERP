"""Alimentation de la gamme d'un article fabriqué avec le temps de découpe calculé de sa pièce à découper."""

import datetime

from django.db import transaction
from django.db.models import Max, Q

from technique.models import Article, Gamme

from .temps import ErreurTemps, estimer_temps_decoupe


def alimenter_gamme(piece, aujourdhui=None):
    """Crée ou met à jour l'étape « découpe » de la gamme de l'article lié à la pièce : temps variable = temps de
    découpe d'une pièce (minutes) sur le poste de la machine. Une étape déjà calculée est mise à jour (ou
    historisée si son tarif date d'un autre jour) ; une étape saisie à la main n'est jamais écrasée.
    Retourne (étape, estimation). Lève ErreurTemps si la pièce ne permet pas de calculer."""
    aujourdhui = aujourdhui or datetime.date.today()
    if not piece.article_id:
        raise ErreurTemps("Liez d'abord la pièce à son article fabriqué (champ « Article »).")
    if piece.article.nature != Article.Nature.FABRIQUE:
        raise ErreurTemps("Seul un article fabriqué porte une gamme.")
    estimation = estimer_temps_decoupe(piece)
    poste = estimation.parametre.poste
    if poste is None:
        raise ErreurTemps(f"Renseignez le poste de travail du paramètre de coupe « {estimation.parametre} ».")
    minutes = round(estimation.total_min, 3)

    with transaction.atomic():
        actives = Gamme.objects.filter(article=piece.article, date_debut__lte=aujourdhui).filter(
            Q(date_fin__isnull=True) | Q(date_fin__gte=aujourdhui)
        )
        existante = actives.filter(origine="decoupe").order_by("ordre").first()  # quel que soit le poste : on peut changer de machine
        if existante and existante.date_debut == aujourdhui:
            existante.temps_variable = minutes
            existante.poste = poste
            existante.save(update_fields=["temps_variable", "poste"])
            return existante, estimation
        if existante:
            existante.date_fin = aujourdhui - datetime.timedelta(days=1)
            existante.save(update_fields=["date_fin"])
            ordre = existante.ordre
            temps_fixe = 0  # le réglage de la découpe se compte par tôle (poste ou paramètre de coupe), pas par pièce
        else:
            ordre = (actives.aggregate(m=Max("ordre"))["m"] or 0) + 1
            temps_fixe = 0  # le réglage machine se saisit à la main ; un poste horaire exige un temps fixe
        etape = Gamme.objects.create(
            article=piece.article, poste=poste, ordre=ordre, temps_fixe=temps_fixe, temps_variable=minutes,
            date_debut=aujourdhui, origine="decoupe",
        )
    return etape, estimation


def alimenter_gamme_pliage(pliage, aujourdhui=None):
    """Calcule la pièce pliée et crée ou met à jour l'étape « pliage » de la gamme de son article, sur le poste de la
    plieuse retenue : temps fixe = réglages, temps variable = temps des plis d'une pièce (le moteur applique le temps
    de série selon la quantité). Une étape saisie à la main n'est jamais écrasée. Retourne (étape, résultat).
    Lève ErreurPliage si le paramétrage manque ou si le pliage est refusé."""
    from decoupe.models import Plieuse

    from .pliage import ErreurPliage, calculer_pliage_article

    aujourdhui = aujourdhui or datetime.date.today()
    resultat = calculer_pliage_article(pliage)
    if resultat.statut == "refus":
        raise ErreurPliage(" ".join(resultat.refus))
    poste = Plieuse.objects.get(pk=resultat.plieuse_id).poste
    if poste is None:
        raise ErreurPliage(f"Renseignez le poste de travail de la plieuse « {resultat.plieuse} » (Paramétrage → Pliage).")
    article = pliage.article
    valeurs = {"poste": poste, "temps_fixe": resultat.temps_reglage_min, "temps_variable": resultat.temps_pli_min}

    with transaction.atomic():
        actives = Gamme.objects.filter(article=article, date_debut__lte=aujourdhui).filter(
            Q(date_fin__isnull=True) | Q(date_fin__gte=aujourdhui)
        )
        existante = actives.filter(origine="pliage").order_by("ordre").first()
        if existante and existante.date_debut == aujourdhui:
            for champ, valeur in valeurs.items():
                setattr(existante, champ, valeur)
            existante.save(update_fields=list(valeurs))
            return existante, resultat
        if existante:
            existante.date_fin = aujourdhui - datetime.timedelta(days=1)
            existante.save(update_fields=["date_fin"])
            ordre = existante.ordre
        else:
            ordre = (actives.aggregate(m=Max("ordre"))["m"] or 0) + 1
        etape = Gamme.objects.create(article=article, ordre=ordre, date_debut=aujourdhui, origine="pliage", **valeurs)
    return etape, resultat
