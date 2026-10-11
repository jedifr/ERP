from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import translation

from decoupe.models import PieceDecoupe

from .models import Article


class FamillesArticlesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.fab = Article.objects.create(reference="F1", nature="fabrique", libelle="Rectangle")
        cls.achete = Article.objects.create(reference="C1", nature="composant", libelle="Vis")
        cls.matiere = Article.objects.create(reference="M1", nature="matiere_premiere", libelle="Tôle")
        PieceDecoupe.objects.create(
            nom="Rect", article=cls.fab, largeur_mm=200, hauteur_mm=100, fichier_source="decoupe/sources/rect.dxf",
            format_source="dxf", contour_json={"exterieur": [[0, 0], [200, 0], [200, 100], [0, 100]], "trous": []},
        )

    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("fam", "f@example.com", "pass1234"))

    def liste(self, filtre):
        return self.client.get("/admin/technique/article/" + filtre)

    def test_chaque_famille_ne_montre_que_ses_articles(self):
        self.assertEqual([a.pk for a in self.liste("?nature__exact=fabrique").context["cl"].result_list], ["F1"])
        self.assertEqual([a.pk for a in self.liste("?nature__in=composant,consommable,service_achete").context["cl"].result_list], ["C1"])
        self.assertEqual([a.pk for a in self.liste("?nature__exact=matiere_premiere").context["cl"].result_list], ["M1"])

    def test_colonnes_adaptees_et_miniature(self):
        rep = self.liste("?nature__exact=fabrique")
        self.assertContains(rep, "<svg")
        self.assertNotContains(rep, "column-cout_unitaire")
        self.assertContains(self.liste("?nature__exact=matiere_premiere"), "column-cout_unitaire")

    def test_fiche_fabrique_montre_apercu_et_fichier_source(self):
        rep = self.client.get("/admin/technique/article/F1/change/")
        self.assertContains(rep, "Aperçu de la pièce")
        self.assertContains(rep, "rect.dxf")

    def test_creation_depuis_une_famille_preselectionne_la_nature(self):
        rep = self.client.get("/admin/technique/article/add/?_changelist_filters=nature__exact%3Dfabrique")
        self.assertEqual(rep.context["adminform"].form.initial.get("nature"), "fabrique")


class TraductionsTests(TestCase):
    def test_chaines_unfold_traduites(self):
        with translation.override("fr"):
            self.assertEqual(translation.gettext("Type to search"), "Rechercher…")
            self.assertEqual(translation.gettext("No results found"), "Aucun résultat")

    def test_catalogue_mo_a_jour(self):
        from django.conf import settings
        from comptes.i18n import compiler

        po = settings.BASE_DIR / "locale/fr/LC_MESSAGES/django.po"
        mo = po.with_suffix(".mo")
        self.assertEqual(mo.read_bytes(), compiler(po), "Relancer : python manage.py compiler_traductions")
