"""
Django settings for the ERP maison project.
"""

import os
from pathlib import Path

from django.templatetags.static import static
from django.urls import reverse_lazy
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


def _env_bool(name, default=False):
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


# Gestion de stock optionnelle : toutes les sociétés n'en ont pas. Mettre DJANGO_STOCK_ACTIF=false
# pour la désactiver entièrement (menu, tableau de bord, réceptions et livraisons sans lots).
# Même activée, elle reste facultative article par article (case « géré en stock »).
STOCK_ACTIF = _env_bool("DJANGO_STOCK_ACTIF", True)

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "django-insecure-change-me-in-production")

# SECURITY WARNING: don't run with debug turned on in production!
# Faux par défaut : une variable d'environnement oubliée ne doit jamais activer le mode debug.
DEBUG = _env_bool("DJANGO_DEBUG", False)

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

# Sessions et cookies. Les cookies « sécurisés » ne partent qu'en HTTPS : à activer seulement si
# l'ERP est servi en HTTPS (sinon la connexion en HTTP cesse de fonctionner).
SESSION_COOKIE_AGE = int(os.environ.get("DJANGO_SESSION_HEURES", "12")) * 3600  # une journée de travail
SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = _env_bool("DJANGO_COOKIES_SECURISES", False)
SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_HSTS_SECONDS", "0"))  # ex. 31536000 une fois le HTTPS validé
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

# Les fichiers déposés (DXF/DWG des pièces à découper, app `decoupe`) sont servis par Django
# même hors DEBUG : par défaut cet ERP reste sur le réseau local de l'atelier, éventuellement
# derrière le reverse proxy DSM (qui gère alors le HTTPS mais pas forcément /media/). Mettre à
# `false` si un reverse proxy dédié prend le relai pour /media/.
SERVE_MEDIA = _env_bool("DJANGO_SERVE_MEDIA", True)

# Application definition

INSTALLED_APPS = [
    "unfold",
    "unfold.contrib.simple_history",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "simple_history",
    "django_filters",
    "codification",
    "comptes",
    "documents",
    "technique",
    "decoupe",
    "commercial",
    "chiffrage",
    "stock",
    "facturation",
    "comptabilite",
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
    "simple_history.middleware.HistoryRequestMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# L'historique est en lecture seule (voir chiffrage.admin.HistoriqueLectureSeule).
SIMPLE_HISTORY_REVERT_DISABLED = True

# Connexion directe sur /admin/login/ (sans ?next=) : revenir à l'accueil de l'admin plutôt
# que sur /accounts/profile/ (Django par défaut), qui n'existe pas ici (404).
LOGIN_REDIRECT_URL = "/admin/"
# Page de connexion unique (sinon Django redirige vers /accounts/login/, qui n'existe pas).
LOGIN_URL = "/admin/login/"

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
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


# E-mail (relances de paiement, synthèse quotidienne). Sans serveur SMTP configuré, les messages
# sont écrits dans les journaux (`docker compose logs web`) au lieu d'être envoyés : rien ne
# part par erreur tant que DJANGO_EMAIL_HOST n'est pas renseigné.
EMAIL_HOST = os.environ.get("DJANGO_EMAIL_HOST", "")
EMAIL_BACKEND = (
    "django.core.mail.backends.smtp.EmailBackend" if EMAIL_HOST else "django.core.mail.backends.console.EmailBackend"
)
EMAIL_PORT = int(os.environ.get("DJANGO_EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.environ.get("DJANGO_EMAIL_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("DJANGO_EMAIL_PASSWORD", "")
EMAIL_USE_TLS = _env_bool("DJANGO_EMAIL_TLS", True)
DEFAULT_FROM_EMAIL = os.environ.get("DJANGO_EMAIL_EXPEDITEUR", "erp@localhost")
# Délai minimal entre deux relances d'une même facture (jours) et destinataires de la synthèse.
RELANCE_DELAI_MIN_JOURS = int(os.environ.get("DJANGO_RELANCE_DELAI_MIN_JOURS", "7"))
SYNTHESE_DESTINATAIRES = [e.strip() for e in os.environ.get("DJANGO_SYNTHESE_DESTINATAIRES", "").split(",") if e.strip()]

# Authentification : refus après trop d'échecs (voir comptes.connexions). Réglable ici.
AUTHENTICATION_BACKENDS = ["comptes.auth.ModelBackendProtege"]
CONNEXION_ECHECS_MAX = int(os.environ.get("DJANGO_CONNEXION_ECHECS_MAX", "5"))
CONNEXION_FENETRE_MINUTES = int(os.environ.get("DJANGO_CONNEXION_FENETRE_MINUTES", "15"))


# Password validation
# https://docs.djangoproject.com/en/5.2/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
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

# Fichiers déposés par les utilisateurs (ex. fichiers DXF/DWG des pièces à découper)
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# Default primary key field type
# https://docs.djangoproject.com/en/5.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# Django REST Framework
# https://www.django-rest-framework.org/api-guide/settings/

REST_FRAMEWORK = {
    # Les montants sont des Decimal en base ; l'API les renvoie comme des nombres JSON
    # (comme avant le passage en Decimal), pas comme des chaînes.
    "COERCE_DECIMAL_TO_STRING": False,
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
    # Sécurisé par défaut : lecture = permission « voir », écriture = ajout/modification/
    # suppression du modèle (voir comptes.permissions). Un nouveau ViewSet est donc protégé
    # sans rien ajouter ; une vue sans modèle (APIView) doit déclarer sa propre permission.
    "DEFAULT_PERMISSION_CLASSES": ["comptes.permissions.ModelPermissionsAvecLecture"],
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


# Menu latéral, organisé selon le chemin d'une commande : on vend, on produit, on achète, on stocke, on
# comptabilise ; les données de base et l'administration sont regroupées à part (et repliées par défaut).
# Chaque entrée n'est visible que pour qui a le droit « voir » le modèle correspondant.
def _menu(titre, icone, app, modele, droit=None, url=None):
    droit = droit or f"{app}.view_{modele}"
    verifier = droit if callable(droit) else (lambda request, d=droit: request.user.has_perm(d))
    return {
        "title": titre,
        "icon": icone,
        "link": reverse_lazy(url or f"admin:{app}_{modele}_changelist"),
        "permission": verifier,
    }


def _superutilisateur(request):
    return request.user.is_superuser


NAVIGATION = [
    {
        "title": "Ventes",
        "separator": True,
        "items": [
            _menu("Devis", "description", "chiffrage", "devis"),
            _menu("Commandes clients", "shopping_cart", "chiffrage", "commande"),
            _menu("Livraisons (BL)", "local_shipping", "chiffrage", "livraison"),
            _menu("Factures", "receipt_long", "facturation", "facture"),
        ],
    },
    {
        "title": "Production",
        "separator": True,
        "items": [
            _menu("Ordres de fabrication", "build", "chiffrage", "ordrefabrication"),
            _menu("Pièces à découper", "content_cut", "decoupe", "piecedecoupe"),
            _menu("Imbrications", "grid_view", "decoupe", "imbricationjob"),
            _menu("Profils d'import", "layers", "decoupe", "profilimportdecoupe"),
            _menu("Paramètres de coupe", "speed", "decoupe", "parametrecoupe"),
            _menu("Formats de tôle", "crop_landscape", "decoupe", "formattole"),
        ],
    },
    {
        "title": "Achats",
        "separator": True,
        "items": [
            _menu("Commandes fournisseur", "local_shipping", "achats", "commandefournisseur"),
            _menu("Réceptions", "move_to_inbox", "achats", "reception"),
            _menu("Factures fournisseur", "request_quote", "achats", "facturefournisseur"),
            _menu("Envois sous-traitance", "outbound", "soustraitance", "envoisoustraitance"),
            _menu("Retours sous-traitance", "keyboard_return", "soustraitance", "retoursoustraitance"),
            _menu("Fournisseurs d'article", "contact_page", "achats", "articlefournisseur"),
            _menu("Tarifs d'achat", "sell", "achats", "tarifachatarticle"),
        ],
    },
    {
        "title": "Stock",
        "separator": True,
        "items": [
            _menu("Lots", "inventory_2", "stock", "lot"),
            _menu("Mouvements de stock", "sync_alt", "stock", "mouvementstock"),
            _menu("Alertes de stock", "warning", "stock", "alertestock"),
            _menu("Transferts", "swap_horiz", "stock", "transfert"),
            _menu("Inventaires", "fact_check", "stock", "inventaire"),
            _menu("Emplacements", "warehouse", "stock", "emplacement"),
        ],
    },
    {
        "title": "Comptabilité",
        "separator": True,
        "collapsible": True,
        "items": [
            _menu("Écritures comptables", "list_alt", "comptabilite", "ecriturecomptable"),
            _menu("Journaux comptables", "book", "comptabilite", "journalcomptable"),
            _menu("Plan comptable", "account_balance", "comptabilite", "comptecomptable"),
            _menu("Postes de gestion", "workspaces", "comptabilite", "postegestion"),
            _menu("Codes analytiques", "label", "comptabilite", "codeanalytique"),
            _menu("Comptes de vente d'article", "trending_up", "comptabilite", "articlecomptevente"),
            _menu("Comptes d'achat d'article", "trending_down", "comptabilite", "articlecompteachat"),
            _menu("Comptes comptables de tiers", "badge", "comptabilite", "tierscomptecomptable"),
            _menu("Paramètres comptables", "settings", "comptabilite", "parametrescomptables"),
        ],
    },
    {
        "title": "Données de base",
        "separator": True,
        "collapsible": True,
        "items": [
            _menu("Tiers (clients, fournisseurs)", "handshake", "commercial", "tiers"),
            _menu("Adresses", "location_on", "commercial", "adresse"),
            _menu("Contacts", "contacts", "commercial", "contact"),
            _menu("Articles", "category", "technique", "article"),
            _menu("Matières", "science", "technique", "matiere"),
            _menu("Nomenclatures", "account_tree", "technique", "nomenclature"),
            _menu("Gammes", "route", "technique", "gamme"),
            _menu("Postes de travail", "precision_manufacturing", "technique", "postetravail"),
            _menu("Tarifs de poste", "payments", "technique", "tarifposte"),
            _menu("Taux de TVA", "percent", "commercial", "tauxtva"),
            _menu("Conditions de paiement", "schedule", "commercial", "conditionpaiement"),
            _menu("Délais proposés", "timer", "commercial", "delaipropose"),
            _menu("Devises", "euro", "commercial", "devise"),
            _menu("Pays", "public", "commercial", "pays"),
        ],
    },
    {
        "title": "Administration",
        "separator": True,
        "collapsible": True,
        "items": [
            _menu("Société (en-tête des documents)", "business", "comptes", "societe"),
            _menu("Modèles de documents (PDF)", "edit_document", "documents", "modeledocument", _superutilisateur),
            _menu("Règles de codification", "tag", "codification", "reglecodification"),
            _menu("Utilisateurs", "person", "auth", "user", _superutilisateur),
            _menu("Groupes", "groups", "auth", "group", _superutilisateur),
            _menu("Audit des droits", "admin_panel_settings", "auth", "user", _superutilisateur, url="admin:auth_user_audit_droits"),
            _menu("Journal des connexions", "login", "comptes", "evenementconnexion"),
        ],
    },
]


# Unfold — thème de l'admin Django
# https://unfoldadmin.com/docs/configuration/settings/

UNFOLD = {
    "STYLES": [
        lambda request: static("comptes/admin_extra.css"),
        lambda request: static("comptes/fiche_deux_colonnes.css"),
        lambda request: static("comptes/theme.css"),
    ],
    "SCRIPTS": [
        lambda request: static("comptes/anti_double_clic.js"),
        lambda request: static("comptes/filtre_client.js"),
    ],
    "SITE_TITLE": "ERP maison",
    "SITE_HEADER": "ERP maison",
    "SITE_SUBHEADER": "Métallurgie & chaudronnerie",
    "SITE_SYMBOL": "factory",
    "ACCOUNT": {
        "navigation": [
            {"title": "Mes colonnes", "link": reverse_lazy("mes_colonnes")},
            {"title": "Changer le mot de passe", "link": reverse_lazy("admin:password_change")},
        ],
    },
    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": False,
    "DASHBOARD_CALLBACK": "comptes.dashboard.dashboard_callback",
    "GLOBAL_CALLBACK": "comptes.layout.global_callback",
    "ENVIRONMENT": "comptes.version.badge_environnement",
    "ENVIRONMENT_TITLE_PREFIX": "comptes.version.prefixe_titre",
    # Identité "atelier" : ambre/acier plutôt que le violet par défaut d'Unfold.
    "COLORS": {
        "primary": {
            "50": "oklch(98.7% .022 95.277)",
            "100": "oklch(96.2% .059 95.617)",
            "200": "oklch(92.4% .12 95.746)",
            "300": "oklch(87.9% .169 91.605)",
            "400": "oklch(82.8% .189 84.429)",
            "500": "oklch(76.9% .188 70.08)",
            "600": "oklch(66.6% .179 58.318)",
            "700": "oklch(55.5% .163 48.998)",
            "800": "oklch(47.3% .137 46.201)",
            "900": "oklch(41.4% .112 45.904)",
            "950": "oklch(27.9% .077 45.635)",
        },
    },
    # Recherche globale (Ctrl+K / ⌘K) : documents et écrans, voir comptes/recherche.py.
    "COMMAND": {
        "search_models": False,
        "search_callback": "comptes.recherche.recherche_globale",
        "show_history": True,
    },
    "SIDEBAR": {
        "show_search": True,
        "command_search": True,
        "show_all_applications": False,
        "navigation": NAVIGATION,
    },
}


if not STOCK_ACTIF:
    UNFOLD["SIDEBAR"]["navigation"] = [g for g in UNFOLD["SIDEBAR"]["navigation"] if g.get("title") != "Stock"]
