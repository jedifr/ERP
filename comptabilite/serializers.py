from technique.serializers import FullCleanModelSerializer

from .models import CompteComptable, JournalComptable


class CompteComptableSerializer(FullCleanModelSerializer):
    class Meta:
        model = CompteComptable
        fields = "__all__"
        read_only_fields = ["classe"]


class JournalComptableSerializer(FullCleanModelSerializer):
    class Meta:
        model = JournalComptable
        fields = "__all__"
