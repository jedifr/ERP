"""Champ décimal du projet.

- Accepte aussi les flottants (convertis par leur écriture courte) : le `DecimalField` de Django
  convertit 0.1 avec la précision du champ (0.1000000 pour 7 chiffres), ce que son propre
  validateur refuse ensuite. Ici 0.1 reste 0.1.
- Dans les formulaires, l'affichage ne garde pas les zéros inutiles (un prix à 6 décimales
  s'affiche « 10 » ou « 0.12345 », pas « 10.000000 »)."""

from decimal import Decimal

from django import forms
from django.db import models


class ChampFormDecimal(forms.DecimalField):
    def prepare_value(self, value):
        if isinstance(value, Decimal):
            return format(value.normalize(), "f")
        return value


class ChampDecimal(models.DecimalField):
    def to_python(self, value):
        if isinstance(value, float):
            value = Decimal(repr(value))
        return super().to_python(value)

    def formfield(self, **kwargs):
        kwargs.setdefault("form_class", ChampFormDecimal)
        return super().formfield(**kwargs)
