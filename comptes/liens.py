"""Liens vers les fiches de l'admin, pour les bandeaux de confirmation (« commande créée… ») :
le document créé s'ouvre d'un clic, sans le rechercher dans la liste."""

from django.urls import reverse
from django.utils.html import format_html


def lien_admin(objet, texte=None):
    meta = objet._meta
    url = reverse(f"admin:{meta.app_label}_{meta.model_name}_change", args=[objet.pk])
    return format_html('<a href="{}" class="underline font-semibold">{}</a>', url, texte or str(objet))
