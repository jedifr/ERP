from rest_framework.routers import DefaultRouter

from .views import CompteComptableViewSet, JournalComptableViewSet

router = DefaultRouter()
router.register("comptes-comptables", CompteComptableViewSet, basename="compte-comptable")
router.register("journaux-comptables", JournalComptableViewSet, basename="journal-comptable")

urlpatterns = router.urls
