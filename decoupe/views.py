from django.http import HttpResponse
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response

from .models import ImbricationJob, PieceDecoupe
from .serializers import ImbricationJobSerializer, PieceDecoupeSerializer
from .services.apercu_svg import generer_svg_feuille


class PieceDecoupeViewSet(viewsets.ModelViewSet):
    queryset = PieceDecoupe.objects.select_related("article", "matiere", "profil_import").all()
    serializer_class = PieceDecoupeSerializer
    parser_classes = [MultiPartParser, FormParser]
    filterset_fields = ["statut", "format_source", "article", "pas_rotation_deg", "symetrie_autorisee", "a_gravure"]
    search_fields = ["nom"]

    def perform_create(self, serializer):
        piece = serializer.save()
        piece.importer_geometrie()

    def perform_update(self, serializer):
        piece = serializer.save()
        if "fichier_source" in self.request.data:
            piece.importer_geometrie()

    @action(detail=True, methods=["post"], url_path="reimporter")
    def reimporter(self, request, pk=None):
        piece = self.get_object()
        piece.importer_geometrie()
        return Response(self.get_serializer(piece).data)


class ImbricationJobViewSet(viewsets.ModelViewSet):
    queryset = (
        ImbricationJob.objects.select_related("article_matiere")
        .prefetch_related("lignes__piece", "placements__piece")
        .all()
    )
    serializer_class = ImbricationJobSerializer
    filterset_fields = ["article_matiere"]

    @action(detail=True, methods=["post"], url_path="recalculer")
    def recalculer(self, request, pk=None):
        job = self.get_object()
        job.calculer()
        return Response(self.get_serializer(job).data)

    @action(detail=True, methods=["get"], url_path=r"apercu/(?P<numero_feuille>\d+)")
    def apercu(self, request, pk=None, numero_feuille=None):
        job = self.get_object()
        placements = job.placements.filter(numero_feuille=numero_feuille).select_related("piece")
        if not placements.exists():
            return Response(
                {"detail": "Aucune feuille avec ce numéro pour cette imbrication."},
                status=status.HTTP_404_NOT_FOUND,
            )
        svg = generer_svg_feuille(
            job.largeur_feuille_mm,
            job.longueur_feuille_mm,
            [(p.piece, p.x_mm, p.y_mm, p.rotation_deg, p.miroir) for p in placements],
        )
        return HttpResponse(svg, content_type="image/svg+xml")
