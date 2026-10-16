"""API du configurateur web : catalogue, prix instantané et envoi du panier (demande à traiter par un commercial).

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

import base64
import binascii

from .services.bibliotheque import liste as liste_bibliotheque
from .services.demandes import enregistrer_demande
from .services.dxf import ErreurDxf, deposer
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
    """POST {matiere, epaisseur, quantite, forme + cotes | pliage | libre | dxf (+ echelle) | bibliotheque} → prix (voir web.services.prix.chiffrer)."""

    def post(self, request):
        d = request.data
        try:
            resultat = chiffrer(d.get("matiere"), d.get("epaisseur"), d.get("quantite"), forme=d.get("forme"), cotes=d.get("cotes"),
                                pliage=d.get("pliage"), libre=d.get("libre"), dxf=d.get("dxf"), echelle=d.get("echelle"),
                                bibliotheque=d.get("bibliotheque"))
        except ErreurPrixWeb as exc:
            return Response({"erreur": str(exc)}, status=400)
        return Response(resultat.en_dict_public())


class BibliothequeWebVue(_VueWeb):
    """GET → formes décoratives (clé, nom, proportion, rapport épaisseur / largeur maxi, vignette) et crédit de la source.
    Le prix se demande ensuite avec {"bibliotheque": {"cle": ..., "largeur": mm}}."""

    def get(self, request):
        return Response(liste_bibliotheque())


class DxfWebVue(_VueWeb):
    """POST {nom, contenu (base64)} → {jeton, nom, contour, largeur_mm, hauteur_mm, avertissements}. Le prix se demande
    ensuite avec {"dxf": jeton}."""

    def post(self, request):
        try:
            contenu = base64.b64decode(str(request.data.get("contenu") or ""), validate=True)
        except (binascii.Error, ValueError):
            return Response({"erreur": "Fichier illisible."}, status=400)
        try:
            fiche = deposer(request.data.get("nom"), contenu)
        except ErreurDxf as exc:
            return Response({"erreur": str(exc)}, status=400)
        return Response(fiche)


class DemandeWebVue(_VueWeb):
    """POST {client: {...}, lignes: [...]} → {numero, total_ht, a_valider}. Prix recalculés par l'ERP."""

    def post(self, request):
        try:
            demande = enregistrer_demande(request.data)
        except ErreurPrixWeb as exc:
            return Response({"erreur": str(exc)}, status=400)
        return Response({"numero": demande.numero, "total_pieces_ht": float(demande.total_pieces_ht), "total_ht": float(demande.total_ht),
                         "a_valider": demande.a_valider}, status=201)
