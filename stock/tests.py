import datetime

from django.core.exceptions import ValidationError
from django.test import TestCase

from technique.models import Article

from .models import AlerteStock, Emplacement, Lot, MouvementStock, stock_total


class LotTests(TestCase):
    def test_article_non_gere_en_stock_refuse(self):
        article = Article.objects.create(
            reference="PIECE-STOCK-01", nature=Article.Nature.FABRIQUE, gere_en_stock=False
        )
        emplacement = Emplacement.objects.create(code="A1")
        lot = Lot(article=article, emplacement=emplacement)
        with self.assertRaises(ValidationError):
            lot.full_clean()

    def test_article_gere_en_stock_accepte(self):
        article = Article.objects.create(
            reference="TOLE-STOCK-01", nature=Article.Nature.MATIERE_PREMIERE
        )
        emplacement = Emplacement.objects.create(code="A2")
        lot = Lot(article=article, emplacement=emplacement)
        lot.full_clean()  # ne doit pas lever d'exception


class MouvementStockTests(TestCase):
    def setUp(self):
        self.article = Article.objects.create(
            reference="TOLE-STOCK-02",
            nature=Article.Nature.MATIERE_PREMIERE,
            stock_mini=10,
        )
        self.emplacement = Emplacement.objects.create(code="B1")
        self.lot = Lot.objects.create(article=self.article, emplacement=self.emplacement, quantite=0)

    def test_entree_augmente_la_quantite_du_lot(self):
        MouvementStock.objects.create(
            lot=self.lot,
            type_mouvement=MouvementStock.TypeMouvement.ENTREE,
            quantite=50,
            date_mouvement=datetime.date(2026, 1, 1),
        )
        self.lot.refresh_from_db()
        self.assertEqual(self.lot.quantite, 50)

    def test_sortie_diminue_la_quantite_du_lot(self):
        MouvementStock.objects.create(
            lot=self.lot, type_mouvement=MouvementStock.TypeMouvement.ENTREE, quantite=50
        )
        MouvementStock.objects.create(
            lot=self.lot, type_mouvement=MouvementStock.TypeMouvement.SORTIE, quantite=20
        )
        self.lot.refresh_from_db()
        self.assertEqual(self.lot.quantite, 30)

    def test_quantite_negative_ou_nulle_refusee(self):
        mouvement = MouvementStock(
            lot=self.lot, type_mouvement=MouvementStock.TypeMouvement.ENTREE, quantite=0
        )
        with self.assertRaises(ValidationError):
            mouvement.full_clean()

    def test_edition_ulterieure_ne_reapplique_pas_le_delta(self):
        mouvement = MouvementStock.objects.create(
            lot=self.lot, type_mouvement=MouvementStock.TypeMouvement.ENTREE, quantite=50
        )
        mouvement.reference_origine = "OF-999"
        mouvement.save()
        self.lot.refresh_from_db()
        self.assertEqual(self.lot.quantite, 50)


class AlerteStockTests(TestCase):
    def setUp(self):
        self.article = Article.objects.create(
            reference="TOLE-STOCK-03",
            nature=Article.Nature.MATIERE_PREMIERE,
            stock_mini=10,
        )
        self.emplacement = Emplacement.objects.create(code="C1")
        self.lot = Lot.objects.create(article=self.article, emplacement=self.emplacement, quantite=0)

    def _mouvement(self, type_mouvement, quantite):
        return MouvementStock.objects.create(lot=self.lot, type_mouvement=type_mouvement, quantite=quantite)

    def test_alerte_declenchee_sous_le_seuil(self):
        self._mouvement(MouvementStock.TypeMouvement.ENTREE, 5)  # stock=5 <= stock_mini=10
        self.assertEqual(
            AlerteStock.objects.filter(article=self.article, statut=AlerteStock.Statut.ACTIVE).count(), 1
        )

    def test_pas_de_doublon_alerte_active(self):
        self._mouvement(MouvementStock.TypeMouvement.ENTREE, 5)
        self._mouvement(MouvementStock.TypeMouvement.SORTIE, 1)  # stock=4, toujours sous le seuil
        self.assertEqual(
            AlerteStock.objects.filter(article=self.article, statut=AlerteStock.Statut.ACTIVE).count(), 1
        )

    def test_alerte_cloturee_automatiquement_au_retour_au_dessus_du_seuil(self):
        self._mouvement(MouvementStock.TypeMouvement.ENTREE, 5)
        self._mouvement(MouvementStock.TypeMouvement.ENTREE, 20)  # stock=25 > stock_mini=10
        alerte = AlerteStock.objects.get(article=self.article)
        self.assertEqual(alerte.statut, AlerteStock.Statut.TRAITEE)
        self.assertIsNotNone(alerte.date_traitement)

    def test_pas_dalerte_sans_stock_mini(self):
        article = Article.objects.create(reference="TOLE-STOCK-04", nature=Article.Nature.MATIERE_PREMIERE)
        emplacement = Emplacement.objects.create(code="C2")
        lot = Lot.objects.create(article=article, emplacement=emplacement, quantite=0)
        MouvementStock.objects.create(lot=lot, type_mouvement=MouvementStock.TypeMouvement.ENTREE, quantite=1)
        self.assertFalse(AlerteStock.objects.filter(article=article).exists())

    def test_stock_total_agrege_plusieurs_lots(self):
        autre_emplacement = Emplacement.objects.create(code="C3")
        autre_lot = Lot.objects.create(article=self.article, emplacement=autre_emplacement, quantite=0)
        self._mouvement(MouvementStock.TypeMouvement.ENTREE, 5)
        MouvementStock.objects.create(
            lot=autre_lot, type_mouvement=MouvementStock.TypeMouvement.ENTREE, quantite=7
        )
        self.assertEqual(stock_total(self.article), 12)
