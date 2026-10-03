from rest_framework import serializers

from technique.serializers import FullCleanModelSerializer

from .models import CHAMPS_FIGES, Facture, FactureLigne, facture_verrouillee


class FactureSerializer(FullCleanModelSerializer):
    date_echeance = serializers.ReadOnlyField()

    class Meta:
        model = Facture
        fields = "__all__"

    def validate(self, attrs):
        # Verrou évalué AVANT super().validate() : celui-ci applique les valeurs
        # soumises sur l'instance, et facture_verrouillee() relit la base.
        verrouillee = self.instance is not None and facture_verrouillee(self.instance)
        en_base = (
            Facture.objects.filter(pk=self.instance.pk).values(*CHAMPS_FIGES).first() if verrouillee else None
        )
        attrs = super().validate(attrs)
        request = self.context.get("request")
        if attrs.get("anticipee") and request is not None and not request.user.has_perm(
            "facturation.facturer_avant_livraison"
        ):
            raise serializers.ValidationError({"anticipee": "Vous n'avez pas la permission de facturer avant livraison."})
        if verrouillee:
            for champ in CHAMPS_FIGES:
                nom = champ[:-3] if champ.endswith("_id") else champ
                if nom in attrs:
                    valeur = attrs[nom].pk if hasattr(attrs[nom], "pk") else attrs[nom]
                    if valeur != en_base[champ]:
                        raise serializers.ValidationError(
                            {nom: "Facture émise ou comptabilisée : ce champ ne se modifie plus (corrigez par un avoir)."}
                        )
        return attrs


class FactureLigneSerializer(FullCleanModelSerializer):
    montant_ht = serializers.ReadOnlyField()
    montant_ttc = serializers.ReadOnlyField()

    class Meta:
        model = FactureLigne
        fields = "__all__"
