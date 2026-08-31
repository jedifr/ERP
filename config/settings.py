"""
Django settings for the ERP maison project.
"""

import os
from pathlib import Path

from django.urls import reverse_lazy
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


def _env_bool(name, default=False):
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "django-insecure-change-me-in-production")

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = _env_bool("DJANGO_DEBUG", True)

ALLOWED_HOSTS = [h.strip() for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]

# Derrière un reverse proxy (ex. le reverse proxy Synology DSM) qui termine le
# HTTPS et transmet en HTTP au conteneur : indispensable pour que Django sache
# que la requête d'origine était bien en HTTPS (redirections, cookies secure,
# vérification CSRF sur les formulaires comme l'admin).
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True

# Origines HTTPS autorisées à soumettre des formulaires (protection CSRF de
# Django). Ex. : https://192.168.1.50:441 pour un reverse proxy Synology.
CSRF_TRUSTED_ORIGINS = [
    o.strip() for o in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()
]


# Application definition

INSTALLED_APPS = [
    "unfold",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "django_filters",
    "technique",
    "commercial",
    "chiffrage",
    "stock",
    "facturation",
    "achats",
    "soustraitance",
    "pilotage",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


# Database
# https://docs.djangoproject.com/en/5.2/ref/settings/#databases

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("DB_NAME", "erp_db"),
        "USER": os.environ.get("DB_USER", "erp_user"),
        "PASSWORD": os.environ.get("DB_PASSWORD", "erp_dev_password"),
        "HOST": os.environ.get("DB_HOST", "localhost"),
        "PORT": os.environ.get("DB_PORT", "5432"),
    }
}


# Password validation
# https://docs.djangoproject.com/en/5.2/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# Internationalization
# https://docs.djangoproject.com/en/5.2/topics/i18n/

LANGUAGE_CODE = "fr-fr"

TIME_ZONE = "Europe/Paris"

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.2/howto/static-files/

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# Default primary key field type
# https://docs.djangoproject.com/en/5.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# Django REST Framework
# https://www.django-rest-framework.org/api-guide/settings/

REST_FRAMEWORK = {
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
}


# Synchronisation avec l'outil de planification d'atelier (voir chiffrage/planning_sync.py)
# Laisser PLANNING_API_URL vide tant que l'API du planning atelier n'est pas définie :
# les OF restent créés localement, avec statut_synchro="en_attente".
PLANNING_API_URL = os.environ.get("PLANNING_API_URL", "")
PLANNING_API_KEY = os.environ.get("PLANNING_API_KEY", "")
PLANNING_SYNC_MAX_TENTATIVES = int(os.environ.get("PLANNING_SYNC_MAX_TENTATIVES", "5"))


# Logging
# Le handler "console" par défaut de Django n'écrit que si DEBUG=True : sans
# ceci, les erreurs 400 (ex. DisallowedHost) ou CSRF n'apparaissent nulle part
# dans `docker compose logs` une fois DEBUG=False. On force donc les logs
# Django (avertissements et erreurs) vers stdout, visibles par Docker.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {"class": "logging.StreamHandler"},
    },
    "loggers": {
        "django": {"handlers": ["console"], "level": "WARNING"},
    },
}


# Unfold — thème de l'admin Django
# https://unfoldadmin.com/docs/configuration/settings/

UNFOLD = {
    "SITE_TITLE": "ERP maison",
    "SITE_HEADER": "ERP maison",
    "SITE_SYMBOL": "factory",
    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": False,
    "SIDEBAR": {
        "show_search": True,
        "show_all_applications": False,
        "navigation": [
            {
                "title": "Socle technique",
                "separator": True,
                "items": [
                    {
                        "title": "Matières",
                        "icon": "science",
                        "link": reverse_lazy("admin:technique_matiere_changelist"),
                    },
                    {
                        "title": "Articles",
                        "icon": "category",
                        "link": reverse_lazy("admin:technique_article_changelist"),
                    },
                    {
                        "title": "Postes de travail",
                        "icon": "precision_manufacturing",
                        "link": reverse_lazy("admin:technique_postetravail_changelist"),
                    },
                    {
                        "title": "Tarifs de poste",
                        "icon": "payments",
                        "link": reverse_lazy("admin:technique_tarifposte_changelist"),
                    },
                    {
                        "title": "Nomenclatures",
                        "icon": "account_tree",
                        "link": reverse_lazy("admin:technique_nomenclature_changelist"),
                    },
                    {
                        "title": "Gammes",
                        "icon": "route",
                        "link": reverse_lazy("admin:technique_gamme_changelist"),
                    },
                ],
            },
            {
                "title": "Commercial",
                "separator": True,
                "items": [
                    {
                        "title": "Tiers",
                        "icon": "handshake",
                        "link": reverse_lazy("admin:commercial_tiers_changelist"),
                    },
                    {
                        "title": "Adresses",
                        "icon": "location_on",
                        "link": reverse_lazy("admin:commercial_adresse_changelist"),
                    },
                    {
                        "title": "Contacts",
                        "icon": "contacts",
                        "link": reverse_lazy("admin:commercial_contact_changelist"),
                    },
                ],
            },
            {
                "title": "Chiffrage et production",
                "separator": True,
                "items": [
                    {
                        "title": "Devis",
                        "icon": "request_quote",
                        "link": reverse_lazy("admin:chiffrage_devis_changelist"),
                    },
                    {
                        "title": "Commandes",
                        "icon": "shopping_cart",
                        "link": reverse_lazy("admin:chiffrage_commande_changelist"),
                    },
                    {
                        "title": "Ordres de fabrication",
                        "icon": "build",
                        "link": reverse_lazy("admin:chiffrage_ordrefabrication_changelist"),
                    },
                ],
            },
            {
                "title": "Stock",
                "separator": True,
                "items": [
                    {
                        "title": "Emplacements",
                        "icon": "warehouse",
                        "link": reverse_lazy("admin:stock_emplacement_changelist"),
                    },
                    {
                        "title": "Lots",
                        "icon": "inventory_2",
                        "link": reverse_lazy("admin:stock_lot_changelist"),
                    },
                    {
                        "title": "Mouvements de stock",
                        "icon": "sync_alt",
                        "link": reverse_lazy("admin:stock_mouvementstock_changelist"),
                    },
                    {
                        "title": "Alertes de stock",
                        "icon": "warning",
                        "link": reverse_lazy("admin:stock_alertestock_changelist"),
                    },
                ],
            },
            {
                "title": "Achats et sous-traitance",
                "separator": True,
                "items": [
                    {
                        "title": "Commandes fournisseur",
                        "icon": "local_shipping",
                        "link": reverse_lazy("admin:achats_commandefournisseur_changelist"),
                    },
                    {
                        "title": "Réceptions",
                        "icon": "move_to_inbox",
                        "link": reverse_lazy("admin:achats_reception_changelist"),
                    },
                    {
                        "title": "Envois sous-traitance",
                        "icon": "outbound",
                        "link": reverse_lazy("admin:soustraitance_envoisoustraitance_changelist"),
                    },
                    {
                        "title": "Retours sous-traitance",
                        "icon": "keyboard_return",
                        "link": reverse_lazy("admin:soustraitance_retoursoustraitance_changelist"),
                    },
                ],
            },
            {
                "title": "Facturation",
                "separator": True,
                "items": [
                    {
                        "title": "Factures",
                        "icon": "receipt_long",
                        "link": reverse_lazy("admin:facturation_facture_changelist"),
                    },
                ],
            },
        ],
    },
}
