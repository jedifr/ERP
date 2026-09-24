from django.urls import path

from .views import MargeReelleOrdreFabricationView, TauxChargePosteView

urlpatterns = [
    path(
        "pilotage/marge-reelle/<str:numero>/",
        MargeReelleOrdreFabricationView.as_view(),
        name="pilotage-marge-reelle",
    ),
    path(
        "pilotage/taux-charge/<str:nom_poste>/",
        TauxChargePosteView.as_view(),
        name="pilotage-taux-charge",
    ),
]
