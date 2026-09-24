from technique.serializers import FullCleanModelSerializer

from .models import Commande, Devis, DevisLigne, DevisLigneOperation, OperationOF, OrdreFabrication


class DevisSerializer(FullCleanModelSerializer):
    class Meta:
        model = Devis
        fields = "__all__"


class DevisLigneSerializer(FullCleanModelSerializer):
    class Meta:
        model = DevisLigne
        fields = "__all__"
        read_only_fields = ["cout_matiere_calcule", "prix_vente_matiere"]


class DevisLigneOperationSerializer(FullCleanModelSerializer):
    class Meta:
        model = DevisLigneOperation
        fields = "__all__"
        read_only_fields = ["cout_calcule", "prix_vente"]


class CommandeSerializer(FullCleanModelSerializer):
    class Meta:
        model = Commande
        fields = "__all__"


class OrdreFabricationSerializer(FullCleanModelSerializer):
    class Meta:
        model = OrdreFabrication
        fields = "__all__"
        read_only_fields = ["statut_synchro", "nombre_tentatives", "date_derniere_tentative"]


class OperationOFSerializer(FullCleanModelSerializer):
    class Meta:
        model = OperationOF
        fields = "__all__"
