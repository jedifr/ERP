from rest_framework.routers import DefaultRouter

from .views import (
    ArticleCompteAchatViewSet,
    ArticleCompteVenteViewSet,
    CodeAnalytiqueViewSet,
    CompteComptableViewSet,
    EcritureComptableViewSet,
    JournalComptableViewSet,
    LigneEcritureViewSet,
    ParametresComptablesViewSet,
    PosteGestionViewSet,
    TiersCompteComptableViewSet,
)

router = DefaultRouter()
router.register("comptes-comptables", CompteComptableViewSet, basename="compte-comptable")
router.register("codes-analytiques", CodeAnalytiqueViewSet, basename="code-analytique")
router.register("postes-gestion", PosteGestionViewSet, basename="poste-gestion")
router.register("articles-comptes-vente", ArticleCompteVenteViewSet, basename="article-compte-vente")
router.register("articles-comptes-achat", ArticleCompteAchatViewSet, basename="article-compte-achat")
router.register("tiers-comptes-comptables", TiersCompteComptableViewSet, basename="tiers-compte-comptable")
router.register("journaux-comptables", JournalComptableViewSet, basename="journal-comptable")
router.register("parametres-comptables", ParametresComptablesViewSet, basename="parametres-comptable")
router.register("ecritures-comptables", EcritureComptableViewSet, basename="ecriture-comptable")
router.register("lignes-ecriture", LigneEcritureViewSet, basename="ligne-ecriture")

urlpatterns = router.urls
