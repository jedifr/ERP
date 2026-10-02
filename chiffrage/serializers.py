from rest_framework import serializers

from technique.serializers import FullCleanModelSerializer

from .models import Commande, Devis, DevisLigne, DevisLigneOperation, OperationOF, OrdreFabrication


MESSAGE_DEVIS_VERROUILLE = (
    "Ce devis est validé : il est verrouillé. Repassez-le d'abord en brouillon "
    "(possible tant qu'aucune commande n'en est issue)."
)


class DevisSerializer(FullCleanModelSerializer):
    class Meta:
        model = Devis
        fields = "__all__"

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if self.instance is not None and self.instance.statut == Devis.Statut.VALIDE:
            if attrs.get("statut", self.instance.statut) == Devis.Statut.VALIDE:
                raise serializers.ValidationError(MESSAGE_DEVIS_VERROUILLE)
            if self.instance.commandes.exists():
                raise serializers.ValidationError(
                    "Une commande est déjà issue de ce devis : impossible de le repasser en brouillon."
                )
        return attrs


class DevisLigneSerializer(FullCleanModelSerializer):
    class Meta:
        model = DevisLigne
        fields = "__all__"
        read_only_fields = ["cout_matiere_calcule", "prix_vente_matiere"]

    def validate(self, attrs):
        attrs = super().validate(attrs)
        devis = attrs.get("devis") or (self.instance.devis if self.instance else None)
        if devis is not None and devis.statut == Devis.Statut.VALIDE:
            raise serializers.ValidationError(MESSAGE_DEVIS_VERROUILLE)
        return attrs


class DevisLigneOperationSerializer(FullCleanModelSerializer):
    class Meta:
        model = DevisLigneOperation
        fields = "__all__"
        read_only_fields = ["cout_calcule", "prix_vente"]


class CommandeSerializer(FullCleanModelSerializer):
    class Meta:
        model = Commande
        fields = "__all__"
        read_only_fields = ["statut"]


class OrdreFabricationSerializer(FullCleanModelSerializer):
    class Meta:
        model = OrdreFabrication
        fields = "__all__"
        read_only_fields = ["statut_synchro", "nombre_tentatives", "date_derniere_tentative"]


class OperationOFSerializer(FullCleanModelSerializer):
    class Meta:
        model = OperationOF
        fields = "__all__"
