from rest_framework.routers import DefaultRouter

from .views import AlerteStockViewSet, EmplacementViewSet, LotViewSet, MouvementStockViewSet

router = DefaultRouter()
router.register("emplacements", EmplacementViewSet, basename="emplacement")
router.register("lots", LotViewSet, basename="lot")
router.register("mouvements-stock", MouvementStockViewSet, basename="mouvement-stock")
router.register("alertes-stock", AlerteStockViewSet, basename="alerte-stock")

urlpatterns = router.urls
