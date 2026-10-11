from rest_framework import serializers

from technique.serializers import FullCleanModelSerializer

from .models import AlerteStock, Emplacement, Inventaire, InventaireLigne, Lot, MouvementStock, Transfert


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
        read_only_fields = ["utilisateur", "date_creation", "annule_mouvement"]


class AlerteStockSerializer(FullCleanModelSerializer):
    class Meta:
        model = AlerteStock
        fields = "__all__"


class TransfertSerializer(FullCleanModelSerializer):
    class Meta:
        model = Transfert
        fields = "__all__"
        read_only_fields = ["lot_cible", "utilisateur", "date_creation"]


class InventaireSerializer(FullCleanModelSerializer):
    class Meta:
        model = Inventaire
        fields = "__all__"
        read_only_fields = ["statut", "utilisateur_validation", "date_validation"]


class InventaireLigneSerializer(FullCleanModelSerializer):
    class Meta:
        model = InventaireLigne
        fields = "__all__"
        read_only_fields = ["quantite_theorique", "ecart"]

    def validate(self, attrs):
        inventaire = attrs.get("inventaire") or (self.instance.inventaire if self.instance else None)
        if inventaire is not None and inventaire.statut == Inventaire.Statut.VALIDE:
            raise serializers.ValidationError("Cet inventaire est validé : ses lignes ne se modifient plus.")
        return super().validate(attrs)
