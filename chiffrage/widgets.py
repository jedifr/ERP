from django import forms
from django.utils.html import format_html, format_html_join

from commercial.models import DelaiPropose


class DelaiWidget(forms.TextInput):
    """Champ texte libre pour Devis.delai, avec des suggestions venant du
    référentiel DelaiPropose affichées via un <datalist> HTML natif :
    l'utilisateur peut choisir une suggestion dans la liste déroulante ou
    taper n'importe quel autre texte — le <datalist> ne contraint jamais la
    valeur saisie, contrairement à un <select>."""

    def render(self, name, value, attrs=None, renderer=None):
        attrs = {**(attrs or {}), "list": "delai-suggestions"}
        champ_html = super().render(name, value, attrs, renderer)
        options = format_html_join(
            "", "<option value='{}'>", ((d.libelle,) for d in DelaiPropose.objects.all())
        )
        return format_html('{}<datalist id="delai-suggestions">{}</datalist>', champ_html, options)
