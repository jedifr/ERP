from django.urls import path

from .api import BibliothequeWebVue, CatalogueWeb, DemandeWebVue, DxfWebVue, PrixWebVue

urlpatterns = [
    path("catalogue/", CatalogueWeb.as_view(), name="web_catalogue"),
    path("prix/", PrixWebVue.as_view(), name="web_prix"),
    path("bibliotheque/", BibliothequeWebVue.as_view(), name="web_bibliotheque"),
    path("dxf/", DxfWebVue.as_view(), name="web_dxf"),
    path("demandes/", DemandeWebVue.as_view(), name="web_demandes"),
]
