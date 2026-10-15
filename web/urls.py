from django.urls import path

from .api import CatalogueWeb, DemandeWebVue, PrixWebVue

urlpatterns = [
    path("catalogue/", CatalogueWeb.as_view(), name="web_catalogue"),
    path("prix/", PrixWebVue.as_view(), name="web_prix"),
    path("demandes/", DemandeWebVue.as_view(), name="web_demandes"),
]
