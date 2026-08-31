from rest_framework import serializers

from technique.serializers import FullCleanModelSerializer

from .models import Adresse, Contact, Tiers


class TiersSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tiers
        fields = "__all__"


class AdresseSerializer(FullCleanModelSerializer):
    class Meta:
        model = Adresse
        fields = "__all__"


class ContactSerializer(serializers.ModelSerializer):
    class Meta:
        model = Contact
        fields = "__all__"
