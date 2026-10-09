"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path

from comptes.a_completer import a_completer_view
from comptes.colonnes import mes_colonnes
from comptes.navigation import navigation_view
from comptes.parametrage import parametrage_view
from comptes.views import media_protege

urlpatterns = [
    path("admin/mes-colonnes/", admin.site.admin_view(mes_colonnes), name="mes_colonnes"),
    path("admin/navigation/<str:app>/<str:modele>/", admin.site.admin_view(navigation_view), name="navigation_documents"),
    path("admin/a-completer/", admin.site.admin_view(a_completer_view), name="a_completer"),
    path("admin/parametrage/", admin.site.admin_view(parametrage_view), name="parametrage"),
    path("admin/", admin.site.urls),
    path("api/v1/", include("technique.urls")),
    path("api/v1/", include("decoupe.urls")),
    path("api/v1/", include("commercial.urls")),
    path("api/v1/", include("chiffrage.urls")),
    path("api/v1/", include("stock.urls")),
    path("api/v1/", include("facturation.urls")),
    path("api/v1/", include("comptabilite.urls")),
    path("api/v1/", include("achats.urls")),
    path("api/v1/", include("soustraitance.urls")),
    path("api/v1/", include("pilotage.urls")),
    path("api-auth/", include("rest_framework.urls")),
]

if settings.SERVE_MEDIA:
    # Servi par Django même hors DEBUG (`django.conf.urls.static.static()` refuse hors DEBUG) :
    # choix assumé pour cet ERP interne au réseau local (voir `SERVE_MEDIA` dans
    # `config/settings.py`). Réservé aux comptes connectés habilités : voir comptes.views.
    urlpatterns += [
        re_path(r"^media/(?P<path>.*)$", media_protege),
    ]
