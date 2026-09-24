from rest_framework.routers import DefaultRouter

from .views import ImbricationJobViewSet, PieceDecoupeViewSet

router = DefaultRouter()
router.register("pieces-decoupe", PieceDecoupeViewSet, basename="piece-decoupe")
router.register("imbrications", ImbricationJobViewSet, basename="imbrication")

urlpatterns = router.urls
