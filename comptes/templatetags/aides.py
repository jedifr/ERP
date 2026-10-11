from django import template

from comptes.aides import aide_pour

register = template.Library()


@register.inclusion_tag("admin/_aide_liste.html")
def aide_liste(opts):
    """Encadré « Mode d'emploi » en haut d'une liste de paramétrage."""
    return {"aide": aide_pour(opts)}
