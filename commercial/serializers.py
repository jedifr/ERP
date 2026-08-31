from rest_framework import serializers

from technique.serializers import FullCleanModelSerializer

from .models import Adresse, Tiers


class TiersSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tiers
        fields = "__all__"


class AdresseSerializer(FullCleanModelSerializer):
    class Meta:
        model = Adresse
        fields = "__all__"
