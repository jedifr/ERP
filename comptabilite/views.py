from rest_framework import viewsets

from .models import CompteComptable, EcritureComptable, JournalComptable, LigneEcriture, ParametresComptables
from .serializers import (
    CompteComptableSerializer,
    EcritureComptableSerializer,
    JournalComptableSerializer,
    LigneEcritureSerializer,
    ParametresComptablesSerializer,
)


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


class ParametresComptablesViewSet(viewsets.ModelViewSet):
    queryset = ParametresComptables.objects.all()
    serializer_class = ParametresComptablesSerializer


class EcritureComptableViewSet(viewsets.ModelViewSet):
    queryset = EcritureComptable.objects.select_related("journal", "facture").all()
    serializer_class = EcritureComptableSerializer
    filterset_fields = ["journal", "facture"]
    search_fields = ["piece", "libelle"]


class LigneEcritureViewSet(viewsets.ModelViewSet):
    queryset = LigneEcriture.objects.select_related("ecriture", "compte").all()
    serializer_class = LigneEcritureSerializer
    filterset_fields = ["ecriture", "compte"]
