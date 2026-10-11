from django.contrib import messages
from django.contrib.auth.backends import ModelBackend
from django.contrib.auth.signals import user_logged_in, user_login_failed
from django.core.exceptions import PermissionDenied
from django.dispatch import receiver

from . import connexions


class ModelBackendProtege(ModelBackend):
    """Authentification standard, mais refusée (même avec le bon mot de passe) tant que
    l'identifiant est verrouillé après trop d'échecs. S'applique à toute authentification :
    page de connexion de l'admin, API en authentification de base."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        identifiant = username or kwargs.get("username")
        if identifiant and connexions.compte_verrouille(identifiant):
            connexions.enregistrer(identifiant, connexions.Type.BLOQUE, request)
            if request is not None:
                messages.error(
                    request,
                    f"Trop de tentatives de connexion : compte verrouillé, réessayez dans "
                    f"{connexions.minutes_restantes(identifiant)} minute(s).",
                    fail_silently=True,
                )
            raise PermissionDenied
        return super().authenticate(request, username=username, password=password, **kwargs)


@receiver(user_login_failed)
def journaliser_echec(sender, credentials, request=None, **kwargs):
    identifiant = credentials.get("username", "")
    # Un refus pour verrouillage a déjà été journalisé (BLOQUE) : il ne doit pas
    # compter comme un échec de plus, sinon une personne légitime resterait bloquée tant
    # que quelqu'un continue d'essayer.
    if connexions.compte_verrouille(identifiant):
        return
    connexions.enregistrer(identifiant, connexions.Type.ECHEC, request)


@receiver(user_logged_in)
def journaliser_succes(sender, request, user, **kwargs):
    connexions.enregistrer(user.get_username(), connexions.Type.SUCCES, request)
