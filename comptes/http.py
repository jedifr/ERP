"""JsonResponse qui sérialise les `Decimal` en nombres JSON (et non en chaînes).

Les écrans du constructeur de devis et de commande (JavaScript) font de l'arithmétique et
du formatage sur ces valeurs : ils attendent des nombres."""

from decimal import Decimal

from django.core.serializers.json import DjangoJSONEncoder
from django.http import JsonResponse as _JsonResponse


class EncodeurJsonNombres(DjangoJSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal):
            return float(o)
        return super().default(o)


class JsonResponse(_JsonResponse):
    def __init__(self, data, encoder=EncodeurJsonNombres, **kwargs):
        super().__init__(data, encoder=encoder, **kwargs)
