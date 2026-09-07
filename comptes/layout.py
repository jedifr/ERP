"""Mise en page globale de l'admin (UNFOLD["GLOBAL_CALLBACK"]).

Contrairement à DASHBOARD_CALLBACK (page d'accueil uniquement),
GLOBAL_CALLBACK s'exécute sur chaque page admin (chaque appel à
AdminSite.each_context) : c'est le seul point d'accroche pour injecter une
variable de contexte sur la fiche d'un objet (change_view), pas seulement
sur ses listes — Unfold n'expose "list_fullwidth" (ModelAdmin.list_fullwidth)
que pour les pages de liste, faute de `cl` dans le contexte d'une fiche.
"""


def global_callback(request):
    # Désactive le conteneur centré à largeur plafonnée (classe Tailwind
    # "container") sur toutes les pages admin — nos tableaux de lignes
    # (devis, commandes...) sont larges et gagnent à profiter de tout
    # l'écran plutôt que de scroller horizontalement dans un espace réduit.
    return {"is_fullwidth": "1"}
