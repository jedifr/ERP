"""Mise en page globale de l'admin (UNFOLD["GLOBAL_CALLBACK"]).

Contrairement à DASHBOARD_CALLBACK (page d'accueil uniquement),
GLOBAL_CALLBACK s'exécute sur chaque page admin (chaque appel à
AdminSite.each_context) : c'est le seul point d'accroche pour injecter une
variable de contexte sur la fiche d'un objet (change_view), pas seulement
sur ses listes — Unfold n'expose "list_fullwidth" (ModelAdmin.list_fullwidth)
que pour les pages de liste, faute de `cl` dans le contexte d'une fiche.
"""


from .raccourcis import menu_nouveau


def global_callback(request):
    # Désactive le conteneur centré à largeur plafonnée (classe Tailwind
    # "container") sur toutes les pages admin — nos tableaux de lignes
    # (devis, commandes...) sont larges et gagnent à profiter de tout
    # l'écran plutôt que de scroller horizontalement dans un espace réduit.
    contexte = {"is_fullwidth": "1", "menu_nouveau": menu_nouveau(request)}
    if not request.user.is_authenticated:
        contexte.update(page_connexion())
    return contexte


def page_connexion():
    """Logo et pied de page des pages hors connexion (connexion, déconnexion), tirés de la fiche Société."""
    import zlib

    from django.urls import reverse

    from .models import Societe

    societe = Societe.objects.filter(pk=1).first()
    if societe is None:
        return {}
    image = societe.image_connexion
    return {
        # ?v= : change avec le fichier, pour que le navigateur ne garde pas l'ancien logo en cache.
        "logo_connexion_url": f"{reverse('logo_connexion')}?v={zlib.crc32(image.name.encode())}" if image else None,
        "nom_societe": societe.raison_sociale,
    }
