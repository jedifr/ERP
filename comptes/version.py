"""Numéro de version affiché dans l'admin (UNFOLD["ENVIRONMENT"]).

Le NAS ne dispose pas de `.git` (le code y arrive par une archive tar.gz
téléchargée via `update-nas.sh`, sans métadonnées git) : le numéro de
version ne peut donc pas venir d'un hash de commit. Il est à la place lu
depuis le fichier `VERSION` à la racine du dépôt, un simple fichier texte
suivi par git, à incrémenter manuellement à chaque mise à jour notable.
"""

from functools import lru_cache

from django.conf import settings


@lru_cache(maxsize=1)
def lire_version():
    try:
        return (settings.BASE_DIR / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return "dev"


def badge_environnement(request):
    return (f"v{lire_version()}", "info")


def prefixe_titre(request):
    return f"[v{lire_version()}]"
