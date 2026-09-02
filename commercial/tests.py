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


class TiersAdminCodificationTests(TestCase):
    """Le formulaire d'ajout de Tiers doit être pré-rempli avec le code généré
    par la règle de codification "tiers" (voir l'app codification)."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        self.user = User.objects.create_superuser("code-admin", "c@example.com", "pass1234")
        self.client.force_login(self.user)

    def test_formulaire_ajout_pre_rempli_avec_le_code_genere(self):
        response = self.client.get("/admin/commercial/tiers/add/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "TIERS-00001")

    def test_visites_successives_incrementent_le_compteur(self):
        self.client.get("/admin/commercial/tiers/add/")
        response = self.client.get("/admin/commercial/tiers/add/")
        self.assertContains(response, "TIERS-00002")
