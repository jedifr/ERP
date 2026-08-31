from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import Commande, Devis, DevisLigne, DevisLigneOperation, OperationOF, OrdreFabrication
from .moteur import ChiffrageError, calculer_devis
from .planning_sync import resynchroniser
from .production import lancer_en_production
from .serializers import (
    CommandeSerializer,
    DevisLigneOperationSerializer,
    DevisLigneSerializer,
    DevisSerializer,
    OperationOFSerializer,
    OrdreFabricationSerializer,
)


class DevisViewSet(viewsets.ModelViewSet):
    queryset = Devis.objects.select_related("client").all()
    serializer_class = DevisSerializer
    filterset_fields = ["statut", "client"]
    search_fields = ["numero"]

    @action(detail=True, methods=["post"], url_path="recalculer")
    def recalculer(self, request, pk=None):
        devis = self.get_object()
        try:
            calculer_devis(devis)
        except ChiffrageError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(DevisSerializer(devis).data)

    @action(detail=True, methods=["post"], url_path="lancer-en-production")
    def lancer_en_production_action(self, request, pk=None):
        devis = self.get_object()
        try:
            commande = lancer_en_production(devis)
        except ChiffrageError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(CommandeSerializer(commande).data, status=status.HTTP_201_CREATED)


class DevisLigneViewSet(viewsets.ModelViewSet):
    queryset = DevisLigne.objects.select_related("devis", "article").all()
    serializer_class = DevisLigneSerializer
    filterset_fields = ["devis", "article"]


class DevisLigneOperationViewSet(viewsets.ModelViewSet):
    queryset = DevisLigneOperation.objects.select_related("devis_ligne", "poste").all()
    serializer_class = DevisLigneOperationSerializer
    filterset_fields = ["devis_ligne", "poste"]


class CommandeViewSet(viewsets.ModelViewSet):
    queryset = Commande.objects.select_related("devis", "adresse_facturation", "adresse_livraison").all()
    serializer_class = CommandeSerializer
    filterset_fields = ["devis"]
    search_fields = ["numero"]


class OrdreFabricationViewSet(viewsets.ModelViewSet):
    queryset = OrdreFabrication.objects.select_related("commande", "article").all()
    serializer_class = OrdreFabricationSerializer
    filterset_fields = ["commande", "article", "statut_synchro"]
    search_fields = ["numero"]

    @action(detail=True, methods=["post"], url_path="resynchroniser")
    def resynchroniser_action(self, request, pk=None):
        of = self.get_object()
        reussite = resynchroniser(of)
        serializer = OrdreFabricationSerializer(of)
        return Response(
            serializer.data, status=status.HTTP_200_OK if reussite else status.HTTP_502_BAD_GATEWAY
        )


class OperationOFViewSet(viewsets.ModelViewSet):
    queryset = OperationOF.objects.select_related("ordre_fabrication", "poste").all()
    serializer_class = OperationOFSerializer
    filterset_fields = ["ordre_fabrication", "poste"]
