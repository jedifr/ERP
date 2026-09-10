from technique.serializers import FullCleanModelSerializer

from .models import (
    ArticleCompteAchat,
    ArticleCompteVente,
    CodeAnalytique,
    CompteComptable,
    EcritureComptable,
    JournalComptable,
    LigneEcriture,
    ParametresComptables,
)


class CompteComptableSerializer(FullCleanModelSerializer):
    class Meta:
        model = CompteComptable
        fields = "__all__"
        read_only_fields = ["classe"]


class CodeAnalytiqueSerializer(FullCleanModelSerializer):
    class Meta:
        model = CodeAnalytique
        fields = "__all__"


class ArticleCompteVenteSerializer(FullCleanModelSerializer):
    class Meta:
        model = ArticleCompteVente
        fields = "__all__"


class ArticleCompteAchatSerializer(FullCleanModelSerializer):
    class Meta:
        model = ArticleCompteAchat
        fields = "__all__"


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
