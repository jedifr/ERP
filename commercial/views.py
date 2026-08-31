from rest_framework import viewsets

from .models import Adresse, Contact, Tiers
from .serializers import AdresseSerializer, ContactSerializer, TiersSerializer


class TiersViewSet(viewsets.ModelViewSet):
    queryset = Tiers.objects.all()
    serializer_class = TiersSerializer
    filterset_fields = ["type_tiers"]
    search_fields = ["code", "raison_sociale", "siret"]


class AdresseViewSet(viewsets.ModelViewSet):
    queryset = Adresse.objects.select_related("tiers").all()
    serializer_class = AdresseSerializer
    filterset_fields = ["tiers", "type_adresse", "est_principale"]


class ContactViewSet(viewsets.ModelViewSet):
    queryset = Contact.objects.select_related("tiers").all()
    serializer_class = ContactSerializer
    filterset_fields = ["tiers"]
    search_fields = ["nom", "prenom", "email"]
