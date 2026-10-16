from django.urls import path

from .api import CatalogueWeb, PrixWebVue

urlpatterns = [
    path("catalogue/", CatalogueWeb.as_view(), name="web_catalogue"),
    path("prix/", PrixWebVue.as_view(), name="web_prix"),
]
