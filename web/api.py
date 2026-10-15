"""API du configurateur web : lecture du catalogue et prix instantané, rien d'autre.

Elle est faite pour être appelée par le site client (hébergé à part) à travers un tunnel sortant, jamais en ouvrant le NAS :
chaque appel porte l'en-tête « X-Cle-Web » égal à la variable d'environnement ERP_WEB_CLE_API. Sans cette variable,
l'API est fermée. Débit limité (taux « web » de REST_FRAMEWORK)."""

import hmac

from django.conf import settings
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from .services.prix import ErreurPrixWeb, catalogue, chiffrer


class CleWeb(BasePermission):
    def has_permission(self, request, view):
        attendue = getattr(settings, "WEB_CLE_API", "")
        if not attendue:
            raise PermissionDenied("API du configurateur web fermée : définissez ERP_WEB_CLE_API.")
        return hmac.compare_digest(request.headers.get("X-Cle-Web", ""), attendue)


class _VueWeb(APIView):
    authentication_classes = []
    permission_classes = [CleWeb]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "web"


class CatalogueWeb(_VueWeb):
    def get(self, request):
        return Response(catalogue())


class PrixWebVue(_VueWeb):
    """POST {matiere, epaisseur, quantite, forme + cotes | pliage} → prix (voir web.services.prix.chiffrer)."""

    def post(self, request):
        d = request.data
        try:
            resultat = chiffrer(d.get("matiere"), d.get("epaisseur"), d.get("quantite"), forme=d.get("forme"), cotes=d.get("cotes"), pliage=d.get("pliage"))
        except ErreurPrixWeb as exc:
            return Response({"erreur": str(exc)}, status=400)
        return Response(resultat.en_dict())
