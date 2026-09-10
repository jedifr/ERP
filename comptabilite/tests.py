import datetime

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from chiffrage.models import Commande, CommandeLigne, Devis
from commercial.models import Adresse, TauxTVA, Tiers
from facturation.models import Facture
from technique.models import Article

from .generation import GenerationEcritureError, generer_ecriture_facture
from .models import (
    ArticleCompteAchat,
    ArticleCompteVente,
    CodeAnalytique,
    CompteComptable,
    EcritureComptable,
    JournalComptable,
    LigneEcriture,
    ParametresComptables,
    PosteGestion,
)
from .pcg import importer_pcg
from .postes_gestion import importer_postes_gestion


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


class LigneEcritureTests(TestCase):
    def setUp(self):
        journal = JournalComptable.objects.get(code="OD")
        self.ecriture = EcritureComptable.objects.create(
            journal=journal, date_ecriture=datetime.date(2026, 1, 1), piece="P1", libelle="Test"
        )
        self.compte = CompteComptable.objects.create(code="512", libelle="Banques")

    def test_debit_et_credit_a_la_fois_refuse(self):
        ligne = LigneEcriture(ecriture=self.ecriture, compte=self.compte, debit=100, credit=100)
        with self.assertRaises(ValidationError):
            ligne.full_clean()

    def test_ni_debit_ni_credit_refuse(self):
        ligne = LigneEcriture(ecriture=self.ecriture, compte=self.compte)
        with self.assertRaises(ValidationError):
            ligne.full_clean()

    def test_montant_negatif_refuse(self):
        ligne = LigneEcriture(ecriture=self.ecriture, compte=self.compte, debit=-50)
        with self.assertRaises(ValidationError):
            ligne.full_clean()

    def test_ligne_valide(self):
        ligne = LigneEcriture(ecriture=self.ecriture, compte=self.compte, debit=100)
        ligne.full_clean()  # ne doit pas lever d'exception


class EcritureComptableEquilibreTests(TestCase):
    def test_ecriture_equilibree(self):
        journal = JournalComptable.objects.get(code="OD")
        compte1 = CompteComptable.objects.create(code="512", libelle="Banques")
        compte2 = CompteComptable.objects.create(code="411", libelle="Clients")
        ecriture = EcritureComptable.objects.create(
            journal=journal, date_ecriture=datetime.date(2026, 1, 1), piece="P1", libelle="Test"
        )
        LigneEcriture.objects.create(ecriture=ecriture, compte=compte1, debit=100)
        LigneEcriture.objects.create(ecriture=ecriture, compte=compte2, credit=100)
        self.assertTrue(ecriture.est_equilibree)

    def test_ecriture_desequilibree(self):
        journal = JournalComptable.objects.get(code="OD")
        compte1 = CompteComptable.objects.create(code="512", libelle="Banques")
        compte2 = CompteComptable.objects.create(code="411", libelle="Clients")
        ecriture = EcritureComptable.objects.create(
            journal=journal, date_ecriture=datetime.date(2026, 1, 1), piece="P1", libelle="Test"
        )
        LigneEcriture.objects.create(ecriture=ecriture, compte=compte1, debit=100)
        LigneEcriture.objects.create(ecriture=ecriture, compte=compte2, credit=90)
        self.assertFalse(ecriture.est_equilibree)


class ParametresComptablesTests(TestCase):
    def test_charger_cree_la_ligne_unique(self):
        self.assertEqual(ParametresComptables.objects.count(), 1)  # migration 0004
        parametres = ParametresComptables.charger()
        self.assertEqual(ParametresComptables.objects.count(), 1)
        self.assertEqual(parametres.journal_ventes.code, "VT")

    def test_charger_retombe_sur_les_codes_pcg_usuels_si_importes(self):
        importer_pcg()
        parametres = ParametresComptables.charger()
        self.assertEqual(parametres.compte_client_defaut.code, "411")
        self.assertEqual(parametres.compte_vente_defaut.code, "706")
        self.assertEqual(parametres.compte_tva_collectee_defaut.code, "44571")
        # Le repli n'est jamais écrit en base tant que l'utilisateur n'a rien choisi.
        self.assertIsNone(ParametresComptables.objects.get(pk=1).compte_client_defaut_id)

    def test_charger_respecte_un_compte_choisi_manuellement(self):
        importer_pcg()
        compte_special = CompteComptable.objects.get(code="4111")
        parametres = ParametresComptables.charger()
        parametres.compte_client_defaut = compte_special
        parametres.save()

        self.assertEqual(ParametresComptables.charger().compte_client_defaut.code, "4111")


class ArticleComptesTests(TestCase):
    def setUp(self):
        importer_pcg()
        self.article = Article.objects.create(reference="ART-CPT-TEST", nature=Article.Nature.FABRIQUE)
        self.compte_701 = CompteComptable.objects.get(code="701")
        self.compte_607 = CompteComptable.objects.get(code="607")

    def test_un_seul_compte_de_vente_par_article(self):
        ArticleCompteVente.objects.create(article=self.article, compte_vente=self.compte_701)
        with self.assertRaises(ValidationError):
            ArticleCompteVente(article=self.article, compte_vente=self.compte_701).full_clean()

    def test_article_sans_override_utilise_related_name_vide(self):
        self.assertIsNone(getattr(self.article, "compte_vente_override", None))
        self.assertIsNone(getattr(self.article, "compte_achat_override", None))

    def test_compte_achat_et_vente_independants(self):
        ArticleCompteVente.objects.create(article=self.article, compte_vente=self.compte_701)
        ArticleCompteAchat.objects.create(article=self.article, compte_achat=self.compte_607)
        self.article.refresh_from_db()
        self.assertEqual(self.article.compte_vente_override.compte_vente, self.compte_701)
        self.assertEqual(self.article.compte_achat_override.compte_achat, self.compte_607)

    def test_ni_poste_ni_compte_refuse(self):
        with self.assertRaises(ValidationError):
            ArticleCompteVente(article=self.article).full_clean()
        with self.assertRaises(ValidationError):
            ArticleCompteAchat(article=self.article).full_clean()


class PosteGestionTests(TestCase):
    def setUp(self):
        importer_pcg()
        self.compte_france = CompteComptable.objects.get(code="701")
        self.compte_intra_ue = CompteComptable.objects.get(code="706")
        self.poste = PosteGestion.objects.create(
            code="PF", libelle="Pièce fabriquée",
            compte_vente_france=self.compte_france, compte_vente_intra_ue=self.compte_intra_ue,
        )

    def test_compte_vente_pour_regime_configure(self):
        self.assertEqual(self.poste.compte_vente_pour_regime(Tiers.RegimeFiscal.FRANCE), self.compte_france)
        self.assertEqual(self.poste.compte_vente_pour_regime(Tiers.RegimeFiscal.INTRA_UE), self.compte_intra_ue)

    def test_compte_vente_pour_regime_non_configure_renvoie_none(self):
        self.assertIsNone(self.poste.compte_vente_pour_regime(Tiers.RegimeFiscal.HORS_UE))

    def test_compte_achat_pour_regime_non_configure_renvoie_none(self):
        self.assertIsNone(self.poste.compte_achat_pour_regime(Tiers.RegimeFiscal.FRANCE))


class ImporterPostesGestionTests(TestCase):
    def test_import_cree_les_postes_et_les_comptes_manquants(self):
        postes_crees, postes_maj, comptes_crees = importer_postes_gestion()
        self.assertEqual(postes_crees, 145)
        self.assertEqual(postes_maj, 0)
        self.assertGreater(comptes_crees, 100)

        mp = PosteGestion.objects.get(code="MP")
        self.assertEqual(mp.compte_achat_france.code, "601100")
        self.assertEqual(mp.compte_achat_france.systeme, CompteComptable.Systeme.DEVELOPPE)
        self.assertEqual(mp.compte_vente_intra_ue.code, "701101")

    def test_import_idempotent(self):
        importer_postes_gestion()
        total_postes = PosteGestion.objects.count()
        total_comptes = CompteComptable.objects.count()

        postes_crees, postes_maj, comptes_crees = importer_postes_gestion()
        self.assertEqual(postes_crees, 0)
        self.assertEqual(postes_maj, total_postes)
        self.assertEqual(comptes_crees, 0)
        self.assertEqual(CompteComptable.objects.count(), total_comptes)


class CodeAnalytiqueTests(TestCase):
    def test_creation_et_str(self):
        code = CodeAnalytique.objects.create(code="CHANTIER-42", libelle="Chantier 42")
        self.assertEqual(str(code), "CHANTIER-42 — Chantier 42")
        self.assertTrue(code.actif)


class GenererEcritureFactureTests(TestCase):
    def setUp(self):
        importer_pcg()
        client = Tiers.objects.create(
            code="CLI-COMPTA-TEST", raison_sociale="Client Compta Test", type_tiers=Tiers.TypeTiers.CLIENT
        )
        adresse = Adresse.objects.create(
            tiers=client, type_adresse=Adresse.TypeAdresse.FACTURATION,
            adresse="1 rue", code_postal="75000", ville="Paris",
        )
        self.article = Article.objects.create(reference="ART-COMPTA-TEST", nature=Article.Nature.MATIERE_PREMIERE)
        self.taux20 = TauxTVA.objects.create(nom="Taux normal compta test", taux=20)
        self.taux10 = TauxTVA.objects.create(nom="Taux intermédiaire compta test", taux=10)
        devis = Devis.objects.create(numero="DEV-COMPTA-TEST", client=client, date_creation=datetime.date(2026, 1, 1))
        self.commande = Commande.objects.create(
            numero="CDE-COMPTA-TEST", devis=devis, date_commande=datetime.date(2026, 1, 1),
            adresse_facturation=adresse, adresse_livraison=adresse,
        )
        self.facture = Facture.objects.create(
            numero="FAC-COMPTA-TEST", commande=self.commande, date_facturation=datetime.date(2026, 2, 1)
        )

    def test_generation_repartit_par_taux_de_tva_et_equilibre(self):
        CommandeLigne.objects.create(
            commande=self.commande, article=self.article, quantite_commandee=10,
            prix_vente_unitaire=100, taux_tva=self.taux20,
        )
        CommandeLigne.objects.create(
            commande=self.commande, article=self.article, quantite_commandee=5,
            prix_vente_unitaire=40, taux_tva=self.taux10,
        )
        # HT : 1000 (20%) + 200 (10%) = 1200 ; TVA : 200 + 20 = 220 ; TTC : 1420
        ecriture, creee = generer_ecriture_facture(self.facture)
        self.assertTrue(creee)
        self.assertTrue(ecriture.est_equilibree)
        self.assertAlmostEqual(ecriture.total_debit, 1420)

        lignes_client = ecriture.lignes.filter(compte__code="411")
        self.assertEqual(lignes_client.count(), 1)
        self.assertAlmostEqual(lignes_client.first().debit, 1420)

        self.assertAlmostEqual(
            sum(l.credit for l in ecriture.lignes.filter(compte__code="706")), 1200
        )
        self.assertAlmostEqual(
            sum(l.credit for l in ecriture.lignes.filter(compte__code="44571")), 220
        )

    def test_generation_utilise_le_compte_de_vente_specifique_dun_article(self):
        autre_article = Article.objects.create(reference="ART-COMPTA-TEST-2", nature=Article.Nature.FABRIQUE)
        compte_701 = CompteComptable.objects.get(code="701")
        code_atelier = CodeAnalytique.objects.create(code="ATELIER1", libelle="Atelier 1")
        ArticleCompteVente.objects.create(
            article=autre_article, compte_vente=compte_701, code_analytique=code_atelier
        )

        CommandeLigne.objects.create(
            commande=self.commande, article=self.article, quantite_commandee=1,
            prix_vente_unitaire=100, taux_tva=self.taux20,
        )
        CommandeLigne.objects.create(
            commande=self.commande, article=autre_article, quantite_commandee=1,
            prix_vente_unitaire=200, taux_tva=self.taux20,
        )

        ecriture, creee = generer_ecriture_facture(self.facture)
        self.assertTrue(ecriture.est_equilibree)

        ligne_701 = ecriture.lignes.get(compte__code="701")
        self.assertAlmostEqual(ligne_701.credit, 200)
        self.assertEqual(ligne_701.code_analytique, code_atelier)

        ligne_706 = ecriture.lignes.get(compte__code="706")
        self.assertAlmostEqual(ligne_706.credit, 100)
        self.assertIsNone(ligne_706.code_analytique)

        # Le code analytique ne se propage jamais aux lignes Clients/TVA.
        for ligne in ecriture.lignes.filter(compte__code__in=["411", "44571"]):
            self.assertIsNone(ligne.code_analytique)

    def test_generation_resout_le_compte_selon_le_regime_fiscal_du_client(self):
        compte_france = CompteComptable.objects.get(code="701")
        compte_intra_ue = CompteComptable.objects.get(code="706")
        poste = PosteGestion.objects.create(
            code="PF-TEST", libelle="Pièce fabriquée test",
            compte_vente_france=compte_france, compte_vente_intra_ue=compte_intra_ue,
        )
        ArticleCompteVente.objects.create(article=self.article, poste_gestion=poste)

        client = self.commande.devis.client
        client.regime_fiscal = Tiers.RegimeFiscal.INTRA_UE
        client.save()

        CommandeLigne.objects.create(
            commande=self.commande, article=self.article, quantite_commandee=1,
            prix_vente_unitaire=100, taux_tva=self.taux20,
        )
        ecriture, creee = generer_ecriture_facture(self.facture)
        self.assertTrue(ecriture.est_equilibree)
        self.assertTrue(ecriture.lignes.filter(compte__code="706").exists())
        self.assertFalse(ecriture.lignes.filter(compte__code="701").exists())

    def test_generation_echoue_si_poste_gestion_sans_compte_pour_le_regime(self):
        poste = PosteGestion.objects.create(code="PF-INCOMPLET", libelle="Sans compte hors UE")
        ArticleCompteVente.objects.create(article=self.article, poste_gestion=poste)

        client = self.commande.devis.client
        client.regime_fiscal = Tiers.RegimeFiscal.HORS_UE
        client.save()

        CommandeLigne.objects.create(
            commande=self.commande, article=self.article, quantite_commandee=1,
            prix_vente_unitaire=100, taux_tva=self.taux20,
        )
        with self.assertRaises(GenerationEcritureError):
            generer_ecriture_facture(self.facture)

    def test_generation_idempotente(self):
        CommandeLigne.objects.create(
            commande=self.commande, article=self.article, quantite_commandee=1,
            prix_vente_unitaire=100, taux_tva=self.taux20,
        )
        ecriture1, creee1 = generer_ecriture_facture(self.facture)
        nb_lignes = EcritureComptable.objects.count()

        ecriture2, creee2 = generer_ecriture_facture(self.facture)
        self.assertTrue(creee1)
        self.assertFalse(creee2)
        self.assertEqual(ecriture1.pk, ecriture2.pk)
        self.assertEqual(EcritureComptable.objects.count(), nb_lignes)

    def test_generation_repli_sur_montants_globaux_si_pas_de_lignes_chiffrees(self):
        self.facture.montant_ht = 500
        self.facture.montant_ttc = 600
        self.facture.save()

        ecriture, creee = generer_ecriture_facture(self.facture)
        self.assertTrue(creee)
        self.assertAlmostEqual(ecriture.total_debit, 600)
        self.assertAlmostEqual(ecriture.total_credit, 600)

    def test_generation_echoue_sans_lignes_ni_montants(self):
        with self.assertRaises(GenerationEcritureError):
            generer_ecriture_facture(self.facture)

    def test_generation_echoue_si_pcg_non_importe(self):
        CompteComptable.objects.all().delete()
        ParametresComptables.objects.all().delete()
        CommandeLigne.objects.create(
            commande=self.commande, article=self.article, quantite_commandee=1,
            prix_vente_unitaire=100, taux_tva=self.taux20,
        )
        with self.assertRaises(GenerationEcritureError):
            generer_ecriture_facture(self.facture)


class EcritureComptableAdminFormsetTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser("ecriture-admin", "e@example.com", "pass1234")
        self.client.force_login(self.user)
        self.journal = JournalComptable.objects.get(code="OD")
        self.compte1 = CompteComptable.objects.create(code="512", libelle="Banques")
        self.compte2 = CompteComptable.objects.create(code="411", libelle="Clients")

    def _payload(self, debit2, credit2):
        return {
            "journal": self.journal.pk,
            "date_ecriture": "2026-01-01",
            "piece": "P1",
            "libelle": "Test",
            "lignes-TOTAL_FORMS": "2",
            "lignes-INITIAL_FORMS": "0",
            "lignes-MIN_NUM_FORMS": "0",
            "lignes-MAX_NUM_FORMS": "1000",
            "lignes-0-compte": self.compte1.pk,
            "lignes-0-libelle": "",
            "lignes-0-debit": "100",
            "lignes-0-credit": "0",
            "lignes-1-compte": self.compte2.pk,
            "lignes-1-libelle": "",
            "lignes-1-debit": debit2,
            "lignes-1-credit": credit2,
            "_save": "Enregistrer",
        }

    def test_ecriture_desequilibree_refusee_par_ladmin(self):
        response = self.client.post("/admin/comptabilite/ecriturecomptable/add/", data=self._payload(0, "90"))
        self.assertEqual(response.status_code, 200)  # ré-affiche le formulaire, pas de redirect
        self.assertFalse(EcritureComptable.objects.exists())

    def test_ecriture_equilibree_acceptee_par_ladmin(self):
        response = self.client.post("/admin/comptabilite/ecriturecomptable/add/", data=self._payload(0, "100"))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(EcritureComptable.objects.get().est_equilibree)


class FactureAdminGenererEcritureTests(TestCase):
    def setUp(self):
        importer_pcg()
        User = get_user_model()
        self.user = User.objects.create_superuser("facture-admin", "f@example.com", "pass1234")
        self.client.force_login(self.user)

        client_tiers = Tiers.objects.create(
            code="CLI-COMPTA-ADM", raison_sociale="Client Compta Admin", type_tiers=Tiers.TypeTiers.CLIENT
        )
        adresse = Adresse.objects.create(
            tiers=client_tiers, type_adresse=Adresse.TypeAdresse.FACTURATION,
            adresse="1 rue", code_postal="75000", ville="Paris",
        )
        article = Article.objects.create(reference="ART-COMPTA-ADM", nature=Article.Nature.MATIERE_PREMIERE)
        taux20 = TauxTVA.objects.create(nom="Taux normal compta admin", taux=20)
        devis = Devis.objects.create(numero="DEV-COMPTA-ADM", client=client_tiers, date_creation=datetime.date(2026, 1, 1))
        commande = Commande.objects.create(
            numero="CDE-COMPTA-ADM", devis=devis, date_commande=datetime.date(2026, 1, 1),
            adresse_facturation=adresse, adresse_livraison=adresse,
        )
        CommandeLigne.objects.create(
            commande=commande, article=article, quantite_commandee=1, prix_vente_unitaire=100, taux_tva=taux20
        )
        self.facture = Facture.objects.create(
            numero="FAC-COMPTA-ADM", commande=commande, date_facturation=datetime.date(2026, 2, 1)
        )

    def test_action_genere_lecriture(self):
        response = self.client.post(
            "/admin/facturation/facture/",
            data={"action": "action_generer_ecriture", "_selected_action": [self.facture.pk]},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(EcritureComptable.objects.filter(facture=self.facture).exists())
