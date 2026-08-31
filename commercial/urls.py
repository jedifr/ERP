from rest_framework.routers import DefaultRouter

from .views import AdresseViewSet, ContactViewSet, TiersViewSet

router = DefaultRouter()
router.register("tiers", TiersViewSet, basename="tiers")
router.register("adresses", AdresseViewSet, basename="adresse")
router.register("contacts", ContactViewSet, basename="contact")

urlpatterns = router.urls
