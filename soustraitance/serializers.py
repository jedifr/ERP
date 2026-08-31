from technique.serializers import FullCleanModelSerializer

from .models import EnvoiSousTraitance, RetourSousTraitance


class EnvoiSousTraitanceSerializer(FullCleanModelSerializer):
    class Meta:
        model = EnvoiSousTraitance
        fields = "__all__"


class RetourSousTraitanceSerializer(FullCleanModelSerializer):
    class Meta:
        model = RetourSousTraitance
        fields = "__all__"
