from rest_framework.routers import DefaultRouter

from .views import EnvoiSousTraitanceViewSet, RetourSousTraitanceViewSet

router = DefaultRouter()
router.register("envois-sous-traitance", EnvoiSousTraitanceViewSet, basename="envoi-sous-traitance")
router.register("retours-sous-traitance", RetourSousTraitanceViewSet, basename="retour-sous-traitance")

urlpatterns = router.urls
