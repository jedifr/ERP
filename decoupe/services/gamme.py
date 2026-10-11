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
