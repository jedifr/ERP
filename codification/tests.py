import datetime
from unittest.mock import patch

from django.test import TestCase

from .models import RegleCodification
from .services import enregistrer_code_utilise, generer_code


class GenererCodeTests(TestCase):
    """Les 10 règles par défaut sont créées par la migration de données
    (0002_seed_regles_par_defaut) : on repart de leur état pour chaque test
    plutôt que de créer de nouvelles lignes (entite est la clé primaire).

    generer_code() n'est qu'un aperçu : elle ne modifie jamais compteur_actuel
    (voir EnregistrerCodeUtiliseTests pour ce qui fait réellement avancer le
    compteur)."""

    def test_aucune_regle_configuree_retourne_none(self):
        self.assertIsNone(generer_code("inexistant"))

    def test_premiere_generation(self):
        RegleCodification.objects.filter(pk=RegleCodification.Entite.DEVIS).update(
            prefixe="DEV-", nombre_chiffres=5, compteur_actuel=0
        )
        self.assertEqual(generer_code(RegleCodification.Entite.DEVIS), "DEV-00001")

    def test_appels_successifs_sans_enregistrement_renvoient_le_meme_code(self):
        RegleCodification.objects.filter(pk=RegleCodification.Entite.DEVIS).update(
            prefixe="DEV-", nombre_chiffres=3, compteur_actuel=0
        )
        self.assertEqual(generer_code(RegleCodification.Entite.DEVIS), "DEV-001")
        self.assertEqual(generer_code(RegleCodification.Entite.DEVIS), "DEV-001")
        self.assertEqual(generer_code(RegleCodification.Entite.DEVIS), "DEV-001")
        # Rien n'a été persisté par ces appels.
        regle = RegleCodification.objects.get(pk=RegleCodification.Entite.DEVIS)
        self.assertEqual(regle.compteur_actuel, 0)

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

    def test_reinitialisation_annuelle_repart_a_un_lannee_suivante(self):
        RegleCodification.objects.filter(pk=RegleCodification.Entite.FACTURE).update(
            prefixe="FAC-",
            nombre_chiffres=4,
            reinitialisation=RegleCodification.Reinitialisation.ANNUELLE,
            compteur_actuel=3,
            annee_compteur=datetime.date.today().year,
        )
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
            compteur_actuel=1,
        )
        with patch("codification.services.datetime") as mock_datetime:
            mock_datetime.date.today.return_value = datetime.date(2099, 1, 1)
            code = generer_code(RegleCodification.Entite.COMMANDE)
        self.assertEqual(code, "CDE-002")


class EnregistrerCodeUtiliseTests(TestCase):
    """Ce qui fait réellement avancer compteur_actuel — appelé après
    l'enregistrement réel d'un nouvel objet (CodificationInitialeMixin.
    save_model), jamais à la prévisualisation."""

    def test_code_conforme_fait_avancer_le_compteur(self):
        RegleCodification.objects.filter(pk=RegleCodification.Entite.DEVIS).update(
            prefixe="DEV-", nombre_chiffres=5, compteur_actuel=0
        )
        enregistrer_code_utilise(RegleCodification.Entite.DEVIS, "DEV-00001")
        regle = RegleCodification.objects.get(pk=RegleCodification.Entite.DEVIS)
        self.assertEqual(regle.compteur_actuel, 1)
        self.assertEqual(generer_code(RegleCodification.Entite.DEVIS), "DEV-00002")

    def test_formulaire_abandonne_ne_fait_pas_avancer_le_compteur(self):
        # C'est exactement le scénario signalé : consulter le formulaire
        # d'ajout (generer_code) sans jamais enregistrer ne doit laisser
        # aucune trace, contrairement à l'ancien comportement.
        RegleCodification.objects.filter(pk=RegleCodification.Entite.TIERS).update(
            prefixe="CLI-", nombre_chiffres=4, compteur_actuel=0
        )
        generer_code(RegleCodification.Entite.TIERS)
        generer_code(RegleCodification.Entite.TIERS)
        generer_code(RegleCodification.Entite.TIERS)
        self.assertEqual(generer_code(RegleCodification.Entite.TIERS), "CLI-0001")

    def test_code_different_du_format_ne_fait_rien(self):
        RegleCodification.objects.filter(pk=RegleCodification.Entite.DEVIS).update(
            prefixe="DEV-", nombre_chiffres=5, compteur_actuel=3
        )
        enregistrer_code_utilise(RegleCodification.Entite.DEVIS, "AUTRE-CODE-LIBRE")
        regle = RegleCodification.objects.get(pk=RegleCodification.Entite.DEVIS)
        self.assertEqual(regle.compteur_actuel, 3)

    def test_code_manuel_superieur_rattrape_le_compteur(self):
        # L'utilisateur a remplacé la suggestion par un numéro plus haut :
        # le compteur doit rattraper pour ne pas provoquer de collision au
        # prochain aperçu.
        RegleCodification.objects.filter(pk=RegleCodification.Entite.DEVIS).update(
            prefixe="DEV-", nombre_chiffres=5, compteur_actuel=3
        )
        enregistrer_code_utilise(RegleCodification.Entite.DEVIS, "DEV-00050")
        self.assertEqual(generer_code(RegleCodification.Entite.DEVIS), "DEV-00051")

    def test_code_manuel_inferieur_ne_fait_pas_reculer_le_compteur(self):
        RegleCodification.objects.filter(pk=RegleCodification.Entite.DEVIS).update(
            prefixe="DEV-", nombre_chiffres=5, compteur_actuel=10
        )
        enregistrer_code_utilise(RegleCodification.Entite.DEVIS, "DEV-00002")
        regle = RegleCodification.objects.get(pk=RegleCodification.Entite.DEVIS)
        self.assertEqual(regle.compteur_actuel, 10)

    def test_aucune_regle_configuree_ne_leve_pas(self):
        enregistrer_code_utilise("inexistant", "PEU-IMPORTE")  # ne doit pas lever

    def test_reinitialisation_annuelle_synchronise_annee_et_numero(self):
        annee = datetime.date.today().year
        RegleCodification.objects.filter(pk=RegleCodification.Entite.FACTURE).update(
            prefixe="FAC-",
            nombre_chiffres=4,
            reinitialisation=RegleCodification.Reinitialisation.ANNUELLE,
            compteur_actuel=0,
            annee_compteur=None,
        )
        enregistrer_code_utilise(RegleCodification.Entite.FACTURE, f"FAC-{annee}-0001")
        regle = RegleCodification.objects.get(pk=RegleCodification.Entite.FACTURE)
        self.assertEqual(regle.annee_compteur, annee)
        self.assertEqual(regle.compteur_actuel, 1)
        self.assertEqual(generer_code(RegleCodification.Entite.FACTURE), f"FAC-{annee}-0002")
