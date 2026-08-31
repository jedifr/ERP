from technique.serializers import FullCleanModelSerializer

from .models import AlerteStock, Emplacement, Lot, MouvementStock


class EmplacementSerializer(FullCleanModelSerializer):
    class Meta:
        model = Emplacement
        fields = "__all__"


class LotSerializer(FullCleanModelSerializer):
    class Meta:
        model = Lot
        fields = "__all__"
        read_only_fields = ["quantite"]


class MouvementStockSerializer(FullCleanModelSerializer):
    class Meta:
        model = MouvementStock
        fields = "__all__"


class AlerteStockSerializer(FullCleanModelSerializer):
    class Meta:
        model = AlerteStock
        fields = "__all__"
