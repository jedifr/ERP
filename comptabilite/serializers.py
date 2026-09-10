from technique.serializers import FullCleanModelSerializer

from .models import CompteComptable, EcritureComptable, JournalComptable, LigneEcriture, ParametresComptables


class CompteComptableSerializer(FullCleanModelSerializer):
    class Meta:
        model = CompteComptable
        fields = "__all__"
        read_only_fields = ["classe"]


class JournalComptableSerializer(FullCleanModelSerializer):
    class Meta:
        model = JournalComptable
        fields = "__all__"


class ParametresComptablesSerializer(FullCleanModelSerializer):
    class Meta:
        model = ParametresComptables
        fields = "__all__"


class LigneEcritureSerializer(FullCleanModelSerializer):
    class Meta:
        model = LigneEcriture
        fields = "__all__"


class EcritureComptableSerializer(FullCleanModelSerializer):
    class Meta:
        model = EcritureComptable
        fields = "__all__"
