import datetime

from django.core.exceptions import ValidationError
from django.test import TestCase

from chiffrage.models import Commande, Devis, OperationOF, OrdreFabrication
from commercial.models import Adresse, Tiers
from technique.models import Article, PosteTravail

from .models import EnvoiSousTraitance, RetourSousTraitance


class SousTraitanceTests(TestCase):
    def setUp(self):
        client_tiers = Tiers.objects.create(
            code="CLI-ST", raison_sociale="Client ST", type_tiers=Tiers.TypeTiers.CLIENT
        )
        adresse = Adresse.objects.create(
            tiers=client_tiers,
            type_adresse=Adresse.TypeAdresse.FACTURATION,
            adresse="1 rue",
            code_postal="75000",
            ville="Paris",
        )
        devis = Devis.objects.create(
            numero="DEV-ST", client=client_tiers, date_creation=datetime.date(2026, 1, 1), statut="valide"
        )
        commande = Commande.objects.create(
            numero="CDE-ST",
            devis=devis,
            date_commande=datetime.date(2026, 1, 1),
            adresse_facturation=adresse,
            adresse_livraison=adresse,
        )
        article = Article.objects.create(reference="PIECE-ST", nature=Article.Nature.FABRIQUE)
        ordre_fabrication = OrdreFabrication.objects.create(
            numero="OF-ST", commande=commande, article=article, quantite=10, date_lancement=datetime.date(2026, 1, 1)
        )
        poste = PosteTravail.objects.create(
            nom="Sous-Traitance ST", mode_calcul=PosteTravail.ModeCalcul.FORFAITAIRE
        )
        self.operation = OperationOF.objects.create(ordre_fabrication=ordre_fabrication, poste=poste, ordre=1)

        self.sous_traitant = Tiers.objects.create(
            code="ST-001", raison_sociale="Sous-traitant Test", type_tiers=Tiers.TypeTiers.FOURNISSEUR
        )
        self.envoi = EnvoiSousTraitance.objects.create(
            numero="ENV-001",
            operation_of=self.operation,
            sous_traitant=self.sous_traitant,
            date_envoi=datetime.date(2026, 1, 2),
            quantite_envoyee=10,
        )

    def test_retour_complet_cloture_operation_et_envoi(self):
        RetourSousTraitance.objects.create(
            numero="RET-001", envoi=self.envoi, date_retour=datetime.date(2026, 1, 5), quantite_retournee=10
        )
        self.operation.refresh_from_db()
        self.envoi.refresh_from_db()
        self.assertEqual(self.operation.quantite_bonne, 10)
        self.assertEqual(self.operation.statut, "terminee")
        self.assertEqual(self.envoi.statut, "retourne")

    def test_retour_partiel_ne_cloture_pas(self):
        RetourSousTraitance.objects.create(
            numero="RET-002", envoi=self.envoi, date_retour=datetime.date(2026, 1, 5), quantite_retournee=4
        )
        self.operation.refresh_from_db()
        self.envoi.refresh_from_db()
        self.assertEqual(self.operation.quantite_bonne, 4)
        self.assertNotEqual(self.operation.statut, "terminee")
        self.assertNotEqual(self.envoi.statut, "retourne")

    def test_retours_partiels_cumules_cloturent(self):
        RetourSousTraitance.objects.create(
            numero="RET-003", envoi=self.envoi, date_retour=datetime.date(2026, 1, 5), quantite_retournee=6
        )
        RetourSousTraitance.objects.create(
            numero="RET-004", envoi=self.envoi, date_retour=datetime.date(2026, 1, 6), quantite_retournee=4
        )
        self.operation.refresh_from_db()
        self.assertEqual(self.operation.quantite_bonne, 10)
        self.assertEqual(self.operation.statut, "terminee")

    def test_non_conforme_alimente_quantite_rebut(self):
        RetourSousTraitance.objects.create(
            numero="RET-005",
            envoi=self.envoi,
            date_retour=datetime.date(2026, 1, 5),
            quantite_retournee=3,
            conforme=False,
        )
        self.operation.refresh_from_db()
        self.assertEqual(self.operation.quantite_rebut, 3)
        self.assertIsNone(self.operation.quantite_bonne)

    def test_depassement_quantite_envoyee_refuse(self):
        retour = RetourSousTraitance(
            numero="RET-006", envoi=self.envoi, date_retour=datetime.date(2026, 1, 5), quantite_retournee=15
        )
        with self.assertRaises(ValidationError):
            retour.full_clean()
