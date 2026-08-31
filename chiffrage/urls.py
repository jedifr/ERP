from rest_framework.routers import DefaultRouter

from .views import (
    CommandeViewSet,
    DevisLigneOperationViewSet,
    DevisLigneViewSet,
    DevisViewSet,
    OperationOFViewSet,
    OrdreFabricationViewSet,
)

router = DefaultRouter()
router.register("devis", DevisViewSet, basename="devis")
router.register("devis-lignes", DevisLigneViewSet, basename="devis-ligne")
router.register("devis-ligne-operations", DevisLigneOperationViewSet, basename="devis-ligne-operation")
router.register("commandes", CommandeViewSet, basename="commande")
router.register("ordres-fabrication", OrdreFabricationViewSet, basename="ordre-fabrication")
router.register("operations-of", OperationOFViewSet, basename="operation-of")

urlpatterns = router.urls
