import datetime
from unittest.mock import patch

from django.test import TestCase

from .models import RegleCodification
from .services import generer_code


class GenererCodeTests(TestCase):
    """Les 10 règles par défaut sont créées par la migration de données
    (0002_seed_regles_par_defaut) : on repart de leur état pour chaque test
    plutôt que de créer de nouvelles lignes (entite est la clé primaire)."""

    def test_aucune_regle_configuree_retourne_none(self):
        self.assertIsNone(generer_code("inexistant"))

    def test_premiere_generation(self):
        RegleCodification.objects.filter(pk=RegleCodification.Entite.DEVIS).update(
            prefixe="DEV-", nombre_chiffres=5, compteur_actuel=0
        )
        self.assertEqual(generer_code(RegleCodification.Entite.DEVIS), "DEV-00001")

    def test_incremente_a_chaque_appel(self):
        RegleCodification.objects.filter(pk=RegleCodification.Entite.DEVIS).update(
            prefixe="DEV-", nombre_chiffres=3, compteur_actuel=0
        )
        self.assertEqual(generer_code(RegleCodification.Entite.DEVIS), "DEV-001")
        self.assertEqual(generer_code(RegleCodification.Entite.DEVIS), "DEV-002")
        self.assertEqual(generer_code(RegleCodification.Entite.DEVIS), "DEV-003")

    def test_nombre_de_chiffres_parametrable(self):
        RegleCodification.objects.filter(pk=RegleCodification.Entite.TIERS).update(
            prefixe="CLI-", nombre_chiffres=2, compteur_actuel=0
        )
        self.assertEqual(generer_code(RegleCodification.Entite.TIERS), "CLI-01")

    def test_prefixe_vide_autorise(self):
        RegleCodification.objects.filter(pk=RegleCodification.Entite.EMPLACEMENT).update(
            prefixe="", nombre_chiffres=4, compteur_actuel=0
        )
        self.assertEqual(generer_code(RegleCodification.Entite.EMPLACEMENT), "0001")

    def test_reinitialisation_annuelle_insere_lannee(self):
        RegleCodification.objects.filter(pk=RegleCodification.Entite.FACTURE).update(
            prefixe="FAC-",
            nombre_chiffres=4,
            reinitialisation=RegleCodification.Reinitialisation.ANNUELLE,
            compteur_actuel=0,
            annee_compteur=None,
        )
        annee = datetime.date.today().year
        self.assertEqual(generer_code(RegleCodification.Entite.FACTURE), f"FAC-{annee}-0001")
        self.assertEqual(generer_code(RegleCodification.Entite.FACTURE), f"FAC-{annee}-0002")

    def test_reinitialisation_annuelle_repart_a_un_lannee_suivante(self):
        RegleCodification.objects.filter(pk=RegleCodification.Entite.FACTURE).update(
            prefixe="FAC-",
            nombre_chiffres=4,
            reinitialisation=RegleCodification.Reinitialisation.ANNUELLE,
            compteur_actuel=0,
            annee_compteur=None,
        )
        generer_code(RegleCodification.Entite.FACTURE)
        generer_code(RegleCodification.Entite.FACTURE)
        regle = RegleCodification.objects.get(pk=RegleCodification.Entite.FACTURE)
        self.assertEqual(regle.compteur_actuel, 2)

        annee_suivante = datetime.date.today().year + 1
        with patch("codification.services.datetime") as mock_datetime:
            mock_datetime.date.today.return_value = datetime.date(annee_suivante, 1, 15)
            code = generer_code(RegleCodification.Entite.FACTURE)
        self.assertEqual(code, f"FAC-{annee_suivante}-0001")

    def test_reinitialisation_jamais_compteur_continu_entre_annees(self):
        RegleCodification.objects.filter(pk=RegleCodification.Entite.COMMANDE).update(
            prefixe="CDE-",
            nombre_chiffres=3,
            reinitialisation=RegleCodification.Reinitialisation.JAMAIS,
            compteur_actuel=0,
        )
        generer_code(RegleCodification.Entite.COMMANDE)
        with patch("codification.services.datetime") as mock_datetime:
            mock_datetime.date.today.return_value = datetime.date(2099, 1, 1)
            code = generer_code(RegleCodification.Entite.COMMANDE)
        self.assertEqual(code, "CDE-002")
