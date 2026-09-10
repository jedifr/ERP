from django.contrib.auth import get_user_model
from django.test import TestCase

from .models import CompteComptable, JournalComptable
from .pcg import importer_pcg


class CompteComptableTests(TestCase):
    def test_classe_deduite_du_code(self):
        compte = CompteComptable.objects.create(code="6061", libelle="Fournitures non stockables")
        self.assertEqual(compte.classe, 6)

    def test_systeme_par_defaut(self):
        compte = CompteComptable.objects.create(code="512", libelle="Banques")
        self.assertEqual(compte.systeme, CompteComptable.Systeme.BASE)


class ImporterPcgTests(TestCase):
    def test_import_cree_les_comptes_officiels(self):
        crees, maj = importer_pcg()
        self.assertGreater(crees, 800)
        self.assertEqual(maj, 0)

        compte = CompteComptable.objects.get(code="706")
        self.assertEqual(compte.libelle, "Prestations de services")
        self.assertEqual(compte.classe, 7)
        self.assertEqual(compte.compte_parent_id, "70")

        fournisseurs = CompteComptable.objects.get(code="401")
        self.assertEqual(fournisseurs.classe, 4)

    def test_import_idempotent_ne_duplique_pas(self):
        importer_pcg()
        total_apres_premier_import = CompteComptable.objects.count()

        crees, maj = importer_pcg()
        self.assertEqual(crees, 0)
        self.assertEqual(maj, total_apres_premier_import)
        self.assertEqual(CompteComptable.objects.count(), total_apres_premier_import)

    def test_import_met_a_jour_un_libelle_modifie_a_la_main(self):
        importer_pcg()
        compte = CompteComptable.objects.get(code="706")
        compte.libelle = "Libellé modifié à la main"
        compte.save()

        importer_pcg()
        compte.refresh_from_db()
        self.assertEqual(compte.libelle, "Prestations de services")


class JournalComptableTests(TestCase):
    def test_journaux_par_defaut_charges_par_la_migration(self):
        codes = set(JournalComptable.objects.values_list("code", flat=True))
        self.assertEqual(codes, {"AC", "VT", "BQ1", "CA", "ER", "OD", "AN"})

    def test_ajout_dun_nouveau_journal(self):
        JournalComptable.objects.create(code="BQ2", libelle="BANQUE POPULAIRE", nature=JournalComptable.Nature.BANQUE)
        self.assertTrue(JournalComptable.objects.filter(code="BQ2").exists())


class CompteComptableAdminTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser("compta-admin", "c@example.com", "pass1234")
        self.client.force_login(self.user)

    def test_action_importer_pcg_depuis_ladmin(self):
        self.assertEqual(CompteComptable.objects.count(), 0)

        response = self.client.get(
            "/admin/comptabilite/comptecomptable/action_importer_pcg/", follow=True
        )
        self.assertEqual(response.status_code, 200)
        self.assertGreater(CompteComptable.objects.count(), 800)
