from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from comptes.permissions import ModelPermissionsAvecLecture

from .models import (
    AlerteStock,
    Emplacement,
    Inventaire,
    InventaireError,
    InventaireLigne,
    Lot,
    MouvementImmuableError,
    MouvementStock,
    StockInsuffisantError,
    Transfert,
)
from .serializers import (
    AlerteStockSerializer,
    EmplacementSerializer,
    InventaireLigneSerializer,
    InventaireSerializer,
    LotSerializer,
    MouvementStockSerializer,
    TransfertSerializer,
)


class EmplacementViewSet(viewsets.ModelViewSet):
    permission_classes = [ModelPermissionsAvecLecture]
    queryset = Emplacement.objects.all()
    serializer_class = EmplacementSerializer
    search_fields = ["code", "libelle"]


class LotViewSet(viewsets.ModelViewSet):
    permission_classes = [ModelPermissionsAvecLecture]
    queryset = Lot.objects.select_related("article", "emplacement").all()
    serializer_class = LotSerializer
    filterset_fields = ["article", "emplacement", "statut"]


class MouvementStockViewSet(
    mixins.CreateModelMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    """Journal de stock : consultation et saisie seulement. Pas de modification ni de
    suppression — une erreur se corrige par `annuler` (mouvement inverse)."""

    permission_classes = [ModelPermissionsAvecLecture]
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
    permission_classes = [ModelPermissionsAvecLecture]
    queryset = AlerteStock.objects.select_related("article").all()
    serializer_class = AlerteStockSerializer
    filterset_fields = ["article", "statut"]


class TransfertViewSet(
    mixins.CreateModelMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    permission_classes = [ModelPermissionsAvecLecture]
    queryset = Transfert.objects.select_related("lot_source", "emplacement_cible", "lot_cible").all()
    serializer_class = TransfertSerializer
    filterset_fields = ["lot_source", "emplacement_cible"]

    def perform_create(self, serializer):
        try:
            serializer.save(utilisateur=self.request.user)
        except StockInsuffisantError as exc:
            raise ValidationError({"quantite": [str(exc)]}) from exc


class InventaireViewSet(viewsets.ModelViewSet):
    permission_classes = [ModelPermissionsAvecLecture]
    queryset = Inventaire.objects.all()
    serializer_class = InventaireSerializer
    filterset_fields = ["statut"]

    def perform_update(self, serializer):
        if serializer.instance.statut == Inventaire.Statut.VALIDE:
            raise ValidationError("Cet inventaire est validé : il ne se modifie plus.")
        serializer.save()

    def perform_destroy(self, instance):
        if instance.statut == Inventaire.Statut.VALIDE:
            raise ValidationError("Cet inventaire est validé : il ne se supprime pas.")
        instance.delete()

    @action(detail=True, methods=["post"], url_path="valider", permission_classes=[IsAuthenticated])
    def valider_action(self, request, pk=None):
        if not request.user.has_perm("stock.valider_inventaire"):
            raise PermissionDenied("Vous n'avez pas la permission de valider un inventaire.")
        inventaire = self.get_object()
        try:
            inventaire.valider(utilisateur=request.user)
        except (InventaireError, StockInsuffisantError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(InventaireSerializer(inventaire).data)


class InventaireLigneViewSet(viewsets.ModelViewSet):
    permission_classes = [ModelPermissionsAvecLecture]
    queryset = InventaireLigne.objects.select_related("inventaire", "lot").all()
    serializer_class = InventaireLigneSerializer
    filterset_fields = ["inventaire", "lot"]

    def perform_destroy(self, instance):
        if instance.inventaire.statut == Inventaire.Statut.VALIDE:
            raise ValidationError("Cet inventaire est validé : ses lignes ne se suppriment plus.")
        instance.delete()
