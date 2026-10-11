from rest_framework.routers import DefaultRouter

from .views import (
    AlerteStockViewSet,
    EmplacementViewSet,
    InventaireLigneViewSet,
    InventaireViewSet,
    LotViewSet,
    MouvementStockViewSet,
    TransfertViewSet,
)

router = DefaultRouter()
router.register("emplacements", EmplacementViewSet, basename="emplacement")
router.register("lots", LotViewSet, basename="lot")
router.register("mouvements-stock", MouvementStockViewSet, basename="mouvement-stock")
router.register("alertes-stock", AlerteStockViewSet, basename="alerte-stock")
router.register("transferts-stock", TransfertViewSet, basename="transfert-stock")
router.register("inventaires", InventaireViewSet, basename="inventaire")
router.register("inventaire-lignes", InventaireLigneViewSet, basename="inventaire-ligne")

urlpatterns = router.urls
