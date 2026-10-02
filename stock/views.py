from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import AlerteStock, Emplacement, Lot, MouvementImmuableError, MouvementStock, StockInsuffisantError
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


class MouvementStockViewSet(
    mixins.CreateModelMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    """Journal de stock : consultation et saisie seulement. Pas de modification ni de
    suppression — une erreur se corrige par `annuler` (mouvement inverse)."""

    queryset = MouvementStock.objects.select_related("lot").all()
    serializer_class = MouvementStockSerializer
    filterset_fields = ["lot", "type_mouvement"]

    def perform_create(self, serializer):
        try:
            serializer.save(utilisateur=self.request.user)
        except StockInsuffisantError as exc:
            raise ValidationError({"quantite": [str(exc)]}) from exc

    @action(detail=True, methods=["post"], url_path="annuler", permission_classes=[IsAuthenticated])
    def annuler_action(self, request, pk=None):
        if not request.user.has_perm("stock.annuler_mouvement"):
            raise PermissionDenied("Vous n'avez pas la permission d'annuler un mouvement de stock.")
        mouvement = self.get_object()
        try:
            inverse = mouvement.annuler(utilisateur=request.user, motif=str(request.data.get("motif", "")).strip())
        except (MouvementImmuableError, StockInsuffisantError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(MouvementStockSerializer(inverse).data, status=status.HTTP_201_CREATED)


class AlerteStockViewSet(viewsets.ModelViewSet):
    queryset = AlerteStock.objects.select_related("article").all()
    serializer_class = AlerteStockSerializer
    filterset_fields = ["article", "statut"]
