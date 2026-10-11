import datetime
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from chiffrage.models import Commande, CommandeLigne, Devis
from codification.models import RegleCodification
from commercial.models import Adresse, Pays, TauxTVA, Tiers
from comptabilite.models import EcritureComptable, JournalComptable
from facturation.models import Facture, FactureLigne
from technique.models import Article, Matiere


class PreparerProductionTests(TestCase):
    def setUp(self):
        pays = Pays.objects.first() or Pays.objects.create(nom="France")
        self.client_tiers = Tiers.objects.create(code="CLI-PP", raison_sociale="Client", type_tiers=Tiers.TypeTiers.CLIENT)
        adresse = Adresse.objects.create(tiers=self.client_tiers, est_facturation=True, est_livraison=True, adresse="1 rue", code_postal="75000", ville="Paris", pays=pays, est_principale=True)
        self.matiere = Matiere.objects.create(nom="S235-PP", densite=7.85)
        self.devis = Devis.objects.create(numero="DEV-PP-1", client=self.client_tiers, date_creation=datetime.date(2026, 1, 1))
        self.revision = Devis.objects.create(numero="DEV-PP-1-B", client=self.client_tiers, date_creation=datetime.date(2026, 1, 2), devis_origine=self.devis)
        self.commande = Commande.objects.create(
            numero="CDE-PP", devis=self.devis, client=self.client_tiers, date_commande=datetime.date(2026, 1, 3), adresse_facturation=adresse, adresse_livraison=adresse,
        )
        self.article = Article.objects.create(reference="PIECE-PP", nature=Article.Nature.FABRIQUE)
        self.tole = Article.objects.create(reference="TOLE-PP", nature=Article.Nature.MATIERE_PREMIERE, matiere=self.matiere, epaisseur=3)
        ligne = CommandeLigne.objects.create(commande=self.commande, article=self.article, quantite_commandee=2, prix_vente_unitaire=10, taux_tva=TauxTVA.objects.create(nom="N", taux=20))
        facture = Facture.objects.create(numero="FAC-PP", commande=self.commande, date_facturation=datetime.date(2026, 1, 4))
        FactureLigne.objects.create(facture=facture, commande_ligne=ligne, quantite=1)
        Facture.objects.create(numero="AV-PP", commande=self.commande, date_facturation=datetime.date(2026, 1, 5), type_document=Facture.TypeDocument.AVOIR, facture_origine=facture, motif="x")
        journal = JournalComptable.objects.first() or JournalComptable.objects.create(code="VT", libelle="Ventes", nature="ventes")
        EcritureComptable.objects.create(journal=journal, date_ecriture=datetime.date(2026, 1, 4), piece="FAC-PP", libelle="Facture", facture=facture)
        regle, _ = RegleCodification.objects.get_or_create(entite="devis")
        regle.compteur_actuel = 12
        regle.save()
        tiers_regle, _ = RegleCodification.objects.get_or_create(entite="tiers")
        tiers_regle.compteur_actuel = 7
        tiers_regle.save()

    def lancer(self, *args):
        sortie = StringIO()
        call_command("preparer_production", *args, stdout=sortie)
        return sortie.getvalue()

    def test_par_defaut_liste_sans_rien_supprimer(self):
        texte = self.lancer()
        self.assertIn("facturation.Facture", texte)
        self.assertIn("Rien n'a été modifié", texte)
        self.assertEqual((Devis.objects.count(), Facture.objects.count()), (2, 2))

    def test_refus_sans_sauvegarde_attestee(self):
        with self.assertRaisesMessage(CommandError, "sauvegarde"):
            self.lancer("--confirmer", "--oui")
        self.assertEqual(Devis.objects.count(), 2)

    def test_suppression_des_documents_et_remise_a_zero_des_compteurs(self):
        self.lancer("--confirmer", "--sauvegarde-faite", "--oui")
        for modele in (Devis, Commande, CommandeLigne, Facture, FactureLigne, EcritureComptable):
            self.assertEqual(modele.objects.count(), 0, modele)
        self.assertEqual(Devis.history.count(), 0)
        self.assertEqual(RegleCodification.objects.get(entite="devis").compteur_actuel, 0)
        self.assertEqual(RegleCodification.objects.get(entite="tiers").compteur_actuel, 7)  # donnée de référence : intact
        # données de référence conservées
        self.assertTrue(Tiers.objects.filter(code="CLI-PP").exists())
        self.assertTrue(Article.objects.filter(pk="TOLE-PP").exists())
        self.assertTrue(Article.objects.filter(pk="PIECE-PP").exists())  # article fabriqué gardé sans --articles-fabriques
        self.assertTrue(Matiere.objects.filter(pk="S235-PP").exists())

    def test_articles_fabriques_sur_demande(self):
        self.lancer("--confirmer", "--sauvegarde-faite", "--oui", "--articles-fabriques")
        self.assertFalse(Article.objects.filter(pk="PIECE-PP").exists())
        self.assertTrue(Article.objects.filter(pk="TOLE-PP").exists())

    def test_confirmation_a_taper(self):
        from unittest import mock

        with mock.patch("builtins.input", return_value="non"):
            with self.assertRaisesMessage(CommandError, "Annulé"):
                self.lancer("--confirmer", "--sauvegarde-faite")
        self.assertEqual(Devis.objects.count(), 2)
        with mock.patch("builtins.input", return_value="SUPPRIMER"):
            self.lancer("--confirmer", "--sauvegarde-faite")
        self.assertEqual(Devis.objects.count(), 0)
