import datetime

from django.test import TestCase

from chiffrage.models import Commande, Devis
from commercial.models import Adresse, ConditionPaiement, Tiers

from .models import Facture


class FactureDateEcheanceTests(TestCase):
    def setUp(self):
        self.client_tiers = Tiers.objects.create(
            code="CLI-ECHEANCE", raison_sociale="Client Échéance", type_tiers=Tiers.TypeTiers.CLIENT
        )
        adresse = Adresse.objects.create(
            tiers=self.client_tiers, type_adresse=Adresse.TypeAdresse.FACTURATION,
            adresse="1 rue", code_postal="75000", ville="Paris",
        )
        devis = Devis.objects.create(
            numero="DEV-ECHEANCE", client=self.client_tiers, date_creation=datetime.date(2026, 1, 1)
        )
        self.commande = Commande.objects.create(
            numero="CDE-ECHEANCE", devis=devis, date_commande=datetime.date(2026, 1, 1),
            adresse_facturation=adresse, adresse_livraison=adresse,
        )

    def _facture(self, date_facturation=datetime.date(2026, 1, 1)):
        return Facture.objects.create(
            numero=f"FAC-ECHEANCE-{date_facturation.isoformat()}", commande=self.commande,
            date_facturation=date_facturation,
        )

    def test_echeance_calculee_depuis_les_conditions_du_client(self):
        self.client_tiers.conditions_paiement = ConditionPaiement.objects.create(
            libelle="30 jours net (échéance)", nombre_jours=30
        )
        self.client_tiers.save()
        facture = self._facture()
        self.assertEqual(facture.date_echeance, datetime.date(2026, 1, 31))

    def test_echeance_none_sans_condition_de_paiement(self):
        facture = self._facture()
        self.assertIsNone(facture.date_echeance)

    def test_echeance_none_si_condition_sans_delai_chiffre(self):
        self.client_tiers.conditions_paiement = ConditionPaiement.objects.create(
            libelle="À réception (échéance)"
        )
        self.client_tiers.save()
        facture = self._facture()
        self.assertIsNone(facture.date_echeance)
