import datetime

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from chiffrage.models import OrdreFabrication
from technique.models import PosteTravail

from .services import PilotageError, marge_reelle_ordre_fabrication, taux_charge_poste


class MargeReelleOrdreFabricationView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, numero):
        try:
            of = OrdreFabrication.objects.get(pk=numero)
        except OrdreFabrication.DoesNotExist:
            return Response({"detail": "Ordre de fabrication introuvable."}, status=status.HTTP_404_NOT_FOUND)

        try:
            resultat = marge_reelle_ordre_fabrication(of)
        except PilotageError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(resultat)


class TauxChargePosteView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, nom_poste):
        try:
            poste = PosteTravail.objects.get(pk=nom_poste)
        except PosteTravail.DoesNotExist:
            return Response({"detail": "Poste de travail introuvable."}, status=status.HTTP_404_NOT_FOUND)

        date_debut_str = request.query_params.get("date_debut")
        date_fin_str = request.query_params.get("date_fin")
        if not date_debut_str or not date_fin_str:
            return Response(
                {"detail": "Paramètres requis : date_debut, date_fin (YYYY-MM-DD)."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            date_debut = datetime.date.fromisoformat(date_debut_str)
            date_fin = datetime.date.fromisoformat(date_fin_str)
        except ValueError:
            return Response({"detail": "Dates invalides (format attendu : YYYY-MM-DD)."}, status=status.HTTP_400_BAD_REQUEST)

        resultat = taux_charge_poste(poste, date_debut, date_fin)
        return Response(resultat)
