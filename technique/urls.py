from rest_framework.routers import DefaultRouter

from .views import (
    ArticleViewSet,
    GammeViewSet,
    MatiereViewSet,
    NomenclatureViewSet,
    PosteTravailViewSet,
    TarifPosteViewSet,
)

router = DefaultRouter()
router.register("matieres", MatiereViewSet, basename="matiere")
router.register("articles", ArticleViewSet, basename="article")
router.register("postes-travail", PosteTravailViewSet, basename="poste-travail")
router.register("tarifs-poste", TarifPosteViewSet, basename="tarif-poste")
router.register("nomenclatures", NomenclatureViewSet, basename="nomenclature")
router.register("gammes", GammeViewSet, basename="gamme")

urlpatterns = router.urls
