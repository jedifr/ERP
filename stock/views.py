from rest_framework import viewsets

from .models import AlerteStock, Emplacement, Lot, MouvementStock
from .serializers import (
    AlerteStockSerializer,
    EmplacementSerializer,
    LotSerializer,
    MouvementStockSerializer,
)


class EmplacementViewSet(viewsets.ModelViewSet):
    queryset = Emplacement.objects.all()
    serializer_class = EmplacementSerializer
    search_fields = ["code", "libelle"]


class LotViewSet(viewsets.ModelViewSet):
    queryset = Lot.objects.select_related("article", "emplacement").all()
    serializer_class = LotSerializer
    filterset_fields = ["article", "emplacement", "statut"]


class MouvementStockViewSet(viewsets.ModelViewSet):
    queryset = MouvementStock.objects.select_related("lot").all()
    serializer_class = MouvementStockSerializer
    filterset_fields = ["lot", "type_mouvement"]


class AlerteStockViewSet(viewsets.ModelViewSet):
    queryset = AlerteStock.objects.select_related("article").all()
    serializer_class = AlerteStockSerializer
    filterset_fields = ["article", "statut"]
