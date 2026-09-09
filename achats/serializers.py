from technique.serializers import FullCleanModelSerializer

from .models import (
    ArticleFournisseur,
    CommandeFournisseur,
    LigneCommandeFournisseur,
    Reception,
    ReceptionLigne,
    TarifAchatArticle,
)


class ArticleFournisseurSerializer(FullCleanModelSerializer):
    class Meta:
        model = ArticleFournisseur
        fields = "__all__"


class TarifAchatArticleSerializer(FullCleanModelSerializer):
    class Meta:
        model = TarifAchatArticle
        fields = "__all__"


class CommandeFournisseurSerializer(FullCleanModelSerializer):
    class Meta:
        model = CommandeFournisseur
        fields = "__all__"


class LigneCommandeFournisseurSerializer(FullCleanModelSerializer):
    class Meta:
        model = LigneCommandeFournisseur
        fields = "__all__"
        read_only_fields = ["quantite_recue"]


class ReceptionSerializer(FullCleanModelSerializer):
    class Meta:
        model = Reception
        fields = "__all__"


class ReceptionLigneSerializer(FullCleanModelSerializer):
    class Meta:
        model = ReceptionLigne
        fields = "__all__"
