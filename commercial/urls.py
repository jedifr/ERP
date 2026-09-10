from rest_framework.routers import DefaultRouter

from .views import AdresseViewSet, ContactTelephoneViewSet, ContactViewSet, TiersViewSet

router = DefaultRouter()
router.register("tiers", TiersViewSet, basename="tiers")
router.register("adresses", AdresseViewSet, basename="adresse")
router.register("contacts", ContactViewSet, basename="contact")
router.register("contacts-telephones", ContactTelephoneViewSet, basename="contact-telephone")

urlpatterns = router.urls
