from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from technique.serializers import FullCleanModelSerializer

from .models import ImbricationJob, ImbricationLigne, ImbricationPlacement, PieceDecoupe


class _BooleanFieldAvecDefautMultipart(serializers.BooleanField):
    """DRF traite un booléen absent d'un envoi multipart comme False (sémantique des cases à
    cocher HTML), même si `default=` est fourni : `default_empty_html` court-circuite le
    `default` normal. Comme l'upload d'une pièce se fait en multipart, on force ici le
    comportement à retomber sur le `default` explicite (True) plutôt que sur False, pour que
    l'absence du champ à l'upload corresponde bien au défaut métier du modèle."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if "default" in kwargs:
            self.default_empty_html = kwargs["default"]


class PieceDecoupeSerializer(FullCleanModelSerializer):
    symetrie_autorisee = _BooleanFieldAvecDefautMultipart(default=True, required=False)

    class Meta:
        model = PieceDecoupe
        fields = "__all__"
        read_only_fields = [
            "format_source",
            "statut",
            "message_erreur",
            "avertissements",
            "surface_mm2",
            "perimetre_decoupe_mm",
            "largeur_mm",
            "hauteur_mm",
            "nb_contours_interieurs",
            "contour_json",
            "calques_detectes",
            "a_gravure",
            "gravure_json",
            "pliage_json",
            "longueur_gravure_mm",
            "date_import",
        ]


class ImbricationLigneSerializer(serializers.ModelSerializer):
    class Meta:
        model = ImbricationLigne
        fields = ["id", "piece", "quantite"]


class ImbricationPlacementSerializer(serializers.ModelSerializer):
    class Meta:
        model = ImbricationPlacement
        fields = ["id", "piece", "numero_feuille", "x_mm", "y_mm", "rotation_deg", "miroir"]


class ImbricationJobSerializer(serializers.ModelSerializer):
    """Sérialiseur avec écriture imbriquée : les lignes (pièces + quantités) sont créées avec
    le job, qui déclenche immédiatement le calcul d'imbrication."""

    lignes = ImbricationLigneSerializer(many=True)
    placements = ImbricationPlacementSerializer(many=True, read_only=True)

    class Meta:
        model = ImbricationJob
        fields = "__all__"
        read_only_fields = [
            "nb_feuilles",
            "surface_pieces_mm2",
            "surface_feuilles_mm2",
            "taux_utilisation_pct",
            "cout_matiere_estime",
            "pieces_non_placees",
            "date_calcul",
        ]

    def validate(self, attrs):
        lignes = attrs.get("lignes")
        if not lignes:
            raise serializers.ValidationError({"lignes": "Au moins une pièce est requise."})

        job_attrs = {champ: valeur for champ, valeur in attrs.items() if champ != "lignes"}
        instance = self.Meta.model(**job_attrs)
        try:
            instance.full_clean(exclude=["lignes", "placements"])
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.message_dict if hasattr(exc, "message_dict") else exc.messages)
        return attrs

    def create(self, validated_data):
        lignes_data = validated_data.pop("lignes")
        job = ImbricationJob.objects.create(**validated_data)
        for ligne in lignes_data:
            ImbricationLigne.objects.create(job=job, **ligne)
        job.calculer()
        return job

    def update(self, instance, validated_data):
        lignes_data = validated_data.pop("lignes", None)
        for champ, valeur in validated_data.items():
            setattr(instance, champ, valeur)
        instance.save()
        if lignes_data is not None:
            instance.lignes.all().delete()
            for ligne in lignes_data:
                ImbricationLigne.objects.create(job=instance, **ligne)
        instance.calculer()
        return instance
