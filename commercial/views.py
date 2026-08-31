from rest_framework import viewsets

from .models import Adresse, Tiers
from .serializers import AdresseSerializer, TiersSerializer


class TiersViewSet(viewsets.ModelViewSet):
    queryset = Tiers.objects.all()
    serializer_class = TiersSerializer
    filterset_fields = ["type_tiers"]
    search_fields = ["code", "raison_sociale", "siret"]


class AdresseViewSet(viewsets.ModelViewSet):
    queryset = Adresse.objects.select_related("tiers").all()
    serializer_class = AdresseSerializer
    filterset_fields = ["tiers", "type_adresse", "est_principale"]
