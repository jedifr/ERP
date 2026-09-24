from rest_framework.routers import DefaultRouter

from .views import (
    ArticleFournisseurViewSet,
    CommandeFournisseurViewSet,
    FactureFournisseurViewSet,
    LigneCommandeFournisseurViewSet,
    ReceptionLigneViewSet,
    ReceptionViewSet,
    TarifAchatArticleViewSet,
)

router = DefaultRouter()
router.register("articles-fournisseur", ArticleFournisseurViewSet, basename="article-fournisseur")
router.register("tarifs-achat", TarifAchatArticleViewSet, basename="tarif-achat")
router.register("commandes-fournisseur", CommandeFournisseurViewSet, basename="commande-fournisseur")
router.register(
    "lignes-commande-fournisseur", LigneCommandeFournisseurViewSet, basename="ligne-commande-fournisseur"
)
router.register("receptions", ReceptionViewSet, basename="reception")
router.register("receptions-lignes", ReceptionLigneViewSet, basename="reception-ligne")
router.register("factures-fournisseur", FactureFournisseurViewSet, basename="facture-fournisseur")

urlpatterns = router.urls
