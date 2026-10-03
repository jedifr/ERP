from django.db import transaction
from django.http import HttpResponse
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from comptes.permissions import ModelPermissionsAvecLecture

from .documents import (
    DocumentError,
    generer_pdf_ar_commande,
    generer_pdf_bon_preparation,
    generer_pdf_devis,
    generer_pdf_ordre_fabrication,
)
from .models import Commande, CommandeError, Devis, DevisLigne, DevisLigneOperation, OperationOF, OrdreFabrication
from .moteur import ChiffrageError, calculer_devis
from .planning_sync import resynchroniser
from .production import creer_ordres_fabrication, lancer_en_production, reviser_devis
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

    @action(detail=True, methods=["post"], url_path="reviser", permission_classes=[IsAuthenticated])
    def reviser_action(self, request, pk=None):
        """Nouvel indice du devis : corps `{"motif": "..."}` (obligatoire). Crée un brouillon
        et marque le devis actuel « remplacé »."""
        if not request.user.has_perm("chiffrage.change_devis"):
            raise PermissionDenied("Vous n'avez pas la permission de modifier un devis.")
        devis = self.get_object()
        try:
            revision = reviser_devis(devis, request.data.get("motif", ""))
        except ChiffrageError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(DevisSerializer(revision).data, status=status.HTTP_201_CREATED)

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

    @action(detail=True, methods=["get"], url_path="ar-pdf")
    def ar_pdf_action(self, request, pk=None):
        """Accusé de réception de commande (PDF)."""
        commande = self.get_object()
        return _reponse_pdf(generer_pdf_ar_commande, commande, f"ar-{commande.pk}")

    @action(detail=True, methods=["get"], url_path="bon-preparation-pdf")
    def bon_preparation_pdf_action(self, request, pk=None):
        """Bon de préparation (PDF, sans prix)."""
        commande = self.get_object()
        return _reponse_pdf(generer_pdf_bon_preparation, commande, f"preparation-{commande.pk}")

    @action(detail=True, methods=["post"], url_path="creer-ordres-fabrication", permission_classes=[IsAuthenticated])
    def creer_ordres_fabrication_action(self, request, pk=None):
        """Crée les ordres de fabrication manquants : un par ligne d'article fabriqué, ou un par
        article avec `{"regrouper": true}`."""
        commande = self.get_object()
        if not request.user.has_perm("chiffrage.add_ordrefabrication"):
            raise PermissionDenied("Vous n'avez pas la permission de créer un ordre de fabrication.")
        regrouper = str(request.data.get("regrouper", "")).lower() in ("1", "true", "oui")
        try:
            ordres = creer_ordres_fabrication(commande, regrouper=regrouper)
        except ChiffrageError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(OrdreFabricationSerializer(ordres, many=True).data, status=status.HTTP_201_CREATED)

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


def _reponse_pdf(generateur, objet, nom):
    try:
        contenu = generateur(objet)
    except DocumentError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    reponse = HttpResponse(contenu, content_type="application/pdf")
    reponse["Content-Disposition"] = f'inline; filename="{nom}.pdf"'
    return reponse


class OrdreFabricationViewSet(viewsets.ModelViewSet):
    permission_classes = [ModelPermissionsAvecLecture]
    queryset = OrdreFabrication.objects.select_related("commande", "article").all()
    serializer_class = OrdreFabricationSerializer
    filterset_fields = ["commande", "article", "statut_synchro"]
    search_fields = ["numero"]

    @action(detail=True, methods=["get"], url_path="pdf")
    def pdf_action(self, request, pk=None):
        """Fiche de fabrication (PDF) : nomenclature et gamme."""
        of = self.get_object()
        return _reponse_pdf(generer_pdf_ordre_fabrication, of, f"of-{of.pk}")

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
