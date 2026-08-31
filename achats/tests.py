import datetime

from django.core.exceptions import ValidationError
from django.test import TestCase

from commercial.models import Tiers
from stock.models import AlerteStock, Emplacement, Lot, MouvementStock
from technique.models import Article

from .models import AchatsError, CommandeFournisseur, LigneCommandeFournisseur, Reception, ReceptionLigne


class ReceptionTests(TestCase):
    def setUp(self):
        self.fournisseur = Tiers.objects.create(
            code="FOUR-001", raison_sociale="Fournisseur Test", type_tiers=Tiers.TypeTiers.FOURNISSEUR
        )
        self.article = Article.objects.create(
            reference="TOLE-ACH-01", nature=Article.Nature.MATIERE_PREMIERE, cout_unitaire=2.0
        )
        self.commande = CommandeFournisseur.objects.create(
            numero="CF-001", fournisseur=self.fournisseur, date_commande=datetime.date(2026, 1, 1)
        )
        self.ligne = LigneCommandeFournisseur.objects.create(
            commande_fournisseur=self.commande,
            article=self.article,
            quantite_commandee=100,
            prix_unitaire_achat=2.0,
        )
        self.reception = Reception.objects.create(
            numero="REC-001", commande_fournisseur=self.commande, date_reception=datetime.date(2026, 1, 10)
        )

    def test_sans_lot_leve_erreur(self):
        rl = ReceptionLigne(reception=self.reception, ligne_commande_fournisseur=self.ligne, quantite_recue=50)
        with self.assertRaises(AchatsError):
            rl.save()

    def test_reception_cree_mouvement_et_maj_quantite_recue(self):
        emplacement = Emplacement.objects.create(code="ACH-A1")
        Lot.objects.create(article=self.article, emplacement=emplacement, quantite=0)

        ReceptionLigne.objects.create(
            reception=self.reception, ligne_commande_fournisseur=self.ligne, quantite_recue=50
        )

        self.ligne.refresh_from_db()
        self.assertEqual(self.ligne.quantite_recue, 50)

        lot = Lot.objects.get(article=self.article)
        self.assertEqual(lot.quantite, 50)
        mouvement = MouvementStock.objects.get(lot=lot)
        self.assertEqual(mouvement.type_mouvement, MouvementStock.TypeMouvement.ENTREE)
        self.assertEqual(mouvement.reference_origine, "RECEPTION-REC-001")

    def test_plusieurs_lots_leve_erreur(self):
        e1 = Emplacement.objects.create(code="ACH-B1")
        e2 = Emplacement.objects.create(code="ACH-B2")
        Lot.objects.create(article=self.article, emplacement=e1, quantite=0)
        Lot.objects.create(article=self.article, emplacement=e2, quantite=0)

        rl = ReceptionLigne(reception=self.reception, ligne_commande_fournisseur=self.ligne, quantite_recue=10)
        with self.assertRaises(AchatsError):
            rl.save()

    def test_depassement_quantite_commandee_refuse(self):
        rl = ReceptionLigne(
            reception=self.reception, ligne_commande_fournisseur=self.ligne, quantite_recue=150
        )
        with self.assertRaises(ValidationError):
            rl.full_clean()


class AlerteStockClotureTests(TestCase):
    def test_creation_ligne_cloture_alerte_active(self):
        fournisseur = Tiers.objects.create(
            code="FOUR-002", raison_sociale="Fournisseur B", type_tiers=Tiers.TypeTiers.FOURNISSEUR
        )
        article = Article.objects.create(
            reference="TOLE-ACH-02", nature=Article.Nature.MATIERE_PREMIERE, stock_mini=10
        )
        alerte = AlerteStock.objects.create(article=article, date_declenchement=datetime.date(2026, 1, 1))
        commande = CommandeFournisseur.objects.create(
            numero="CF-002", fournisseur=fournisseur, date_commande=datetime.date(2026, 1, 5)
        )

        LigneCommandeFournisseur.objects.create(
            commande_fournisseur=commande,
            article=article,
            alerte_stock_origine=alerte,
            quantite_commandee=50,
            prix_unitaire_achat=1.5,
        )

        alerte.refresh_from_db()
        self.assertEqual(alerte.statut, AlerteStock.Statut.TRAITEE)
        self.assertIsNotNone(alerte.date_traitement)
