from rest_framework.routers import DefaultRouter

from .views import FactureLigneViewSet, FactureViewSet

router = DefaultRouter()
router.register("factures", FactureViewSet, basename="facture")
router.register("facture-lignes", FactureLigneViewSet, basename="facture-ligne")

urlpatterns = router.urls
