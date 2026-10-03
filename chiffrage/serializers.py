from rest_framework import serializers

from technique.serializers import FullCleanModelSerializer

from .models import Commande, ComposantOF, Devis, DevisLigne, DevisLigneOperation, OperationOF, OrdreFabrication


MESSAGE_DEVIS_VERROUILLE = (
    "Ce devis est validé : il est verrouillé. Repassez-le d'abord en brouillon "
    "(possible tant qu'aucune commande n'en est issue)."
)


class DevisSerializer(FullCleanModelSerializer):
    indice = serializers.ReadOnlyField()

    class Meta:
        model = Devis
        fields = "__all__"

    def validate(self, attrs):
        # État d'origine lu AVANT super().validate() : celui-ci applique les
        # valeurs soumises sur l'instance (setattr) pour lancer full_clean(),
        # ce qui masquerait le statut réellement enregistré.
        statut_actuel = self.instance.statut if self.instance is not None else None
        commandes_existantes = self.instance.commandes.exists() if self.instance is not None else False
        attrs = super().validate(attrs)
        if attrs.get("issue") in (Devis.Issue.ACCEPTE, Devis.Issue.REMPLACE):
            raise serializers.ValidationError(
                {"issue": "« Accepté » vient de la création de la commande, « Remplacé » de la révision."}
            )
        if statut_actuel == Devis.Statut.VALIDE:
            champs_libres = {"issue", "motif_refus", "date_validite"}
            if attrs.get("statut", statut_actuel) == Devis.Statut.VALIDE and not set(attrs) <= champs_libres:
                raise serializers.ValidationError(MESSAGE_DEVIS_VERROUILLE)
            if commandes_existantes:
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
        # Devis d'origine (avant un éventuel déplacement de la ligne vers un
        # autre devis) et devis visé : aucun des deux ne doit être validé.
        devis_ids = set()
        if self.instance is not None:
            devis_ids.add(self.instance.devis_id)
        attrs = super().validate(attrs)
        if attrs.get("devis") is not None:
            devis_ids.add(attrs["devis"].pk)
        if Devis.objects.filter(pk__in=devis_ids, statut=Devis.Statut.VALIDE).exists():
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


class ComposantOFSerializer(serializers.ModelSerializer):
    class Meta:
        model = ComposantOF
        fields = ["article", "longueur_mm", "largeur_mm", "quantite_par_unite", "quantite_necessaire"]


class OrdreFabricationSerializer(FullCleanModelSerializer):
    composants = ComposantOFSerializer(many=True, read_only=True)

    class Meta:
        model = OrdreFabrication
        fields = "__all__"
        read_only_fields = [
            "lignes_commande",
            "statut_synchro",
            "nombre_tentatives",
            "date_derniere_tentative",
            "derniere_erreur",
            "prochaine_tentative",
            "empreinte_envoyee",
        ]


class OperationOFSerializer(FullCleanModelSerializer):
    class Meta:
        model = OperationOF
        fields = "__all__"
