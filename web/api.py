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
from .services.prix import ErreurPrixWeb, catalogue, chiffrer, chiffrer_jeu, taux_tva, ttc


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


LOT_MAX = 30  # pièces d'un même envoi (dessin vectorisé en plusieurs pièces)


def _lot(request):
    """Liste {"lot": [...]} ou {"fichiers": [...]} d'un appel groupé ; None pour un appel simple."""
    for cle in ("lot", "fichiers"):
        if cle in request.data:
            liste = request.data.get(cle)
            if not isinstance(liste, list) or not liste or len(liste) > LOT_MAX or not all(isinstance(x, dict) for x in liste):
                raise ErreurPrixWeb(f"Envoi groupé : de 1 à {LOT_MAX} pièces.")
            return liste
    return None


class PrixWebVue(_VueWeb):
    """POST {matiere, epaisseur, quantite, procede?, forme + cotes | pliage | libre | dxf (+ echelle) | bibliotheque} → prix (voir web.services.prix.chiffrer).
    POST {lot: [demande, ...]} → {lot: [prix | {erreur}]} : plusieurs pièces en un appel, chiffrées chacune seule.
    POST {lot: [...], jeu: true} → prix du jeu (web.services.prix.chiffrer_jeu) : les pièces d'un dessin vectorisé, découpées ensemble
    dans la même tôle, avec la part de chacune dans `pieces`."""

    @staticmethod
    def _chiffrer(d):
        return chiffrer(d.get("matiere"), d.get("epaisseur"), d.get("quantite"), forme=d.get("forme"), cotes=d.get("cotes"),
                        pliage=d.get("pliage"), libre=d.get("libre"), dxf=d.get("dxf"), echelle=d.get("echelle"),
                        bibliotheque=d.get("bibliotheque"), procede=d.get("procede")).en_dict_public()

    def post(self, request):
        try:
            lot = _lot(request)
            if lot is None:
                return Response(self._chiffrer(request.data))
            if request.data.get("jeu"):
                return Response(chiffrer_jeu(lot).en_dict_public())
        except ErreurPrixWeb as exc:
            return Response({"erreur": str(exc)}, status=400)
        resultats = []
        for d in lot:
            try:
                resultats.append(self._chiffrer(d))
            except ErreurPrixWeb as exc:
                resultats.append({"erreur": str(exc)})
        return Response({"lot": resultats})


class BibliothequeWebVue(_VueWeb):
    """GET → formes décoratives (clé, nom, proportion, rapport épaisseur / largeur maxi, vignette) et crédit de la source.
    Le prix se demande ensuite avec {"bibliotheque": {"cle": ..., "largeur": mm}}."""

    def get(self, request):
        return Response(liste_bibliotheque())


class DxfWebVue(_VueWeb):
    """POST {nom, contenu (base64)} → {jeton, nom, contour, largeur_mm, hauteur_mm, avertissements}. Le prix se demande
    ensuite avec {"dxf": jeton}. POST {fichiers: [{nom, contenu}, ...]} → {fichiers: [fiche | {erreur}]} : une pièce par fichier."""

    @staticmethod
    def _deposer(d):
        try:
            contenu = base64.b64decode(str(d.get("contenu") or ""), validate=True)
        except (binascii.Error, ValueError):
            raise ErreurDxf("Fichier illisible.")
        return deposer(d.get("nom"), contenu)

    def post(self, request):
        try:
            fichiers = _lot(request)
            if fichiers is None:
                return Response(self._deposer(request.data))
        except (ErreurDxf, ErreurPrixWeb) as exc:
            return Response({"erreur": str(exc)}, status=400)
        resultats = []
        for d in fichiers:
            try:
                resultats.append(self._deposer(d))
            except ErreurDxf as exc:
                resultats.append({"erreur": str(exc)})
        return Response({"fichiers": resultats})


class DemandeWebVue(_VueWeb):
    """POST {client: {...}, lignes: [...]} → {numero, total_ht, a_valider}. Prix recalculés par l'ERP."""

    def post(self, request):
        try:
            demande = enregistrer_demande(request.data)
        except ErreurPrixWeb as exc:
            return Response({"erreur": str(exc)}, status=400)
        return Response({"numero": demande.numero, "total_pieces_ht": float(demande.total_pieces_ht), "total_ht": float(demande.total_ht),
                         "total_ttc": float(ttc(demande.total_ht, taux_tva())), "a_valider": demande.a_valider}, status=201)
