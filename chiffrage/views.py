from django.db import transaction
from django.http import HttpResponse
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from comptes.permissions import ModelPermissionsAvecLecture

from .documents import DocumentError, generer_pdf_devis
from .models import Commande, CommandeError, Devis, DevisLigne, DevisLigneOperation, OperationOF, OrdreFabrication
from .moteur import ChiffrageError, calculer_devis
from .planning_sync import resynchroniser
from .production import lancer_en_production
from .validation import verifier_validation_devis
from .serializers import (
    CommandeSerializer,
    DevisLigneOperationSerializer,
    DevisLigneSerializer,
    DevisSerializer,
    OperationOFSerializer,
    OrdreFabricationSerializer,
    MESSAGE_DEVIS_VERROUILLE,
)


class DevisViewSet(viewsets.ModelViewSet):
    permission_classes = [ModelPermissionsAvecLecture]
    queryset = Devis.objects.select_related("client").all()
    serializer_class = DevisSerializer
    filterset_fields = ["statut", "client"]
    search_fields = ["numero"]

    def perform_create(self, serializer):
        with transaction.atomic():
            self._controler_validation(serializer.save(), statut_avant=None)

    def perform_update(self, serializer):
        # Relu en base : la validation du serializer a déjà appliqué les valeurs
        # soumises sur l'instance, qui ne porte donc plus l'ancien statut.
        statut_avant = Devis.objects.values_list("statut", flat=True).get(pk=serializer.instance.pk)
        with transaction.atomic():
            self._controler_validation(serializer.save(), statut_avant)

    def _controler_validation(self, devis, statut_avant):
        """Passage à « validé » : permission requise, puis mêmes contrôles que
        l'admin. Levée dans la transaction : tout est annulé en cas de refus."""
        if devis.statut != Devis.Statut.VALIDE or statut_avant == Devis.Statut.VALIDE:
            return
        user = self.request.user
        if not user.has_perm("chiffrage.valider_devis"):
            raise PermissionDenied("Vous n'avez pas la permission de valider un devis.")
        raisons = verifier_validation_devis(
            devis, peut_vendre_sous_cout=user.has_perm("chiffrage.valider_vente_sous_cout")
        )
        if raisons:
            raise ValidationError({"statut": raisons})

    @action(detail=True, methods=["get"], url_path="pdf")
    def pdf_action(self, request, pk=None):
        """Offre au format PDF (la lecture exige la permission « voir » du devis)."""
        devis = self.get_object()
        try:
            contenu = generer_pdf_devis(devis)
        except DocumentError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        reponse = HttpResponse(contenu, content_type="application/pdf")
        reponse["Content-Disposition"] = f'inline; filename="devis-{devis.pk}.pdf"'
        return reponse

    @action(detail=True, methods=["post"], url_path="recalculer", permission_classes=[IsAuthenticated])
    def recalculer(self, request, pk=None):
        devis = self.get_object()
        if not request.user.has_perm("chiffrage.change_devis"):
            raise PermissionDenied("Vous n'avez pas la permission de modifier un devis.")
        if devis.statut == Devis.Statut.VALIDE:
            return Response({"detail": MESSAGE_DEVIS_VERROUILLE}, status=status.HTTP_400_BAD_REQUEST)
        try:
            calculer_devis(devis)
        except ChiffrageError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(DevisSerializer(devis).data)

    @action(detail=True, methods=["post"], url_path="lancer-en-production", permission_classes=[IsAuthenticated])
    def lancer_en_production_action(self, request, pk=None):
        devis = self.get_object()
        if not request.user.has_perm("chiffrage.add_commande"):
            raise PermissionDenied("Vous n'avez pas la permission de créer une commande.")
        try:
            commande = lancer_en_production(devis)
        except ChiffrageError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(CommandeSerializer(commande).data, status=status.HTTP_201_CREATED)


class DevisLigneViewSet(viewsets.ModelViewSet):
    permission_classes = [ModelPermissionsAvecLecture]
    queryset = DevisLigne.objects.select_related("devis", "article").all()
    serializer_class = DevisLigneSerializer
    filterset_fields = ["devis", "article"]

    def perform_destroy(self, instance):
        if instance.devis.statut == Devis.Statut.VALIDE:
            raise ValidationError(MESSAGE_DEVIS_VERROUILLE)
        super().perform_destroy(instance)


class DevisLigneOperationViewSet(viewsets.ModelViewSet):
    permission_classes = [ModelPermissionsAvecLecture]
    queryset = DevisLigneOperation.objects.select_related("devis_ligne", "poste").all()
    serializer_class = DevisLigneOperationSerializer
    filterset_fields = ["devis_ligne", "poste"]


class CommandeViewSet(viewsets.ModelViewSet):
    permission_classes = [ModelPermissionsAvecLecture]
    queryset = Commande.objects.select_related("devis", "adresse_facturation", "adresse_livraison").all()
    serializer_class = CommandeSerializer
    filterset_fields = ["devis", "statut"]
    search_fields = ["numero"]

    @action(detail=True, methods=["post"], url_path="annuler", permission_classes=[IsAuthenticated])
    def annuler_action(self, request, pk=None):
        commande = self.get_object()
        if not request.user.has_perm("chiffrage.annuler_commande"):
            raise PermissionDenied("Vous n'avez pas la permission d'annuler une commande.")
        try:
            commande.annuler()
        except CommandeError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(CommandeSerializer(commande).data)


class OrdreFabricationViewSet(viewsets.ModelViewSet):
    permission_classes = [ModelPermissionsAvecLecture]
    queryset = OrdreFabrication.objects.select_related("commande", "article").all()
    serializer_class = OrdreFabricationSerializer
    filterset_fields = ["commande", "article", "statut_synchro"]
    search_fields = ["numero"]

    @action(detail=True, methods=["post"], url_path="resynchroniser", permission_classes=[IsAuthenticated])
    def resynchroniser_action(self, request, pk=None):
        of = self.get_object()
        if not request.user.has_perm("chiffrage.change_ordrefabrication"):
            raise PermissionDenied("Vous n'avez pas la permission de modifier un ordre de fabrication.")
        reussite = resynchroniser(of)
        serializer = OrdreFabricationSerializer(of)
        return Response(
            serializer.data, status=status.HTTP_200_OK if reussite else status.HTTP_502_BAD_GATEWAY
        )


class OperationOFViewSet(viewsets.ModelViewSet):
    permission_classes = [ModelPermissionsAvecLecture]
    queryset = OperationOF.objects.select_related("ordre_fabrication", "poste").all()
    serializer_class = OperationOFSerializer
    filterset_fields = ["ordre_fabrication", "poste"]
