from technique.serializers import FullCleanModelSerializer

from .models import Facture


class FactureSerializer(FullCleanModelSerializer):
    class Meta:
        model = Facture
        fields = "__all__"
