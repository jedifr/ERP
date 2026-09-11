from rest_framework import viewsets

from .models import Adresse, Contact, ContactTelephone, Tiers
from .serializers import AdresseSerializer, ContactSerializer, ContactTelephoneSerializer, TiersSerializer


class TiersViewSet(viewsets.ModelViewSet):
    queryset = Tiers.objects.all()
    serializer_class = TiersSerializer
    filterset_fields = ["type_tiers"]
    search_fields = ["code", "raison_sociale", "siret"]


class AdresseViewSet(viewsets.ModelViewSet):
    queryset = Adresse.objects.select_related("tiers").all()
    serializer_class = AdresseSerializer
    filterset_fields = ["tiers", "est_livraison", "est_facturation", "est_principale"]


class ContactViewSet(viewsets.ModelViewSet):
    queryset = Contact.objects.select_related("tiers").all()
    serializer_class = ContactSerializer
    filterset_fields = ["tiers"]
    search_fields = ["nom", "prenom", "email"]


class ContactTelephoneViewSet(viewsets.ModelViewSet):
    queryset = ContactTelephone.objects.select_related("contact").all()
    serializer_class = ContactTelephoneSerializer
    filterset_fields = ["contact", "type_telephone"]
