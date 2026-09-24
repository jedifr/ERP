from rest_framework import viewsets

from .models import EnvoiSousTraitance, RetourSousTraitance
from .serializers import EnvoiSousTraitanceSerializer, RetourSousTraitanceSerializer


class EnvoiSousTraitanceViewSet(viewsets.ModelViewSet):
    queryset = EnvoiSousTraitance.objects.select_related("operation_of", "sous_traitant").all()
    serializer_class = EnvoiSousTraitanceSerializer
    filterset_fields = ["sous_traitant", "statut"]
    search_fields = ["numero"]


class RetourSousTraitanceViewSet(viewsets.ModelViewSet):
    queryset = RetourSousTraitance.objects.select_related("envoi").all()
    serializer_class = RetourSousTraitanceSerializer
    filterset_fields = ["envoi", "conforme"]
    search_fields = ["numero"]
