from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from chiffrage.models import Commande
from comptes.permissions import ModelPermissionsAvecLecture

from .models import Facture, FactureLigne, FactureVerrouilleeError
from .serializers import FactureLigneSerializer, FactureSerializer
from .services import FacturationError, creer_avoir, preparer_facture


class FactureViewSet(viewsets.ModelViewSet):
    permission_classes = [ModelPermissionsAvecLecture]
    queryset = Facture.objects.select_related("commande").all()
    serializer_class = FactureSerializer
    filterset_fields = ["commande", "mode_creation", "statut_paiement", "type_document"]
    search_fields = ["numero", "reference_tiime"]

    def perform_destroy(self, instance):
        try:
            instance.delete()
        except FactureVerrouilleeError as exc:
            raise ValidationError(str(exc)) from exc

    @action(detail=False, methods=["post"], url_path="preparer", permission_classes=[IsAuthenticated])
    def preparer_action(self, request):
        """Facture brouillon du livré non encore facturé d'une commande."""
        if not request.user.has_perm("facturation.add_facture"):
            raise PermissionDenied("Vous n'avez pas la permission de créer une facture.")
        anticipee = bool(request.data.get("anticipee"))
        if anticipee and not request.user.has_perm("facturation.facturer_avant_livraison"):
            raise PermissionDenied("Vous n'avez pas la permission de facturer avant livraison.")
        try:
            commande = Commande.objects.get(pk=request.data.get("commande"))
            facture = preparer_facture(commande, anticipee=anticipee)
        except Commande.DoesNotExist:
            return Response({"detail": "Commande introuvable."}, status=status.HTTP_404_NOT_FOUND)
        except FacturationError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(FactureSerializer(facture).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="avoir", permission_classes=[IsAuthenticated])
    def avoir_action(self, request, pk=None):
        """Avoir sur le solde restant à créditer d'une facture émise."""
        if not request.user.has_perm("facturation.creer_avoir"):
            raise PermissionDenied("Vous n'avez pas la permission de créer un avoir.")
        facture = self.get_object()
        try:
            avoir = creer_avoir(facture, str(request.data.get("motif", "")), utilisateur=request.user)
        except FacturationError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(FactureSerializer(avoir).data, status=status.HTTP_201_CREATED)


class FactureLigneViewSet(viewsets.ModelViewSet):
    permission_classes = [ModelPermissionsAvecLecture]
    queryset = FactureLigne.objects.select_related("facture", "commande_ligne").all()
    serializer_class = FactureLigneSerializer
    filterset_fields = ["facture", "commande_ligne"]

    def perform_destroy(self, instance):
        try:
            instance.delete()
        except FactureVerrouilleeError as exc:
            raise ValidationError(str(exc)) from exc
