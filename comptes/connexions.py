"""Limitation des tentatives de connexion (anti force brute) et journal des connexions.

Règle : après `CONNEXION_ECHECS_MAX` échecs (5) en `CONNEXION_FENETRE_MINUTES` (15)
sur un même identifiant, plus aucune tentative n'est acceptée — même avec le bon mot de
passe — jusqu'à ce que les échecs sortent de la fenêtre. Une connexion réussie ou un
déblocage manuel remet le compteur à zéro. Le compteur est par identifiant (insensible à la
casse), volontairement pas par adresse IP : derrière le reverse proxy du NAS, toutes les
requêtes viennent de la même IP et un blocage par IP verrouillerait tout le monde."""

import datetime
import ipaddress

from django.conf import settings
from django.db.models import Max
from django.utils import timezone

from .models import EvenementConnexion

Type = EvenementConnexion.Type


def _echecs_max():
    return getattr(settings, "CONNEXION_ECHECS_MAX", 5)


def _fenetre():
    return datetime.timedelta(minutes=getattr(settings, "CONNEXION_FENETRE_MINUTES", 15))


def normaliser(identifiant):
    return (identifiant or "").strip().lower()[:150]


def adresse_ip(request):
    if request is None:
        return None
    brute = request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip() or request.META.get("REMOTE_ADDR", "")
    try:
        return str(ipaddress.ip_address(brute))
    except ValueError:
        return None


def enregistrer(identifiant, type_evenement, request=None):
    EvenementConnexion.objects.create(
        identifiant=normaliser(identifiant), type_evenement=type_evenement, adresse_ip=adresse_ip(request)
    )
    # Conservation limitée : l'historique ne grossit pas indéfiniment.
    EvenementConnexion.objects.filter(date__lt=timezone.now() - datetime.timedelta(days=180)).delete()


def _echecs_recents(identifiant):
    identifiant = normaliser(identifiant)
    debut = timezone.now() - _fenetre()
    remise_a_zero = EvenementConnexion.objects.filter(
        identifiant=identifiant, type_evenement__in=[Type.SUCCES, Type.DEBLOCAGE], date__gte=debut
    ).aggregate(dernier=Max("date"))["dernier"]
    echecs = EvenementConnexion.objects.filter(identifiant=identifiant, type_evenement=Type.ECHEC, date__gte=debut)
    if remise_a_zero:
        echecs = echecs.filter(date__gt=remise_a_zero)
    return echecs


def compte_verrouille(identifiant):
    return _echecs_recents(identifiant).count() >= _echecs_max()


def minutes_restantes(identifiant):
    """Minutes avant qu'une nouvelle tentative soit acceptée (0 si pas verrouillé)."""
    echecs = list(_echecs_recents(identifiant).order_by("date").values_list("date", flat=True))
    if len(echecs) < _echecs_max():
        return 0
    # Le verrou tombe quand assez d'échecs sont sortis de la fenêtre pour repasser sous le seuil.
    echeance = echecs[len(echecs) - _echecs_max()] + _fenetre()
    return max(1, int((echeance - timezone.now()).total_seconds() // 60) + 1)
