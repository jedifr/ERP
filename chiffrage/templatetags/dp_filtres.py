from django import template

register = template.Library()


@register.filter
def mm2_en_m2(valeur):
    """Surface en mm² → m²."""
    try:
        return float(valeur) / 1_000_000
    except (TypeError, ValueError):
        return 0


@register.filter
def mm_en_m(valeur):
    """Longueur en mm → m."""
    try:
        return float(valeur) / 1000
    except (TypeError, ValueError):
        return 0
