from rest_framework.routers import DefaultRouter

from .views import (
    CommandeFournisseurViewSet,
    LigneCommandeFournisseurViewSet,
    ReceptionLigneViewSet,
    ReceptionViewSet,
)

router = DefaultRouter()
router.register("commandes-fournisseur", CommandeFournisseurViewSet, basename="commande-fournisseur")
router.register(
    "lignes-commande-fournisseur", LigneCommandeFournisseurViewSet, basename="ligne-commande-fournisseur"
)
router.register("receptions", ReceptionViewSet, basename="reception")
router.register("receptions-lignes", ReceptionLigneViewSet, basename="reception-ligne")

urlpatterns = router.urls
