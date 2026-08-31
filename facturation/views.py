from rest_framework import viewsets

from .models import Facture
from .serializers import FactureSerializer


class FactureViewSet(viewsets.ModelViewSet):
    queryset = Facture.objects.select_related("commande").all()
    serializer_class = FactureSerializer
    filterset_fields = ["commande", "mode_creation", "statut_paiement"]
    search_fields = ["numero", "reference_tiime"]
