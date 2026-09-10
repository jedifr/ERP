from rest_framework.routers import DefaultRouter

from .views import (
    CompteComptableViewSet,
    EcritureComptableViewSet,
    JournalComptableViewSet,
    LigneEcritureViewSet,
    ParametresComptablesViewSet,
)

router = DefaultRouter()
router.register("comptes-comptables", CompteComptableViewSet, basename="compte-comptable")
router.register("journaux-comptables", JournalComptableViewSet, basename="journal-comptable")
router.register("parametres-comptables", ParametresComptablesViewSet, basename="parametres-comptable")
router.register("ecritures-comptables", EcritureComptableViewSet, basename="ecriture-comptable")
router.register("lignes-ecriture", LigneEcritureViewSet, basename="ligne-ecriture")

urlpatterns = router.urls
