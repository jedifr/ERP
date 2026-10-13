from django import template
from django.utils.html import json_script

register = template.Library()


@register.simple_tag(takes_context=True)
def config_tableaux_page(context):
    """Réglages des tableaux de la page (colonnes masquées, largeurs) pour comptes/static/comptes/tableaux.js."""
    from comptes.colonnes import config_tableaux  # import tardif : comptes.colonnes importe unfold.admin

    config = config_tableaux(context)
    return json_script(config, "tableaux-config") if config else ""
