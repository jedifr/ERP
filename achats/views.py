from rest_framework import status, viewsets
from rest_framework.exceptions import ValidationError as DRFValidationError

from .models import (
    AchatsError,
    ArticleFournisseur,
    CommandeFournisseur,
    FactureFournisseur,
    LigneCommandeFournisseur,
    Reception,
    ReceptionLigne,
    TarifAchatArticle,
)
from .serializers import (
    ArticleFournisseurSerializer,
    CommandeFournisseurSerializer,
    FactureFournisseurSerializer,
    LigneCommandeFournisseurSerializer,
    ReceptionLigneSerializer,
    ReceptionSerializer,
    TarifAchatArticleSerializer,
)


class ArticleFournisseurViewSet(viewsets.ModelViewSet):
    queryset = ArticleFournisseur.objects.select_related("article", "fournisseur").all()
    serializer_class = ArticleFournisseurSerializer
    filterset_fields = ["article", "fournisseur"]


class TarifAchatArticleViewSet(viewsets.ModelViewSet):
    queryset = TarifAchatArticle.objects.select_related("article_fournisseur").all()
    serializer_class = TarifAchatArticleSerializer
    filterset_fields = ["article_fournisseur"]


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


class FactureFournisseurViewSet(viewsets.ModelViewSet):
    queryset = FactureFournisseur.objects.select_related("commande_fournisseur").all()
    serializer_class = FactureFournisseurSerializer
    filterset_fields = ["commande_fournisseur", "statut_paiement"]
    search_fields = ["numero", "reference_fournisseur"]
