from rest_framework import serializers

from technique.serializers import FullCleanModelSerializer

from .models import Facture


class FactureSerializer(FullCleanModelSerializer):
    date_echeance = serializers.ReadOnlyField()

    class Meta:
        model = Facture
        fields = "__all__"
