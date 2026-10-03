from rest_framework import viewsets
from rest_framework.exceptions import ValidationError

from .models import Facture, FactureVerrouilleeError
from .serializers import FactureSerializer


class FactureViewSet(viewsets.ModelViewSet):
    queryset = Facture.objects.select_related("commande").all()
    serializer_class = FactureSerializer
    filterset_fields = ["commande", "mode_creation", "statut_paiement"]
    search_fields = ["numero", "reference_tiime"]

    def perform_destroy(self, instance):
        try:
            instance.delete()
        except FactureVerrouilleeError as exc:
            raise ValidationError(str(exc)) from exc
