from rest_framework import status, viewsets
from rest_framework.exceptions import ValidationError as DRFValidationError

from .models import AchatsError, CommandeFournisseur, LigneCommandeFournisseur, Reception, ReceptionLigne
from .serializers import (
    CommandeFournisseurSerializer,
    LigneCommandeFournisseurSerializer,
    ReceptionLigneSerializer,
    ReceptionSerializer,
)


class CommandeFournisseurViewSet(viewsets.ModelViewSet):
    queryset = CommandeFournisseur.objects.select_related("fournisseur").all()
    serializer_class = CommandeFournisseurSerializer
    filterset_fields = ["fournisseur", "statut"]
    search_fields = ["numero"]


class LigneCommandeFournisseurViewSet(viewsets.ModelViewSet):
    queryset = LigneCommandeFournisseur.objects.select_related("commande_fournisseur", "article").all()
    serializer_class = LigneCommandeFournisseurSerializer
    filterset_fields = ["commande_fournisseur", "article"]


class ReceptionViewSet(viewsets.ModelViewSet):
    queryset = Reception.objects.select_related("commande_fournisseur").all()
    serializer_class = ReceptionSerializer
    filterset_fields = ["commande_fournisseur"]
    search_fields = ["numero"]


class ReceptionLigneViewSet(viewsets.ModelViewSet):
    queryset = ReceptionLigne.objects.select_related("reception", "ligne_commande_fournisseur").all()
    serializer_class = ReceptionLigneSerializer
    filterset_fields = ["reception", "ligne_commande_fournisseur"]

    def perform_create(self, serializer):
        try:
            serializer.save()
        except AchatsError as exc:
            raise DRFValidationError({"detail": str(exc)}, code=status.HTTP_400_BAD_REQUEST)
