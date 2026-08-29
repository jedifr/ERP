from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from .models import Article, Gamme, Matiere, Nomenclature, PosteTravail, TarifPoste


class FullCleanModelSerializer(serializers.ModelSerializer):
    """Fait rejouer les validations métier définies dans `Model.clean()` côté API,
    pour ne pas dupliquer les règles déjà écrites sur les modèles."""

    def validate(self, attrs):
        instance = self.instance
        if instance is not None:
            for field, value in attrs.items():
                setattr(instance, field, value)
        else:
            instance = self.Meta.model(**attrs)

        try:
            instance.full_clean(exclude=self._fields_excluded_from_clean())
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.message_dict if hasattr(exc, "message_dict") else exc.messages)
        return attrs

    def _fields_excluded_from_clean(self):
        # Les champs non exposés par le serializer ne peuvent pas être validés par full_clean.
        model_fields = {f.name for f in self.Meta.model._meta.get_fields()}
        return list(model_fields - set(self.fields.keys()))


class MatiereSerializer(serializers.ModelSerializer):
    class Meta:
        model = Matiere
        fields = "__all__"


class ArticleSerializer(FullCleanModelSerializer):
    class Meta:
        model = Article
        fields = "__all__"


class PosteTravailSerializer(serializers.ModelSerializer):
    class Meta:
        model = PosteTravail
        fields = "__all__"


class TarifPosteSerializer(FullCleanModelSerializer):
    class Meta:
        model = TarifPoste
        fields = "__all__"


class NomenclatureSerializer(FullCleanModelSerializer):
    class Meta:
        model = Nomenclature
        fields = "__all__"


class GammeSerializer(FullCleanModelSerializer):
    class Meta:
        model = Gamme
        fields = "__all__"
