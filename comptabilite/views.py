from rest_framework import viewsets

from .models import (
    ArticleCompteAchat,
    ArticleCompteVente,
    CodeAnalytique,
    CompteComptable,
    EcritureComptable,
    JournalComptable,
    LigneEcriture,
    ParametresComptables,
    PosteGestion,
    TiersCompteComptable,
)
from .serializers import (
    ArticleCompteAchatSerializer,
    ArticleCompteVenteSerializer,
    CodeAnalytiqueSerializer,
    CompteComptableSerializer,
    EcritureComptableSerializer,
    JournalComptableSerializer,
    LigneEcritureSerializer,
    ParametresComptablesSerializer,
    PosteGestionSerializer,
    TiersCompteComptableSerializer,
)


class CompteComptableViewSet(viewsets.ModelViewSet):
    queryset = CompteComptable.objects.select_related("compte_parent").all()
    serializer_class = CompteComptableSerializer
    filterset_fields = ["classe", "systeme", "actif", "compte_parent"]
    search_fields = ["code", "libelle"]


class CodeAnalytiqueViewSet(viewsets.ModelViewSet):
    queryset = CodeAnalytique.objects.all()
    serializer_class = CodeAnalytiqueSerializer
    filterset_fields = ["actif"]
    search_fields = ["code", "libelle"]


class PosteGestionViewSet(viewsets.ModelViewSet):
    queryset = PosteGestion.objects.all()
    serializer_class = PosteGestionSerializer
    filterset_fields = ["groupe", "actif"]
    search_fields = ["code", "libelle"]


class ArticleCompteVenteViewSet(viewsets.ModelViewSet):
    queryset = ArticleCompteVente.objects.select_related("article", "compte_vente", "code_analytique").all()
    serializer_class = ArticleCompteVenteSerializer
    filterset_fields = ["article", "compte_vente"]


class ArticleCompteAchatViewSet(viewsets.ModelViewSet):
    queryset = ArticleCompteAchat.objects.select_related("article", "compte_achat", "code_analytique").all()
    serializer_class = ArticleCompteAchatSerializer
    filterset_fields = ["article", "compte_achat"]


class TiersCompteComptableViewSet(viewsets.ModelViewSet):
    queryset = TiersCompteComptable.objects.select_related("tiers", "compte_client", "compte_fournisseur").all()
    serializer_class = TiersCompteComptableSerializer
    filterset_fields = ["tiers"]


class JournalComptableViewSet(viewsets.ModelViewSet):
    queryset = JournalComptable.objects.all()
    serializer_class = JournalComptableSerializer
    filterset_fields = ["nature", "actif"]
    search_fields = ["code", "libelle"]


class ParametresComptablesViewSet(viewsets.ModelViewSet):
    queryset = ParametresComptables.objects.all()
    serializer_class = ParametresComptablesSerializer


class EcritureComptableViewSet(viewsets.ModelViewSet):
    queryset = EcritureComptable.objects.select_related("journal", "facture").all()
    serializer_class = EcritureComptableSerializer
    filterset_fields = ["journal", "facture"]
    search_fields = ["piece", "libelle"]


class LigneEcritureViewSet(viewsets.ModelViewSet):
    queryset = LigneEcriture.objects.select_related("ecriture", "compte").all()
    serializer_class = LigneEcritureSerializer
    filterset_fields = ["ecriture", "compte"]
