from rest_framework import viewsets

from .models import CompteComptable, JournalComptable
from .serializers import CompteComptableSerializer, JournalComptableSerializer


class CompteComptableViewSet(viewsets.ModelViewSet):
    queryset = CompteComptable.objects.select_related("compte_parent").all()
    serializer_class = CompteComptableSerializer
    filterset_fields = ["classe", "systeme", "actif", "compte_parent"]
    search_fields = ["code", "libelle"]


class JournalComptableViewSet(viewsets.ModelViewSet):
    queryset = JournalComptable.objects.all()
    serializer_class = JournalComptableSerializer
    filterset_fields = ["nature", "actif"]
    search_fields = ["code", "libelle"]
