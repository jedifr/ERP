import datetime

from django.test import TestCase

from chiffrage.models import Commande, Devis, DevisLigne, OperationOF, OrdreFabrication
from chiffrage.moteur import calculer_devis
from chiffrage.production import lancer_en_production
from commercial.models import Adresse, Tiers
from technique.models import Article, Nomenclature, PosteTravail, TarifPoste

from .services import PilotageError, marge_reelle_ordre_fabrication, taux_charge_poste


class MargeReelleTests(TestCase):
    def setUp(self):
        client_tiers = Tiers.objects.create(
            code="CLI-PIL", raison_sociale="Client Pilotage", type_tiers=Tiers.TypeTiers.CLIENT
        )
        for type_adresse in [Adresse.TypeAdresse.FACTURATION, Adresse.TypeAdresse.LIVRAISON]:
            Adresse.objects.create(
                tiers=client_tiers,
                type_adresse=type_adresse,
                adresse="1 rue",
                code_postal="75000",
                ville="Paris",
                est_principale=True,
            )

        composant = Article.objects.create(
            reference="VIS-PIL", nature=Article.Nature.MATIERE_PREMIERE,
            unite_cout=Article.UniteCout.PIECE, cout_unitaire=3.0,
        )
        self.article = Article.objects.create(reference="PIECE-PIL", nature=Article.Nature.FABRIQUE)
        Nomenclature.objects.create(article_parent=self.article, article_composant=composant, quantite=2)

        self.poste = PosteTravail.objects.create(
            nom="Fraiseuse-PIL", mode_calcul=PosteTravail.ModeCalcul.HORAIRE
        )
        TarifPoste.objects.create(poste=self.poste, cout_horaire=40, date_debut=datetime.date(2020, 1, 1))
        from technique.models import Gamme

        Gamme.objects.create(
            article=self.article, poste=self.poste, ordre=1, temps_fixe=5, temps_variable=2,
            date_debut=datetime.date(2020, 1, 1),
        )

        self.devis = Devis.objects.create(
            numero="DEV-PIL", client=client_tiers, date_creation=datetime.date(2026, 1, 1),
            statut=Devis.Statut.VALIDE, taux_marge_globale=25,
        )
        DevisLigne.objects.create(devis=self.devis, article=self.article, quantite=5)
        calculer_devis(self.devis)

        self.commande = lancer_en_production(self.devis)
        self.of = self.commande.ordres_fabrication.get()
        self.operation_of = self.of.operations.get(ordre=1)

    def test_marge_reelle_avec_temps_reel_superieur_au_prevu(self):
        # prévu : matière 6*5=30 (marge 25% -> 37.5) ; opération (5+2*5)*40=600 (marge 25% -> 750)
        # prévu total : prix 787.5, coût 630, marge prévue 157.5
        self.operation_of.temps_reel = 18  # réel plus long que prévu (15)
        self.operation_of.save()

        resultat = marge_reelle_ordre_fabrication(self.of)

        self.assertTrue(resultat["donnees_completes"])
        self.assertAlmostEqual(resultat["prix_vente_prevu_total"], 787.5)
        self.assertAlmostEqual(resultat["cout_prevu_total"], 630)
        self.assertAlmostEqual(resultat["marge_prevue"], 157.5)
        # coût réel : matière 30 + opération 18*40=720 = 750
        self.assertAlmostEqual(resultat["cout_reel_total"], 750)
        self.assertAlmostEqual(resultat["marge_reelle"], 787.5 - 750)
        self.assertAlmostEqual(resultat["ecart_marge"], (787.5 - 750) - 157.5)

    def test_donnees_incompletes_sans_temps_reel(self):
        resultat = marge_reelle_ordre_fabrication(self.of)
        self.assertFalse(resultat["donnees_completes"])

    def test_ordre_fabrication_sans_ligne_devis_leve_erreur(self):
        autre_article = Article.objects.create(reference="PIECE-PIL-2", nature=Article.Nature.FABRIQUE)
        of_orphelin = OrdreFabrication.objects.create(
            numero="OF-ORPHELIN", commande=self.commande, article=autre_article, quantite=1,
            date_lancement=datetime.date(2026, 1, 1),
        )
        with self.assertRaises(PilotageError):
            marge_reelle_ordre_fabrication(of_orphelin)


class TauxChargeTests(TestCase):
    def setUp(self):
        client_tiers = Tiers.objects.create(
            code="CLI-TC", raison_sociale="Client TC", type_tiers=Tiers.TypeTiers.CLIENT
        )
        adresse = Adresse.objects.create(
            tiers=client_tiers, type_adresse=Adresse.TypeAdresse.FACTURATION,
            adresse="1 rue", code_postal="75000", ville="Paris",
        )
        devis = Devis.objects.create(
            numero="DEV-TC", client=client_tiers, date_creation=datetime.date(2026, 1, 1), statut="valide"
        )
        commande = Commande.objects.create(
            numero="CDE-TC", devis=devis, date_commande=datetime.date(2026, 1, 1),
            adresse_facturation=adresse, adresse_livraison=adresse,
        )
        article = Article.objects.create(reference="PIECE-TC", nature=Article.Nature.FABRIQUE)
        self.poste = PosteTravail.objects.create(
            nom="Tour-TC", mode_calcul=PosteTravail.ModeCalcul.HORAIRE, nombre_machines=2
        )

        of1 = OrdreFabrication.objects.create(
            numero="OF-TC-1", commande=commande, article=article, quantite=1,
            date_lancement=datetime.date(2026, 1, 5),  # lundi
        )
        OperationOF.objects.create(ordre_fabrication=of1, poste=self.poste, ordre=1, temps_reel=10)

        of2 = OrdreFabrication.objects.create(
            numero="OF-TC-2", commande=commande, article=article, quantite=1,
            date_lancement=datetime.date(2026, 1, 7),  # mercredi
        )
        OperationOF.objects.create(ordre_fabrication=of2, poste=self.poste, ordre=1, temps_reel=15)

    def test_taux_charge_sur_semaine_ouvree(self):
        resultat = taux_charge_poste(
            self.poste, datetime.date(2026, 1, 5), datetime.date(2026, 1, 9)  # lundi à vendredi
        )
        self.assertEqual(resultat["temps_reel_cumule"], 25)
        self.assertEqual(resultat["capacite_disponible"], 2 * 5 * 7)  # 2 machines * 5 jours * 7h
        self.assertAlmostEqual(resultat["taux_charge"], 25 / 70)

    def test_mouvements_hors_periode_exclus(self):
        resultat = taux_charge_poste(
            self.poste, datetime.date(2026, 1, 12), datetime.date(2026, 1, 16)
        )
        self.assertEqual(resultat["temps_reel_cumule"], 0)
