from rest_framework import viewsets

from .models import Article, Gamme, Matiere, Nomenclature, PosteTravail, TarifPoste
from .serializers import (
    ArticleSerializer,
    GammeSerializer,
    MatiereSerializer,
    NomenclatureSerializer,
    PosteTravailSerializer,
    TarifPosteSerializer,
)


class MatiereViewSet(viewsets.ModelViewSet):
    queryset = Matiere.objects.all()
    serializer_class = MatiereSerializer


class ArticleViewSet(viewsets.ModelViewSet):
    queryset = Article.objects.select_related("matiere").all()
    serializer_class = ArticleSerializer
    filterset_fields = ["nature", "unite_cout", "gere_en_stock", "matiere"]
    search_fields = ["reference", "libelle"]


class PosteTravailViewSet(viewsets.ModelViewSet):
    queryset = PosteTravail.objects.all()
    serializer_class = PosteTravailSerializer
    filterset_fields = ["mode_calcul"]


class TarifPosteViewSet(viewsets.ModelViewSet):
    queryset = TarifPoste.objects.select_related("poste").all()
    serializer_class = TarifPosteSerializer
    filterset_fields = ["poste"]


class NomenclatureViewSet(viewsets.ModelViewSet):
    queryset = Nomenclature.objects.select_related("article_parent", "article_composant").all()
    serializer_class = NomenclatureSerializer
    filterset_fields = ["article_parent", "article_composant"]


class GammeViewSet(viewsets.ModelViewSet):
    queryset = Gamme.objects.select_related("article", "poste").all()
    serializer_class = GammeSerializer
    filterset_fields = ["article", "poste"]
