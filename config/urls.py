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
from django.views.static import serve as serve_static

urlpatterns = [
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
    # `django.conf.urls.static.static()` refuse de servir les fichiers hors DEBUG (c'est
    # voulu pour un vrai déploiement Internet) ; on appelle donc la vue directement, ce qui
    # est un choix assumé pour cet ERP interne au réseau local (voir `SERVE_MEDIA` dans
    # `config/settings.py`).
    urlpatterns += [
        re_path(r"^media/(?P<path>.*)$", serve_static, {"document_root": settings.MEDIA_ROOT}),
    ]
