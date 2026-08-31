from rest_framework.routers import DefaultRouter

from .views import AdresseViewSet, TiersViewSet

router = DefaultRouter()
router.register("tiers", TiersViewSet, basename="tiers")
router.register("adresses", AdresseViewSet, basename="adresse")

urlpatterns = router.urls
