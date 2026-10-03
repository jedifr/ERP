"""Diagnostic de sécurité de la configuration : une liste de constats que l'on affiche aux
administrateurs (bandeau du tableau de bord) et en ligne de commande
(`manage.py verifier_securite`, lancé au démarrage du conteneur). Rien ici ne bloque le
démarrage : un réglage douteux est signalé, jamais cause de panne (un ERP qui ne démarre pas
est pire qu'un avertissement)."""

from dataclasses import dataclass

from django.conf import settings

CRITIQUE = "critique"
AVERTISSEMENT = "avertissement"

CLES_FAIBLES = {"django-insecure-change-me-in-production", "change-me-in-production", "changeme", "secret"}
MOTS_DE_PASSE_FAIBLES = {"erp_dev_password", "password", "postgres", "changeme"}


@dataclass(frozen=True)
class Constat:
    niveau: str
    titre: str
    conseil: str


def cle_secrete_faible(cle):
    texte = (cle or "").lower()
    return texte in CLES_FAIBLES or "change-me" in texte or "insecure" in texte or len(texte) < 32


def diagnostics():
    constats = []
    if settings.DEBUG:
        constats.append(
            Constat(
                CRITIQUE,
                "Mode DEBUG activé",
                "Les pages d'erreur exposent le code, la configuration et les variables. "
                "Mettre DJANGO_DEBUG=False dans le fichier .env.",
            )
        )
    if cle_secrete_faible(settings.SECRET_KEY):
        constats.append(
            Constat(
                CRITIQUE,
                "Clé secrète par défaut ou trop courte",
                "Elle signe les sessions : quiconque la connaît peut forger une session. Générer une clé : "
                "python -c \"import secrets; print(secrets.token_urlsafe(50))\" puis la placer dans "
                "DJANGO_SECRET_KEY (les utilisateurs devront se reconnecter).",
            )
        )
    if "*" in settings.ALLOWED_HOSTS:
        constats.append(
            Constat(AVERTISSEMENT, "ALLOWED_HOSTS accepte tous les noms", "Lister l'IP ou le nom du NAS dans DJANGO_ALLOWED_HOSTS.")
        )
    if settings.DATABASES["default"]["PASSWORD"] in MOTS_DE_PASSE_FAIBLES:
        constats.append(
            Constat(
                AVERTISSEMENT,
                "Mot de passe de base de données par défaut",
                "Choisir un mot de passe propre dans DB_PASSWORD (à changer aussi dans la base existante).",
            )
        )
    if not settings.SESSION_COOKIE_SECURE or not settings.CSRF_COOKIE_SECURE:
        constats.append(
            Constat(
                AVERTISSEMENT,
                "Cookies de session non marqués « sécurisés »",
                "Sans conséquence en réseau local en HTTP. Dès que l'ERP est servi en HTTPS (reverse proxy), "
                "mettre DJANGO_COOKIES_SECURISES=true.",
            )
        )
    return constats


def constats_critiques():
    return [c for c in diagnostics() if c.niveau == CRITIQUE]
