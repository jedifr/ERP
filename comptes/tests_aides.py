from django.contrib.auth import get_user_model
from django.test import TestCase

from .aides import AIDES
from .parametrage import CARTES


class AidesParametrageTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("aide", "a@example.com", "pass-mot-de-passe-71"))

    def test_chaque_section_du_parametrage_a_son_mode_d_emploi(self):
        manquantes = []
        for _cle, _titre, _icone, _description, entrees, _etat in CARTES:
            for entree in entrees:
                app, modele = entree[1], entree[2]
                if modele and f"{app}.{modele}" not in AIDES:
                    manquantes.append(f"{app}.{modele}")
        self.assertEqual(manquantes, [])
        for cle, aide in AIDES.items():
            self.assertTrue(aide["resume"] and aide["etapes"], cle)

    def test_encadre_affiche_sur_les_listes(self):
        for url, attendu in (
            ("/admin/technique/reglecreationtole/", "Base matières rapide"),
            ("/admin/technique/matiere/", "Base matières rapide"),
            ("/admin/decoupe/formattole/", "Mode d'emploi"),
            ("/admin/comptabilite/journalcomptable/", "Mode d'emploi"),
        ):
            page = self.client.get(url)
            self.assertContains(page, "aide-liste", msg_prefix=url)
            self.assertContains(page, attendu, msg_prefix=url)

    def test_pas_d_encadre_sur_une_liste_sans_aide(self):
        self.assertNotContains(self.client.get("/admin/chiffrage/devis/"), "aide-liste")

    def test_la_regle_de_tole_explique_la_creation_en_lot(self):
        page = self.client.get("/admin/technique/reglecreationtole/")
        self.assertContains(page, "Créer plusieurs tôles d")
        self.assertContains(page, "largeur x longueur x épaisseur")
        self.assertContains(page, "Créer plusieurs tôles (Base matières rapide)")  # raccourci dans la barre d'actions de la liste
