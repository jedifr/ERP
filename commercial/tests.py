from django.core.exceptions import ValidationError
from django.test import TestCase

from .models import Adresse, Tiers


class AdresseTests(TestCase):
    def setUp(self):
        self.client_tiers = Tiers.objects.create(
            code="CLI-001", raison_sociale="Client Test", type_tiers=Tiers.TypeTiers.CLIENT
        )

    def test_une_seule_adresse_principale_par_type(self):
        Adresse.objects.create(
            tiers=self.client_tiers,
            type_adresse=Adresse.TypeAdresse.FACTURATION,
            adresse="1 rue A",
            code_postal="75000",
            ville="Paris",
            est_principale=True,
        )
        deuxieme = Adresse(
            tiers=self.client_tiers,
            type_adresse=Adresse.TypeAdresse.FACTURATION,
            adresse="2 rue B",
            code_postal="75000",
            ville="Paris",
            est_principale=True,
        )
        with self.assertRaises(ValidationError):
            deuxieme.full_clean()

    def test_principale_facturation_et_livraison_coexistent(self):
        Adresse.objects.create(
            tiers=self.client_tiers,
            type_adresse=Adresse.TypeAdresse.FACTURATION,
            adresse="1 rue A",
            code_postal="75000",
            ville="Paris",
            est_principale=True,
        )
        livraison = Adresse(
            tiers=self.client_tiers,
            type_adresse=Adresse.TypeAdresse.LIVRAISON,
            adresse="1 rue A",
            code_postal="75000",
            ville="Paris",
            est_principale=True,
        )
        livraison.full_clean()  # ne doit pas lever d'exception
