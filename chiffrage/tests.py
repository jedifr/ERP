import datetime
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from commercial.models import Adresse, Contact, TauxTVA, Tiers
from technique.models import Article, Gamme, Matiere, Nomenclature, PosteTravail, TarifPoste

from .builder import ajouter_ligne_devis, creer_article_fabrique
from .models import Commande, Devis, DevisLigne, DevisLigneOperation, OrdreFabrication
from .moteur import ChiffrageError, calculer_devis, calculer_ligne, cout_matiere_article, previsualiser_ligne
from .planning_sync import PlanningSyncError, resynchroniser, tenter_synchronisation
from .production import lancer_en_production


def _creer_composants_nomenclature(parent):
    acier = Matiere.objects.create(nom="Acier", densite=7.85)

    tole_poids = Article.objects.create(
        reference="TOLE-S235",
        nature=Article.Nature.MATIERE_PREMIERE,
        matiere=acier,
        unite_cout=Article.UniteCout.POIDS,
        epaisseur=3,
        cout_unitaire=2.0,
    )
    tube_metre = Article.objects.create(
        reference="TUBE-ML",
        nature=Article.Nature.MATIERE_PREMIERE,
        unite_cout=Article.UniteCout.LONGUEUR,
        cout_unitaire=5.0,
    )
    tube_kilo = Article.objects.create(
        reference="TUBE-KG",
        nature=Article.Nature.MATIERE_PREMIERE,
        unite_cout=Article.UniteCout.LONGUEUR,
        cout_unitaire=3.0,
        poids_lineique=2.0,
    )
    vis = Article.objects.create(
        reference="VIS-M6",
        nature=Article.Nature.MATIERE_PREMIERE,
        unite_cout=Article.UniteCout.PIECE,
        cout_unitaire=0.05,
    )
    tole_m2 = Article.objects.create(
        reference="TOLE-M2",
        nature=Article.Nature.MATIERE_PREMIERE,
        unite_cout=Article.UniteCout.SURFACE,
        cout_unitaire=50.0,
    )

    Nomenclature.objects.create(
        article_parent=parent, article_composant=tole_poids, longueur_mm=200, largeur_mm=100, quantite=1
    )
    Nomenclature.objects.create(
        article_parent=parent, article_composant=tube_metre, longueur_mm=500, quantite=2
    )
    Nomenclature.objects.create(
        article_parent=parent, article_composant=tube_kilo, longueur_mm=1000, quantite=1
    )
    Nomenclature.objects.create(article_parent=parent, article_composant=vis, quantite=4)
    Nomenclature.objects.create(
        article_parent=parent, article_composant=tole_m2, longueur_mm=1000, largeur_mm=500, quantite=1
    )


class CoutMatiereTests(TestCase):
    def test_cout_matiere_article_fabrique_toutes_unites(self):
        parent = Article.objects.create(reference="PIECE-01", nature=Article.Nature.FABRIQUE)
        _creer_composants_nomenclature(parent)

        # 0.06 dm3 * 7.85 * 2.0 = 0.942
        # 0.5m * 5.0 * 2 = 5.0
        # 1.0m * 2.0kg/m * 3.0 * 1 = 6.0
        # 4 * 0.05 = 0.2
        # 0.5 m2 * 50.0 * 1 = 25.0
        # total par unité = 37.142
        cout_unitaire = cout_matiere_article(parent, 1)
        self.assertAlmostEqual(cout_unitaire, 37.142, places=3)

        cout_total = cout_matiere_article(parent, 3)
        self.assertAlmostEqual(cout_total, 111.426, places=3)

    def test_cout_matiere_premiere_directe(self):
        vis = Article.objects.create(
            reference="VIS-M8",
            nature=Article.Nature.MATIERE_PREMIERE,
            unite_cout=Article.UniteCout.PIECE,
            cout_unitaire=0.10,
        )
        self.assertAlmostEqual(cout_matiere_article(vis, 100), 10.0)

    def test_composant_sans_cout_unitaire_leve_erreur(self):
        parent = Article.objects.create(reference="PIECE-02", nature=Article.Nature.FABRIQUE)
        composant = Article.objects.create(
            reference="TOLE-05", nature=Article.Nature.MATIERE_PREMIERE, unite_cout=Article.UniteCout.PIECE
        )
        Nomenclature.objects.create(article_parent=parent, article_composant=composant, quantite=1)
        with self.assertRaises(ChiffrageError):
            cout_matiere_article(parent, 1)


class CalculerDevisTests(TestCase):
    def setUp(self):
        self.client_tiers = Tiers.objects.create(
            code="CLI-001", raison_sociale="Client Test", type_tiers=Tiers.TypeTiers.CLIENT
        )
        self.article = Article.objects.create(
            reference="PIECE-10", nature=Article.Nature.FABRIQUE, taux_marge_defaut=20
        )
        _creer_composants_nomenclature(self.article)

        self.poste_horaire = PosteTravail.objects.create(
            nom="Tour", mode_calcul=PosteTravail.ModeCalcul.HORAIRE, taux_marge_defaut=15
        )
        TarifPoste.objects.create(
            poste=self.poste_horaire, cout_horaire=50, date_debut=datetime.date(2020, 1, 1)
        )
        Gamme.objects.create(
            article=self.article,
            poste=self.poste_horaire,
            ordre=1,
            temps_fixe=10,
            temps_variable=5,
            date_debut=datetime.date(2020, 1, 1),
        )

        self.devis = Devis.objects.create(
            numero="DEV-001",
            client=self.client_tiers,
            date_creation=datetime.date(2026, 1, 15),
            statut=Devis.Statut.BROUILLON,
        )
        self.ligne = DevisLigne.objects.create(devis=self.devis, article=self.article, quantite=3)

    def test_calcul_matiere_et_marge_defaut(self):
        calculer_devis(self.devis)
        self.ligne.refresh_from_db()
        self.assertAlmostEqual(self.ligne.cout_matiere_calcule, 111.426, places=3)
        self.assertEqual(self.ligne.taux_marge_matiere_applique, 20)
        self.assertAlmostEqual(self.ligne.prix_vente_matiere, 111.426 * 1.2, places=3)

    def test_calcul_operations_gamme(self):
        calculer_devis(self.devis)
        operation = self.ligne.operations.get(ordre=1)
        # (10 + 5*3) * 50 = 1250
        self.assertAlmostEqual(operation.cout_calcule, 1250)
        self.assertEqual(operation.taux_marge_applique, 15)
        self.assertAlmostEqual(operation.prix_vente, 1250 * 1.15)

    def test_marge_globale_ecrase_les_defauts(self):
        self.devis.taux_marge_globale = 10
        self.devis.save()
        calculer_devis(self.devis)
        self.ligne.refresh_from_db()
        operation = self.ligne.operations.get(ordre=1)
        self.assertEqual(self.ligne.taux_marge_matiere_applique, 10)
        self.assertEqual(operation.taux_marge_applique, 10)

    def test_marge_ligne_editee_manuellement_conservee(self):
        calculer_devis(self.devis)
        self.ligne.refresh_from_db()
        self.ligne.taux_marge_matiere_applique = 5
        self.ligne.save()
        calculer_devis(self.devis)
        self.ligne.refresh_from_db()
        self.assertEqual(self.ligne.taux_marge_matiere_applique, 5)

    def test_tarif_poste_historise(self):
        # Un deuxième tarif prend le relais à partir du 2026-01-01.
        TarifPoste.objects.filter(poste=self.poste_horaire).update(date_fin=datetime.date(2025, 12, 31))
        TarifPoste.objects.create(
            poste=self.poste_horaire, cout_horaire=60, date_debut=datetime.date(2026, 1, 1)
        )
        calculer_devis(self.devis)
        operation = self.ligne.operations.get(ordre=1)
        # (10 + 5*3) * 60 = 1500
        self.assertAlmostEqual(operation.cout_calcule, 1500)

    def test_aucun_tarif_valide_leve_erreur(self):
        TarifPoste.objects.filter(poste=self.poste_horaire).delete()
        with self.assertRaises(ChiffrageError):
            calculer_devis(self.devis)

    def test_prix_unitaire_force_remplace_le_calcul_automatique(self):
        self.ligne.prix_vente_unitaire_force = 50
        self.ligne.save()
        calculer_devis(self.devis)
        self.ligne.refresh_from_db()
        # quantite=3 * prix forcé 50 = 150, au lieu de 111.426 * 1.2 = 133.7112
        self.assertEqual(self.ligne.prix_vente_matiere, 150)
        # le coût matière reste calculé normalement (juste le prix de vente est forcé)
        self.assertAlmostEqual(self.ligne.cout_matiere_calcule, 111.426, places=3)

    def test_prix_vente_total_ligne_integre_les_operations(self):
        calculer_devis(self.devis)
        self.ligne.refresh_from_db()
        # matière : 111.426 * 1.2 = 133.7112 ; opération : 1250 * 1.15 = 1437.5
        self.assertAlmostEqual(self.ligne.prix_vente_operations, 1437.5)
        self.assertAlmostEqual(self.ligne.prix_vente_total, 133.7112 + 1437.5, places=3)

    def test_prix_vente_total_ligne_none_si_matiere_non_calculee(self):
        self.assertIsNone(self.ligne.prix_vente_matiere)
        self.assertIsNone(self.ligne.prix_vente_total)

    def test_montants_devis_integrent_matiere_et_operations(self):
        calculer_devis(self.devis)
        self.assertAlmostEqual(self.devis.montant_matiere_ht, 133.7112, places=3)
        self.assertAlmostEqual(self.devis.montant_operations_ht, 1437.5)
        self.assertAlmostEqual(self.devis.montant_total_ht, 133.7112 + 1437.5, places=3)


class LancerEnProductionTests(TestCase):
    def setUp(self):
        self.client_tiers = Tiers.objects.create(
            code="CLI-002", raison_sociale="Client Prod", type_tiers=Tiers.TypeTiers.CLIENT
        )
        Adresse.objects.create(
            tiers=self.client_tiers,
            type_adresse=Adresse.TypeAdresse.FACTURATION,
            adresse="1 rue A",
            code_postal="75000",
            ville="Paris",
            est_principale=True,
        )
        Adresse.objects.create(
            tiers=self.client_tiers,
            type_adresse=Adresse.TypeAdresse.LIVRAISON,
            adresse="1 rue A",
            code_postal="75000",
            ville="Paris",
            est_principale=True,
        )

        self.article_fabrique = Article.objects.create(reference="PIECE-20", nature=Article.Nature.FABRIQUE)
        self.poste = PosteTravail.objects.create(
            nom="Fraiseuse", mode_calcul=PosteTravail.ModeCalcul.HORAIRE
        )
        TarifPoste.objects.create(poste=self.poste, cout_horaire=40, date_debut=datetime.date(2020, 1, 1))
        Gamme.objects.create(
            article=self.article_fabrique,
            poste=self.poste,
            ordre=1,
            temps_fixe=5,
            temps_variable=2,
            date_debut=datetime.date(2020, 1, 1),
        )
        self.article_mp = Article.objects.create(
            reference="VIS-20",
            nature=Article.Nature.MATIERE_PREMIERE,
            unite_cout=Article.UniteCout.PIECE,
            cout_unitaire=0.1,
        )

        self.devis = Devis.objects.create(
            numero="DEV-100",
            client=self.client_tiers,
            date_creation=datetime.date(2026, 1, 1),
            statut=Devis.Statut.VALIDE,
        )
        DevisLigne.objects.create(devis=self.devis, article=self.article_fabrique, quantite=2)
        DevisLigne.objects.create(devis=self.devis, article=self.article_mp, quantite=50)

    def test_cree_commande_et_of_pour_les_lignes_fabriquees(self):
        commande = lancer_en_production(self.devis)
        self.assertEqual(Commande.objects.count(), 1)
        self.assertEqual(commande.devis, self.devis)

        ordres = list(commande.ordres_fabrication.all())
        self.assertEqual(len(ordres), 1)
        of = ordres[0]
        self.assertEqual(of.article, self.article_fabrique)
        self.assertEqual(of.quantite, 2)

        operation = of.operations.get(ordre=1)
        # (5 + 2*2) = 9
        self.assertAlmostEqual(operation.temps_prevu, 9)

    def test_of_reste_en_attente_sans_api_planning_configuree(self):
        of = lancer_en_production(self.devis).ordres_fabrication.first()
        self.assertEqual(of.statut_synchro, OrdreFabrication.StatutSynchro.EN_ATTENTE)
        self.assertEqual(of.nombre_tentatives, 1)

    def test_devis_non_valide_refuse(self):
        self.devis.statut = Devis.Statut.BROUILLON
        self.devis.save()
        with self.assertRaises(ChiffrageError):
            lancer_en_production(self.devis)

    def test_deuxieme_lancement_refuse(self):
        lancer_en_production(self.devis)
        with self.assertRaises(ChiffrageError):
            lancer_en_production(self.devis)

    def test_sans_adresse_principale_refuse(self):
        Adresse.objects.filter(tiers=self.client_tiers, type_adresse=Adresse.TypeAdresse.LIVRAISON).delete()
        with self.assertRaises(ChiffrageError):
            lancer_en_production(self.devis)


class PlanningSyncTests(TestCase):
    def setUp(self):
        tiers = Tiers.objects.create(code="CLI-003", raison_sociale="Client Sync", type_tiers="client")
        commande_devis = Devis.objects.create(
            numero="DEV-200", client=tiers, date_creation=datetime.date(2026, 1, 1), statut="valide"
        )
        adresse = Adresse.objects.create(
            tiers=tiers,
            type_adresse=Adresse.TypeAdresse.FACTURATION,
            adresse="1 rue",
            code_postal="75000",
            ville="Paris",
        )
        commande = Commande.objects.create(
            numero="CDE-200",
            devis=commande_devis,
            date_commande=datetime.date(2026, 1, 1),
            adresse_facturation=adresse,
            adresse_livraison=adresse,
        )
        article = Article.objects.create(reference="PIECE-30", nature=Article.Nature.FABRIQUE)
        self.of = OrdreFabrication.objects.create(
            numero="OF-200", commande=commande, article=article, quantite=1, date_lancement=datetime.date(2026, 1, 1)
        )

    def test_sans_api_configuree_reste_en_attente(self):
        reussite = tenter_synchronisation(self.of)
        self.assertFalse(reussite)
        self.of.refresh_from_db()
        self.assertEqual(self.of.statut_synchro, OrdreFabrication.StatutSynchro.EN_ATTENTE)
        self.assertEqual(self.of.nombre_tentatives, 1)

    @override_settings(PLANNING_SYNC_MAX_TENTATIVES=2)
    def test_echec_persistant_apres_max_tentatives(self):
        tenter_synchronisation(self.of)
        tenter_synchronisation(self.of)
        self.of.refresh_from_db()
        self.assertEqual(self.of.statut_synchro, OrdreFabrication.StatutSynchro.ECHEC_PERSISTANT)
        self.assertEqual(self.of.nombre_tentatives, 2)

    def test_resynchroniser_remet_le_compteur_a_zero(self):
        self.of.nombre_tentatives = 5
        self.of.statut_synchro = OrdreFabrication.StatutSynchro.ECHEC_PERSISTANT
        self.of.save()

        resynchroniser(self.of)
        self.of.refresh_from_db()
        self.assertEqual(self.of.nombre_tentatives, 1)
        self.assertEqual(self.of.statut_synchro, OrdreFabrication.StatutSynchro.EN_ATTENTE)

    @override_settings(PLANNING_API_URL="http://planning-atelier.local/api")
    def test_synchronisation_reussie_avec_api_configuree(self):
        with patch("chiffrage.planning_sync.requests.post") as mock_post:
            mock_post.return_value.raise_for_status.return_value = None
            reussite = tenter_synchronisation(self.of)

        self.assertTrue(reussite)
        self.of.refresh_from_db()
        self.assertEqual(self.of.statut_synchro, OrdreFabrication.StatutSynchro.SYNCHRONISE)
        args, kwargs = mock_post.call_args
        self.assertEqual(kwargs["headers"]["Idempotency-Key"], self.of.numero)

    @override_settings(PLANNING_API_URL="http://planning-atelier.local/api")
    def test_synchronisation_http_en_erreur_est_geree(self):
        import requests

        with patch("chiffrage.planning_sync.requests.post", side_effect=requests.ConnectionError("down")):
            with self.assertRaises(PlanningSyncError):
                from .planning_sync import PlanningSyncClient

                PlanningSyncClient().envoyer_ordre_fabrication(self.of)


class BuilderTests(TestCase):
    """Constructeur de devis : création à la volée d'un article fabriqué
    (nomenclature + gamme) et de sa ligne de devis, en une transaction."""

    def setUp(self):
        self.composant = Article.objects.create(
            reference="VIS-BUILDER",
            nature=Article.Nature.MATIERE_PREMIERE,
            unite_cout=Article.UniteCout.PIECE,
            cout_unitaire=0.1,
        )
        self.poste = PosteTravail.objects.create(
            nom="Poste-Builder", mode_calcul=PosteTravail.ModeCalcul.HORAIRE
        )
        client_tiers = Tiers.objects.create(
            code="CLI-BUILDER", raison_sociale="Client Builder", type_tiers=Tiers.TypeTiers.CLIENT
        )
        self.devis = Devis.objects.create(
            numero="DEV-BUILDER",
            client=client_tiers,
            date_creation=datetime.date(2026, 1, 1),
            statut=Devis.Statut.BROUILLON,
        )

    def _composants(self):
        return [{"article_composant": self.composant, "quantite": 3}]

    def _etapes(self):
        return [
            {
                "poste": self.poste,
                "ordre": 1,
                "temps_fixe": 5,
                "temps_variable": 2,
                "date_debut": datetime.date(2026, 1, 1),
            }
        ]

    def test_creer_article_fabrique_avec_nomenclature_et_gamme(self):
        article = creer_article_fabrique(
            reference="PIECE-BUILDER-TEST",
            taux_marge_defaut=15,
            composants=self._composants(),
            etapes=self._etapes(),
        )
        self.assertEqual(article.nature, Article.Nature.FABRIQUE)
        self.assertEqual(article.composants.count(), 1)
        self.assertEqual(article.gamme_etapes.count(), 1)
        self.assertEqual(article.composants.get().article_composant, self.composant)

    def test_reference_existante_refusee(self):
        Article.objects.create(reference="PIECE-DEJA", nature=Article.Nature.FABRIQUE)
        with self.assertRaises(ChiffrageError):
            creer_article_fabrique(
                reference="PIECE-DEJA",
                taux_marge_defaut=None,
                composants=self._composants(),
                etapes=self._etapes(),
            )

    def test_sans_composant_refuse(self):
        with self.assertRaises(ChiffrageError):
            creer_article_fabrique(
                reference="PIECE-SANS-COMPOSANT",
                taux_marge_defaut=None,
                composants=[],
                etapes=self._etapes(),
            )

    def test_sans_etape_refuse(self):
        with self.assertRaises(ChiffrageError):
            creer_article_fabrique(
                reference="PIECE-SANS-ETAPE",
                taux_marge_defaut=None,
                composants=self._composants(),
                etapes=[],
            )

    def test_etape_invalide_leve_erreur_et_ne_cree_rien(self):
        # Poste horaire sans temps_fixe/temps_variable : invalide (règle Phase 1).
        etapes_invalides = [{"poste": self.poste, "ordre": 1, "date_debut": datetime.date(2026, 1, 1)}]
        with self.assertRaises(ChiffrageError):
            creer_article_fabrique(
                reference="PIECE-INVALIDE",
                taux_marge_defaut=None,
                composants=self._composants(),
                etapes=etapes_invalides,
            )
        self.assertFalse(Article.objects.filter(pk="PIECE-INVALIDE").exists())

    def test_ajouter_ligne_devis(self):
        article = creer_article_fabrique(
            reference="PIECE-BUILDER-LIGNE",
            taux_marge_defaut=None,
            composants=self._composants(),
            etapes=self._etapes(),
        )
        ligne = ajouter_ligne_devis(self.devis, article, 4)
        self.assertEqual(ligne.devis, self.devis)
        self.assertEqual(ligne.quantite, 4)

    def test_ajouter_ligne_sur_devis_non_brouillon_refuse(self):
        self.devis.statut = Devis.Statut.VALIDE
        self.devis.save()
        article = creer_article_fabrique(
            reference="PIECE-BUILDER-VALIDE",
            taux_marge_defaut=None,
            composants=self._composants(),
            etapes=self._etapes(),
        )
        with self.assertRaises(ChiffrageError):
            ajouter_ligne_devis(self.devis, article, 1)


class DevisBuilderViewTests(TestCase):
    """Vue du constructeur de devis (POST JSON)."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        self.user = User.objects.create_superuser("builder-admin", "b@example.com", "pass1234")
        self.client.force_login(self.user)

        self.composant = Article.objects.create(
            reference="VIS-VIEW",
            nature=Article.Nature.MATIERE_PREMIERE,
            unite_cout=Article.UniteCout.PIECE,
            cout_unitaire=0.2,
        )
        self.poste = PosteTravail.objects.create(
            nom="Poste-View", mode_calcul=PosteTravail.ModeCalcul.FORFAITAIRE
        )
        client_tiers = Tiers.objects.create(
            code="CLI-VIEW", raison_sociale="Client View", type_tiers=Tiers.TypeTiers.CLIENT
        )
        self.devis = Devis.objects.create(
            numero="DEV-VIEW",
            client=client_tiers,
            date_creation=datetime.date(2026, 1, 1),
            statut=Devis.Statut.BROUILLON,
        )

    def test_get_affiche_la_page(self):
        response = self.client.get(f"/admin/chiffrage/devis/{self.devis.pk}/constructeur/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Constructeur de devis")
        # Les temps de gamme (temps_fixe/temps_variable) s'expriment en minutes.
        self.assertContains(response, "Temps fixe (min)")
        self.assertContains(response, "Temps variable (min/pièce)")

    def test_post_nouvel_article_cree_tout(self):
        payload = {
            "quantite": 3,
            "nouvel_article": {
                "reference": "PIECE-VIEW-1",
                "taux_marge_defaut": 10,
                "composants": [{"article_composant": "VIS-VIEW", "quantite": 5}],
                "etapes": [
                    {"poste": "Poste-View", "ordre": 1, "cout_forfaitaire": 50, "date_debut": "2026-01-01"}
                ],
            },
        }
        response = self.client.post(
            f"/admin/chiffrage/devis/{self.devis.pk}/constructeur/",
            data=payload,
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(Article.objects.filter(pk="PIECE-VIEW-1").exists())
        self.assertEqual(self.devis.lignes.count(), 1)
        data = response.json()
        # matière : 3 * (5 * 0.2) = 3, marge 10% -> prix_vente_matiere = 3.3
        # opération : 3 * 50 (étape forfaitaire) = 150, marge par défaut du poste (0%) -> 150
        self.assertEqual(data["cout_matiere_calcule"], 3)
        self.assertAlmostEqual(data["prix_vente_operations"], 150)
        self.assertAlmostEqual(data["prix_vente_total"], 153.3, places=3)
        self.assertAlmostEqual(data["montant_total_ht"], 153.3, places=3)
        self.assertIsNone(data["avertissement"])

    def test_post_article_existant(self):
        article = Article.objects.create(
            reference="PIECE-VIEW-EXIST",
            nature=Article.Nature.MATIERE_PREMIERE,
            unite_cout=Article.UniteCout.PIECE,
            cout_unitaire=4,
        )
        payload = {"quantite": 2, "article_existant": "PIECE-VIEW-EXIST"}
        response = self.client.post(
            f"/admin/chiffrage/devis/{self.devis.pk}/constructeur/",
            data=payload,
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.devis.lignes.get().article, article)
        data = response.json()
        self.assertEqual(data["cout_matiere_calcule"], 8)
        self.assertEqual(data["prix_vente_operations"], 0)
        self.assertEqual(data["prix_vente_total"], 8)
        self.assertIsNone(data["avertissement"])

    def test_post_article_sans_cout_unitaire_avertit_sans_bloquer(self):
        article = Article.objects.create(
            reference="PIECE-VIEW-SANS-COUT",
            nature=Article.Nature.MATIERE_PREMIERE,
            unite_cout=Article.UniteCout.PIECE,
        )
        payload = {"quantite": 2, "article_existant": "PIECE-VIEW-SANS-COUT"}
        response = self.client.post(
            f"/admin/chiffrage/devis/{self.devis.pk}/constructeur/",
            data=payload,
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertEqual(self.devis.lignes.get().article, article)
        self.assertIsNotNone(data["avertissement"])
        self.assertIsNone(data["cout_matiere_calcule"])
        self.assertIsNone(data["prix_vente_total"])

    def test_post_article_introuvable_400(self):
        payload = {"quantite": 1, "article_existant": "INEXISTANT"}
        response = self.client.post(
            f"/admin/chiffrage/devis/{self.devis.pk}/constructeur/",
            data=payload,
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_anonyme_redirige(self):
        self.client.logout()
        response = self.client.get(f"/admin/chiffrage/devis/{self.devis.pk}/constructeur/")
        self.assertNotEqual(response.status_code, 200)


class RecalculerLigneViewTests(TestCase):
    """Recalcul en direct d'une ligne de devis (quantité / taux de marge)."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        self.user = User.objects.create_superuser("live-admin", "l@example.com", "pass1234")
        self.client.force_login(self.user)

        self.article = Article.objects.create(
            reference="ART-LIVE-TEST",
            nature=Article.Nature.MATIERE_PREMIERE,
            unite_cout=Article.UniteCout.PIECE,
            cout_unitaire=2.0,
        )
        client_tiers = Tiers.objects.create(
            code="CLI-LIVE-TEST", raison_sociale="Client Live Test", type_tiers=Tiers.TypeTiers.CLIENT
        )
        self.devis = Devis.objects.create(
            numero="DEV-LIVE-TEST",
            client=client_tiers,
            date_creation=datetime.date(2026, 1, 1),
            statut=Devis.Statut.BROUILLON,
        )
        self.ligne = DevisLigne.objects.create(devis=self.devis, article=self.article, quantite=3)

    def _url(self):
        return f"/admin/chiffrage/devis/{self.devis.pk}/lignes/{self.ligne.id}/recalculer/"

    def test_recalcule_quantite(self):
        response = self.client.post(
            self._url(), data={"quantite": 5}, content_type="application/json"
        )
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertEqual(data["cout_matiere_calcule"], 10)
        self.assertEqual(data["prix_vente_matiere"], 10)
        self.assertEqual(data["prix_vente_operations"], 0)
        self.assertEqual(data["prix_vente_total"], 10)
        self.assertEqual(data["montant_total_ht"], 10)
        self.ligne.refresh_from_db()
        self.assertEqual(self.ligne.quantite, 5)

    def test_recalcule_taux_marge(self):
        self.client.post(self._url(), data={"quantite": 5}, content_type="application/json")
        response = self.client.post(
            self._url(), data={"taux_marge_matiere_applique": 25}, content_type="application/json"
        )
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertEqual(data["cout_matiere_calcule"], 10)
        self.assertEqual(data["prix_vente_matiere"], 12.5)

    def test_taux_marge_vide_revient_au_defaut(self):
        self.article.taux_marge_defaut = 10
        self.article.save()
        self.client.post(
            self._url(), data={"taux_marge_matiere_applique": 25}, content_type="application/json"
        )
        response = self.client.post(
            self._url(), data={"taux_marge_matiere_applique": ""}, content_type="application/json"
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["taux_marge_matiere_applique"], 10)

    def test_quantite_invalide_400(self):
        response = self.client.post(
            self._url(), data={"quantite": "abc"}, content_type="application/json"
        )
        self.assertEqual(response.status_code, 400)

    def test_ligne_dune_autre_devis_404(self):
        autre_devis = Devis.objects.create(
            numero="DEV-AUTRE",
            client=self.devis.client,
            date_creation=datetime.date(2026, 1, 1),
            statut=Devis.Statut.BROUILLON,
        )
        response = self.client.post(
            f"/admin/chiffrage/devis/{autre_devis.pk}/lignes/{self.ligne.id}/recalculer/",
            data={"quantite": 5},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)

    def test_anonyme_refuse(self):
        self.client.logout()
        response = self.client.post(
            self._url(), data={"quantite": 5}, content_type="application/json"
        )
        self.assertNotEqual(response.status_code, 200)


class CalculerLigneIsoleeTests(TestCase):
    """Une ligne à problème (ex. article sans coût unitaire) ne doit jamais
    bloquer le calcul en direct des AUTRES lignes du même devis. Reproduit un
    signalement utilisateur : deux lignes affichaient toutes les deux des "-"
    (aucun calcul), alors qu'une seule des deux articles posait problème —
    en cause, recalculer_ligne_view appelait calculer_devis() (qui s'arrête
    à la première ligne en erreur) au lieu de calculer_ligne() (isolée)."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        self.user = User.objects.create_superuser("isolee-admin", "i@example.com", "pass1234")
        self.client.force_login(self.user)

        self.article_sans_cout = Article.objects.create(
            reference="ART-SANS-COUT-ISOLEE",
            nature=Article.Nature.MATIERE_PREMIERE,
            unite_cout=Article.UniteCout.PIECE,
        )
        self.article_ok = Article.objects.create(
            reference="ART-OK-ISOLEE",
            nature=Article.Nature.MATIERE_PREMIERE,
            unite_cout=Article.UniteCout.PIECE,
            cout_unitaire=5.0,
        )
        client_tiers = Tiers.objects.create(
            code="CLI-ISOLEE", raison_sociale="Client Isolee", type_tiers=Tiers.TypeTiers.CLIENT
        )
        self.devis = Devis.objects.create(
            numero="DEV-ISOLEE",
            client=client_tiers,
            date_creation=datetime.date(2026, 1, 1),
            statut=Devis.Statut.BROUILLON,
        )
        self.ligne_en_erreur = DevisLigne.objects.create(
            devis=self.devis, article=self.article_sans_cout, quantite=2
        )
        self.ligne_ok = DevisLigne.objects.create(devis=self.devis, article=self.article_ok, quantite=3)

    def test_calculer_devis_sarrete_a_la_premiere_erreur(self):
        # Comportement historique de calculer_devis(), volontairement conservé
        # pour l'action admin "Recalculer le chiffrage" (en bloc).
        with self.assertRaises(ChiffrageError):
            calculer_devis(self.devis)

    def test_calculer_ligne_ok_reussit_malgre_lautre_ligne_en_erreur(self):
        calculer_ligne(self.devis, self.ligne_ok)
        self.ligne_ok.refresh_from_db()
        self.assertEqual(self.ligne_ok.prix_vente_matiere, 15)

    def test_calculer_ligne_en_erreur_leve_sans_toucher_lautre(self):
        with self.assertRaises(ChiffrageError):
            calculer_ligne(self.devis, self.ligne_en_erreur)
        self.ligne_ok.refresh_from_db()
        self.assertIsNone(self.ligne_ok.prix_vente_matiere)

    def test_recalcul_live_ligne_ok_reussit_malgre_lautre_ligne_en_erreur(self):
        url = f"/admin/chiffrage/devis/{self.devis.pk}/lignes/{self.ligne_ok.id}/recalculer/"
        response = self.client.post(url, data={"quantite": 3}, content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertEqual(data["prix_vente_matiere"], 15)
        # Le montant du devis ne compte que la ligne effectivement calculée.
        self.assertEqual(data["montant_total_ht"], 15)

    def test_recalcul_live_ligne_en_erreur_renvoie_400(self):
        url = f"/admin/chiffrage/devis/{self.devis.pk}/lignes/{self.ligne_en_erreur.id}/recalculer/"
        response = self.client.post(url, data={"quantite": 2}, content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("ART-SANS-COUT-ISOLEE", response.json()["detail"])


class RecalculerLigneAvecOperationsTests(TestCase):
    """Le recalcul en direct doit refléter le temps machine (opérations de gamme),
    pas seulement le coût matière — reproduit le signalement utilisateur."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        self.user = User.objects.create_superuser("ops-admin", "o@example.com", "pass1234")
        self.client.force_login(self.user)

        self.article = Article.objects.create(
            reference="PIECE-OPS", nature=Article.Nature.FABRIQUE, taux_marge_defaut=20
        )
        _creer_composants_nomenclature(self.article)

        self.poste = PosteTravail.objects.create(
            nom="Laser-Ops", mode_calcul=PosteTravail.ModeCalcul.HORAIRE, taux_marge_defaut=15
        )
        TarifPoste.objects.create(poste=self.poste, cout_horaire=50, date_debut=datetime.date(2020, 1, 1))
        Gamme.objects.create(
            article=self.article,
            poste=self.poste,
            ordre=1,
            temps_fixe=10,
            temps_variable=5,
            date_debut=datetime.date(2020, 1, 1),
        )

        client_tiers = Tiers.objects.create(
            code="CLI-OPS", raison_sociale="Client Ops", type_tiers=Tiers.TypeTiers.CLIENT
        )
        self.devis = Devis.objects.create(
            numero="DEV-OPS",
            client=client_tiers,
            date_creation=datetime.date(2026, 1, 1),
            statut=Devis.Statut.BROUILLON,
        )
        self.ligne = DevisLigne.objects.create(devis=self.devis, article=self.article, quantite=3)

    def test_recalcul_live_integre_le_temps_machine(self):
        response = self.client.post(
            f"/admin/chiffrage/devis/{self.devis.pk}/lignes/{self.ligne.id}/recalculer/",
            data={"quantite": 3},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        # matière : 111.426 * 1.2 = 133.7112
        # opération : (10 + 5*3) * 50 = 1250, marge 15% -> 1437.5
        self.assertAlmostEqual(data["prix_vente_matiere"], 133.7112, places=3)
        self.assertAlmostEqual(data["prix_vente_operations"], 1437.5)
        self.assertAlmostEqual(data["prix_vente_total"], 133.7112 + 1437.5, places=3)
        self.assertAlmostEqual(data["montant_total_ht"], 133.7112 + 1437.5, places=3)


class DevisAdressesContactTests(TestCase):
    """Client, adresse de facturation, adresse de livraison et contact sur le devis."""

    def setUp(self):
        self.client_tiers = Tiers.objects.create(
            code="CLI-ADR", raison_sociale="Client Adresses", type_tiers=Tiers.TypeTiers.CLIENT
        )
        self.autre_tiers = Tiers.objects.create(
            code="CLI-AUTRE", raison_sociale="Autre Client", type_tiers=Tiers.TypeTiers.CLIENT
        )
        self.adresse_facturation = Adresse.objects.create(
            tiers=self.client_tiers,
            type_adresse=Adresse.TypeAdresse.FACTURATION,
            adresse="1 rue de la Facture",
            code_postal="75000",
            ville="Paris",
        )
        self.adresse_livraison = Adresse.objects.create(
            tiers=self.client_tiers,
            type_adresse=Adresse.TypeAdresse.LIVRAISON,
            adresse="2 rue de la Livraison",
            code_postal="75000",
            ville="Paris",
        )
        self.contact = Contact.objects.create(tiers=self.client_tiers, nom="Dupont", prenom="Jean")

    def test_devis_avec_adresses_et_contact_du_client(self):
        devis = Devis(
            numero="DEV-ADR-1",
            client=self.client_tiers,
            adresse_facturation=self.adresse_facturation,
            adresse_livraison=self.adresse_livraison,
            contact=self.contact,
            date_creation=datetime.date(2026, 1, 1),
        )
        devis.full_clean()  # ne doit pas lever d'exception
        devis.save()
        self.assertEqual(devis.adresse_facturation, self.adresse_facturation)
        self.assertEqual(devis.adresse_livraison, self.adresse_livraison)
        self.assertEqual(devis.contact, self.contact)

    def test_devis_sans_adresse_ni_contact_reste_valide(self):
        devis = Devis(
            numero="DEV-ADR-2", client=self.client_tiers, date_creation=datetime.date(2026, 1, 1)
        )
        devis.full_clean()  # tous optionnels : ne doit pas lever d'exception

    def test_adresse_facturation_dun_autre_tiers_refusee(self):
        adresse_autre = Adresse.objects.create(
            tiers=self.autre_tiers,
            type_adresse=Adresse.TypeAdresse.FACTURATION,
            adresse="3 rue Ailleurs",
            code_postal="69000",
            ville="Lyon",
        )
        devis = Devis(
            numero="DEV-ADR-3",
            client=self.client_tiers,
            adresse_facturation=adresse_autre,
            date_creation=datetime.date(2026, 1, 1),
        )
        with self.assertRaises(ValidationError):
            devis.full_clean()

    def test_contact_dun_autre_tiers_refuse(self):
        contact_autre = Contact.objects.create(tiers=self.autre_tiers, nom="Martin")
        devis = Devis(
            numero="DEV-ADR-4",
            client=self.client_tiers,
            contact=contact_autre,
            date_creation=datetime.date(2026, 1, 1),
        )
        with self.assertRaises(ValidationError):
            devis.full_clean()


class PrevisualiserLigneTests(TestCase):
    """moteur.previsualiser_ligne : aperçu sans rien persister en base."""

    def setUp(self):
        self.client_tiers = Tiers.objects.create(
            code="CLI-APERCU", raison_sociale="Client Aperçu", type_tiers=Tiers.TypeTiers.CLIENT
        )
        self.devis = Devis.objects.create(
            numero="DEV-APERCU",
            client=self.client_tiers,
            date_creation=datetime.date(2026, 1, 15),
            statut=Devis.Statut.BROUILLON,
        )
        self.article_matiere = Article.objects.create(
            reference="ART-APERCU",
            nature=Article.Nature.MATIERE_PREMIERE,
            unite_cout=Article.UniteCout.PIECE,
            cout_unitaire=2.0,
            taux_marge_defaut=25,
        )

    def test_apercu_matiere_premiere_ne_persiste_rien(self):
        resultat = previsualiser_ligne(self.devis, self.article_matiere, 4)
        self.assertEqual(resultat["cout_matiere_calcule"], 8)
        self.assertEqual(resultat["taux_marge_matiere_applique"], 25)
        self.assertEqual(resultat["prix_vente_matiere"], 10)
        self.assertEqual(resultat["prix_vente_operations"], 0)
        self.assertEqual(resultat["prix_vente_total"], 10)
        self.assertEqual(DevisLigne.objects.count(), 0)

    def test_apercu_avec_prix_unitaire_force(self):
        resultat = previsualiser_ligne(self.devis, self.article_matiere, 4, prix_vente_unitaire_force=3)
        self.assertEqual(resultat["prix_vente_matiere"], 12)
        self.assertEqual(DevisLigne.objects.count(), 0)

    def test_apercu_avec_operations_fabrique(self):
        article = Article.objects.create(
            reference="PIECE-APERCU", nature=Article.Nature.FABRIQUE, taux_marge_defaut=20
        )
        _creer_composants_nomenclature(article)
        poste = PosteTravail.objects.create(
            nom="Poste-Apercu", mode_calcul=PosteTravail.ModeCalcul.HORAIRE, taux_marge_defaut=15
        )
        TarifPoste.objects.create(poste=poste, cout_horaire=50, date_debut=datetime.date(2020, 1, 1))
        Gamme.objects.create(
            article=article,
            poste=poste,
            ordre=1,
            temps_fixe=10,
            temps_variable=5,
            date_debut=datetime.date(2020, 1, 1),
        )

        resultat = previsualiser_ligne(self.devis, article, 3)
        # matière : 111.426 * 1.2 = 133.7112 ; opération : (10+5*3)*50 = 1250 * 1.15 = 1437.5
        self.assertAlmostEqual(resultat["prix_vente_matiere"], 133.7112, places=3)
        self.assertAlmostEqual(resultat["prix_vente_operations"], 1437.5)
        self.assertAlmostEqual(resultat["prix_vente_total"], 133.7112 + 1437.5, places=3)
        self.assertEqual(DevisLigne.objects.count(), 0)
        self.assertEqual(DevisLigneOperation.objects.count(), 0)

    def test_apercu_erreur_si_article_sans_cout_unitaire(self):
        article = Article.objects.create(
            reference="ART-APERCU-SANS-COUT",
            nature=Article.Nature.MATIERE_PREMIERE,
            unite_cout=Article.UniteCout.PIECE,
        )
        with self.assertRaises(ChiffrageError):
            previsualiser_ligne(self.devis, article, 1)


class PrevisualiserLigneViewTests(TestCase):
    """Endpoint POST .../lignes/previsualiser/ utilisé pour l'aperçu live d'une
    ligne pas encore enregistrée dans l'inline de la fiche Devis."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        self.user = User.objects.create_superuser("apercu-admin", "a@example.com", "pass1234")
        self.client.force_login(self.user)

        self.article = Article.objects.create(
            reference="ART-APERCU-VIEW",
            nature=Article.Nature.MATIERE_PREMIERE,
            unite_cout=Article.UniteCout.PIECE,
            cout_unitaire=2.0,
        )
        client_tiers = Tiers.objects.create(
            code="CLI-APERCU-VIEW", raison_sociale="Client Aperçu View", type_tiers=Tiers.TypeTiers.CLIENT
        )
        self.devis = Devis.objects.create(
            numero="DEV-APERCU-VIEW",
            client=client_tiers,
            date_creation=datetime.date(2026, 1, 1),
            statut=Devis.Statut.BROUILLON,
        )

    def _url(self):
        return f"/admin/chiffrage/devis/{self.devis.pk}/lignes/previsualiser/"

    def test_apercu_reussi(self):
        response = self.client.post(
            self._url(),
            data={"article": "ART-APERCU-VIEW", "quantite": 5},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertEqual(data["cout_matiere_calcule"], 10)
        self.assertEqual(data["prix_vente_matiere"], 10)
        self.assertEqual(DevisLigne.objects.count(), 0)

    def test_apercu_avec_prix_force(self):
        response = self.client.post(
            self._url(),
            data={"article": "ART-APERCU-VIEW", "quantite": 5, "prix_vente_unitaire_force": 4},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["prix_vente_matiere"], 20)

    def test_article_introuvable_400(self):
        response = self.client.post(
            self._url(),
            data={"article": "INEXISTANT", "quantite": 1},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_quantite_invalide_400(self):
        response = self.client.post(
            self._url(),
            data={"article": "ART-APERCU-VIEW", "quantite": "abc"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_article_sans_cout_unitaire_400(self):
        Article.objects.create(
            reference="ART-APERCU-VIEW-SANS-COUT",
            nature=Article.Nature.MATIERE_PREMIERE,
            unite_cout=Article.UniteCout.PIECE,
        )
        response = self.client.post(
            self._url(),
            data={"article": "ART-APERCU-VIEW-SANS-COUT", "quantite": 1},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_anonyme_refuse(self):
        self.client.logout()
        response = self.client.post(
            self._url(),
            data={"article": "ART-APERCU-VIEW", "quantite": 1},
            content_type="application/json",
        )
        self.assertNotEqual(response.status_code, 200)


class PrevisualiserLigneNouveauDevisViewTests(TestCase):
    """Endpoint POST .../nouveau-devis/previsualiser-ligne/ : aperçu live sur
    le formulaire d'AJOUT d'un devis, où le devis lui-même n'existe pas
    encore en base (pas de numéro). Reproduit un signalement utilisateur :
    le calcul en direct ne se déclenchait pas du tout sur ce formulaire."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        self.user = User.objects.create_superuser("nouveau-devis-admin", "n@example.com", "pass1234")
        self.client.force_login(self.user)

        self.article = Article.objects.create(
            reference="ART-NOUVEAU-DEVIS",
            nature=Article.Nature.MATIERE_PREMIERE,
            unite_cout=Article.UniteCout.PIECE,
            cout_unitaire=2.0,
            taux_marge_defaut=10,
        )

    def _url(self):
        return "/admin/chiffrage/devis/nouveau-devis/previsualiser-ligne/"

    def test_apercu_reussi_sans_aucun_devis_en_base(self):
        self.assertEqual(Devis.objects.count(), 0)
        response = self.client.post(
            self._url(),
            data={"article": "ART-NOUVEAU-DEVIS", "quantite": 5, "date_creation": "2026-01-01"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertEqual(data["cout_matiere_calcule"], 10)
        # marge par défaut de l'article : 10% -> 10 * 1.10 = 11
        self.assertAlmostEqual(data["prix_vente_matiere"], 11)
        # rien n'a été créé en base (ni Devis, ni DevisLigne)
        self.assertEqual(Devis.objects.count(), 0)
        self.assertEqual(DevisLigne.objects.count(), 0)

    def test_sans_date_creation_utilise_aujourdhui(self):
        response = self.client.post(
            self._url(),
            data={"article": "ART-NOUVEAU-DEVIS", "quantite": 1},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)

    def test_taux_marge_globale_ecrase_le_defaut(self):
        response = self.client.post(
            self._url(),
            data={
                "article": "ART-NOUVEAU-DEVIS",
                "quantite": 5,
                "date_creation": "2026-01-01",
                "taux_marge_globale": 50,
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        # 10 * 1.50 = 15, au lieu de la marge par défaut de l'article (10%)
        self.assertEqual(response.json()["prix_vente_matiere"], 15)

    def test_date_creation_invalide_400(self):
        response = self.client.post(
            self._url(),
            data={"article": "ART-NOUVEAU-DEVIS", "quantite": 1, "date_creation": "pas-une-date"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_date_creation_au_format_francais_jj_mm_aaaa(self):
        # Régression : le widget de date de l'admin (LANGUAGE_CODE="fr-fr")
        # soumet la date au format JJ/MM/AAAA, pas l'ISO strict AAAA-MM-JJ.
        response = self.client.post(
            self._url(),
            data={"article": "ART-NOUVEAU-DEVIS", "quantite": 5, "date_creation": "02/09/2026"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertAlmostEqual(response.json()["prix_vente_matiere"], 11)

    def test_article_introuvable_400(self):
        response = self.client.post(
            self._url(),
            data={"article": "INEXISTANT", "quantite": 1},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_article_sans_cout_unitaire_400(self):
        Article.objects.create(
            reference="ART-NOUVEAU-DEVIS-SANS-COUT",
            nature=Article.Nature.MATIERE_PREMIERE,
            unite_cout=Article.UniteCout.PIECE,
        )
        response = self.client.post(
            self._url(),
            data={"article": "ART-NOUVEAU-DEVIS-SANS-COUT", "quantite": 1},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_anonyme_refuse(self):
        self.client.logout()
        response = self.client.post(
            self._url(),
            data={"article": "ART-NOUVEAU-DEVIS", "quantite": 1},
            content_type="application/json",
        )
        self.assertNotEqual(response.status_code, 200)


class RecalculerLignePrixForceTests(TestCase):
    """recalculer_ligne_view honore aussi le prix unitaire forcé."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        self.user = User.objects.create_superuser("force-admin", "f@example.com", "pass1234")
        self.client.force_login(self.user)

        self.article = Article.objects.create(
            reference="ART-FORCE-TEST",
            nature=Article.Nature.MATIERE_PREMIERE,
            unite_cout=Article.UniteCout.PIECE,
            cout_unitaire=2.0,
        )
        client_tiers = Tiers.objects.create(
            code="CLI-FORCE-TEST", raison_sociale="Client Force Test", type_tiers=Tiers.TypeTiers.CLIENT
        )
        self.devis = Devis.objects.create(
            numero="DEV-FORCE-TEST",
            client=client_tiers,
            date_creation=datetime.date(2026, 1, 1),
            statut=Devis.Statut.BROUILLON,
        )
        self.ligne = DevisLigne.objects.create(devis=self.devis, article=self.article, quantite=3)

    def test_prix_force_via_recalcul_live(self):
        response = self.client.post(
            f"/admin/chiffrage/devis/{self.devis.pk}/lignes/{self.ligne.id}/recalculer/",
            data={"prix_vente_unitaire_force": 10},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        # cout_matiere_calcule = 3*2 = 6 (informatif) ; prix_vente_matiere = 3*10 = 30 (forcé)
        self.assertEqual(data["cout_matiere_calcule"], 6)
        self.assertEqual(data["prix_vente_matiere"], 30)
        self.ligne.refresh_from_db()
        self.assertEqual(self.ligne.prix_vente_unitaire_force, 10)

    def test_prix_force_vide_revient_au_calcul_automatique(self):
        self.ligne.prix_vente_unitaire_force = 10
        self.ligne.save()
        response = self.client.post(
            f"/admin/chiffrage/devis/{self.devis.pk}/lignes/{self.ligne.id}/recalculer/",
            data={"prix_vente_unitaire_force": ""},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        # revient au calcul auto : 6 * (1 + 0/100) = 6 (pas de marge par défaut sur l'article)
        self.assertEqual(response.json()["prix_vente_matiere"], 6)

    def test_prix_force_invalide_400(self):
        response = self.client.post(
            f"/admin/chiffrage/devis/{self.devis.pk}/lignes/{self.ligne.id}/recalculer/",
            data={"prix_vente_unitaire_force": "abc"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)


class TauxTvaEtPrixTtcTests(TestCase):
    """Taux de TVA par ligne (référentiel commercial.TauxTVA) et prix TTC."""

    def setUp(self):
        # "Taux normal" (20 %, par défaut) et "Taux réduit" (5.5 %) viennent de
        # la migration de données commercial/migrations/0005_seed_taux_tva.py.
        self.taux_normal = TauxTVA.objects.get(nom="Taux normal")
        self.taux_reduit = TauxTVA.objects.get(nom="Taux réduit")

        self.client_tiers = Tiers.objects.create(
            code="CLI-TVA", raison_sociale="Client TVA", type_tiers=Tiers.TypeTiers.CLIENT
        )
        self.devis = Devis.objects.create(
            numero="DEV-TVA",
            client=self.client_tiers,
            date_creation=datetime.date(2026, 1, 1),
            statut=Devis.Statut.BROUILLON,
        )
        self.article = Article.objects.create(
            reference="ART-TVA",
            nature=Article.Nature.MATIERE_PREMIERE,
            unite_cout=Article.UniteCout.PIECE,
            cout_unitaire=10.0,
        )

    def test_nouvelle_ligne_recoit_le_taux_par_defaut(self):
        ligne = DevisLigne.objects.create(devis=self.devis, article=self.article, quantite=1)
        self.assertEqual(ligne.taux_tva, self.taux_normal)

    def test_prix_vente_ttc_none_avant_calcul(self):
        ligne = DevisLigne(devis=self.devis, article=self.article, quantite=1, taux_tva=self.taux_normal)
        self.assertIsNone(ligne.prix_vente_ttc)

    def test_prix_vente_ttc_avec_taux(self):
        ligne = DevisLigne.objects.create(
            devis=self.devis, article=self.article, quantite=2, taux_tva=self.taux_reduit
        )
        calculer_devis(self.devis)
        ligne.refresh_from_db()
        # cout=2*10=20, pas de marge -> prix_vente_matiere=20 ; TTC = 20 * 1.055 = 21.1
        self.assertEqual(ligne.prix_vente_matiere, 20)
        self.assertAlmostEqual(ligne.prix_vente_ttc, 21.1, places=3)

    def test_prix_vente_ttc_sans_taux_egal_au_ht(self):
        ligne = DevisLigne.objects.create(
            devis=self.devis, article=self.article, quantite=2, taux_tva=None
        )
        calculer_devis(self.devis)
        ligne.refresh_from_db()
        self.assertEqual(ligne.prix_vente_ttc, ligne.prix_vente_total)

    def test_montant_total_ttc_avec_taux_mixtes(self):
        DevisLigne.objects.create(
            devis=self.devis, article=self.article, quantite=1, taux_tva=self.taux_normal
        )  # 10 HT -> 12 TTC
        DevisLigne.objects.create(
            devis=self.devis, article=self.article, quantite=2, taux_tva=self.taux_reduit
        )  # 20 HT -> 21.1 TTC
        calculer_devis(self.devis)
        self.assertAlmostEqual(self.devis.montant_total_ttc, 12 + 21.1, places=3)

    def test_previsualiser_ligne_inclut_le_ttc(self):
        resultat = previsualiser_ligne(self.devis, self.article, 3, taux_tva=self.taux_normal)
        # 3*10=30 HT -> 36 TTC
        self.assertEqual(resultat["prix_vente_total"], 30)
        self.assertEqual(resultat["prix_vente_ttc"], 36)

    def test_previsualiser_ligne_sans_taux_ttc_egal_ht(self):
        resultat = previsualiser_ligne(self.devis, self.article, 3)
        self.assertEqual(resultat["prix_vente_ttc"], resultat["prix_vente_total"])


class TauxTvaViewsTests(TestCase):
    """Les endpoints live (recalcul + aperçu) prennent en compte taux_tva."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        self.user = User.objects.create_superuser("tva-admin", "t@example.com", "pass1234")
        self.client.force_login(self.user)

        self.taux_normal = TauxTVA.objects.get(nom="Taux normal")
        self.taux_reduit = TauxTVA.objects.get(nom="Taux réduit")

        self.article = Article.objects.create(
            reference="ART-TVA-VIEW",
            nature=Article.Nature.MATIERE_PREMIERE,
            unite_cout=Article.UniteCout.PIECE,
            cout_unitaire=10.0,
        )
        client_tiers = Tiers.objects.create(
            code="CLI-TVA-VIEW", raison_sociale="Client TVA View", type_tiers=Tiers.TypeTiers.CLIENT
        )
        self.devis = Devis.objects.create(
            numero="DEV-TVA-VIEW",
            client=client_tiers,
            date_creation=datetime.date(2026, 1, 1),
            statut=Devis.Statut.BROUILLON,
        )
        self.ligne = DevisLigne.objects.create(
            devis=self.devis, article=self.article, quantite=2, taux_tva=self.taux_normal
        )

    def test_recalcul_change_le_taux_tva(self):
        response = self.client.post(
            f"/admin/chiffrage/devis/{self.devis.pk}/lignes/{self.ligne.id}/recalculer/",
            data={"taux_tva": self.taux_reduit.pk},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        # 2*10=20 HT -> 20*1.055=21.1 TTC
        self.assertAlmostEqual(data["prix_vente_ttc"], 21.1, places=3)
        self.assertAlmostEqual(data["montant_total_ttc"], 21.1, places=3)
        self.ligne.refresh_from_db()
        self.assertEqual(self.ligne.taux_tva, self.taux_reduit)

    def test_recalcul_taux_tva_vide_le_retire(self):
        response = self.client.post(
            f"/admin/chiffrage/devis/{self.devis.pk}/lignes/{self.ligne.id}/recalculer/",
            data={"taux_tva": ""},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.ligne.refresh_from_db()
        self.assertIsNone(self.ligne.taux_tva)

    def test_recalcul_taux_tva_introuvable_400(self):
        response = self.client.post(
            f"/admin/chiffrage/devis/{self.devis.pk}/lignes/{self.ligne.id}/recalculer/",
            data={"taux_tva": 999999},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_apercu_avec_taux_tva(self):
        response = self.client.post(
            f"/admin/chiffrage/devis/{self.devis.pk}/lignes/previsualiser/",
            data={"article": "ART-TVA-VIEW", "quantite": 3, "taux_tva": self.taux_normal.pk},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        # 3*10=30 HT -> 36 TTC
        self.assertEqual(data["prix_vente_ttc"], 36)


class AjoutDevisOuvrirConstructeurTests(TestCase):
    """Bouton "Enregistrer et ouvrir le constructeur" du formulaire d'ajout de
    devis : enregistre normalement le devis (et ses lignes déjà saisies dans
    l'inline), puis redirige vers le constructeur au lieu de la fiche/liste
    par défaut — pour pouvoir composer la suite du devis sans repasser par
    la fiche standard."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        self.user = User.objects.create_superuser("construire-admin", "c@example.com", "pass1234")
        self.client.force_login(self.user)

        self.client_tiers = Tiers.objects.create(
            code="CLI-CONSTRUIRE", raison_sociale="Client Construire", type_tiers=Tiers.TypeTiers.CLIENT
        )
        self.article = Article.objects.create(
            reference="ART-CONSTRUIRE",
            nature=Article.Nature.MATIERE_PREMIERE,
            unite_cout=Article.UniteCout.PIECE,
            cout_unitaire=2.0,
        )

    def _formulaire_de_base(self):
        return {
            "numero": "DEV-CONSTRUIRE-01",
            "client": self.client_tiers.pk,
            "date_creation": "2026-01-01",
            "statut": Devis.Statut.BROUILLON,
            "taux_marge_globale": "",
            "adresse_facturation": "",
            "adresse_livraison": "",
            "contact": "",
            "lignes-TOTAL_FORMS": "1",
            "lignes-INITIAL_FORMS": "0",
            "lignes-MIN_NUM_FORMS": "0",
            "lignes-MAX_NUM_FORMS": "1000",
            "lignes-0-article": self.article.pk,
            "lignes-0-quantite": "4",
            "lignes-0-taux_marge_matiere_applique": "",
            "lignes-0-prix_vente_unitaire_force": "",
            "lignes-0-taux_tva": "",
            "lignes-0-id": "",
            "lignes-0-devis": "",
            "_construire": "Enregistrer et ouvrir le constructeur",
        }

    def test_redirige_vers_le_constructeur_apres_enregistrement(self):
        response = self.client.post(
            "/admin/chiffrage/devis/add/", data=self._formulaire_de_base(), follow=False
        )
        self.assertEqual(response.status_code, 302, getattr(response, "context", None))
        self.assertEqual(response.url, "/admin/chiffrage/devis/DEV-CONSTRUIRE-01/constructeur/")

        devis = Devis.objects.get(pk="DEV-CONSTRUIRE-01")
        self.assertEqual(devis.lignes.count(), 1)
        ligne = devis.lignes.first()
        self.assertEqual(ligne.article, self.article)
        self.assertEqual(ligne.quantite, 4)

    def test_sans_bouton_construire_comportement_par_defaut_inchange(self):
        data = self._formulaire_de_base()
        del data["_construire"]
        data["_save"] = "Enregistrer"
        response = self.client.post("/admin/chiffrage/devis/add/", data=data, follow=False)
        self.assertEqual(response.status_code, 302)
        self.assertNotEqual(response.url, "/admin/chiffrage/devis/DEV-CONSTRUIRE-01/constructeur/")


class ValeursDefautTiersViewTests(TestCase):
    """Endpoint GET .../tiers/<code>/valeurs-defaut/ : adresse de facturation,
    adresse de livraison et contact marqués "principal(e)" pour un tiers —
    utilisé pour pré-remplir automatiquement ces champs sur la fiche Devis
    dès qu'un client est sélectionné."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        self.user = User.objects.create_superuser("defaut-tiers-admin", "d@example.com", "pass1234")
        self.client.force_login(self.user)

        self.tiers = Tiers.objects.create(
            code="CLI-DEFAUT-TIERS", raison_sociale="Client Défaut Tiers", type_tiers=Tiers.TypeTiers.CLIENT
        )

    def _url(self, code=None):
        return f"/admin/chiffrage/devis/tiers/{code or self.tiers.pk}/valeurs-defaut/"

    def test_aucune_valeur_par_defaut_renvoie_des_null(self):
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertIsNone(data["adresse_facturation"])
        self.assertIsNone(data["adresse_livraison"])
        self.assertIsNone(data["contact"])

    def test_adresses_et_contact_principaux_renvoyes(self):
        facturation = Adresse.objects.create(
            tiers=self.tiers,
            type_adresse=Adresse.TypeAdresse.FACTURATION,
            adresse="1 rue de la Facture",
            code_postal="75000",
            ville="Paris",
            est_principale=True,
        )
        # Une adresse de livraison non principale ne doit jamais être renvoyée.
        Adresse.objects.create(
            tiers=self.tiers,
            type_adresse=Adresse.TypeAdresse.LIVRAISON,
            adresse="2 rue Secondaire",
            code_postal="75000",
            ville="Paris",
            est_principale=False,
        )
        livraison = Adresse.objects.create(
            tiers=self.tiers,
            type_adresse=Adresse.TypeAdresse.LIVRAISON,
            adresse="3 rue de la Livraison",
            code_postal="75000",
            ville="Paris",
            est_principale=True,
        )
        contact = Contact.objects.create(
            tiers=self.tiers, nom="Dupont", prenom="Jean", est_principal=True
        )

        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertEqual(data["adresse_facturation"]["id"], facturation.pk)
        self.assertEqual(data["adresse_livraison"]["id"], livraison.pk)
        self.assertEqual(data["contact"]["id"], contact.pk)

    def test_tiers_introuvable_404(self):
        response = self.client.get(self._url(code="INEXISTANT"))
        self.assertEqual(response.status_code, 404)

    def test_anonyme_refuse(self):
        self.client.logout()
        response = self.client.get(self._url())
        self.assertNotEqual(response.status_code, 200)

    def test_contact_associe_a_l_adresse_de_livraison_prioritaire(self):
        # Le contact associé à l'adresse de livraison par défaut doit être
        # préféré au contact principal du tiers, s'il y en a un.
        livraison = Adresse.objects.create(
            tiers=self.tiers,
            type_adresse=Adresse.TypeAdresse.LIVRAISON,
            adresse="Site Nord",
            code_postal="59000",
            ville="Lille",
            est_principale=True,
        )
        Contact.objects.create(tiers=self.tiers, nom="Principal Tiers", est_principal=True)
        contact_site = Contact.objects.create(
            tiers=self.tiers, nom="Contact Site", adresse_livraison=livraison
        )

        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["contact"]["id"], contact_site.pk)

    def test_contact_replie_sur_le_principal_du_tiers_si_aucun_lie_a_l_adresse(self):
        Adresse.objects.create(
            tiers=self.tiers,
            type_adresse=Adresse.TypeAdresse.LIVRAISON,
            adresse="Site Sud",
            code_postal="13000",
            ville="Marseille",
            est_principale=True,
        )
        principal = Contact.objects.create(tiers=self.tiers, nom="Principal Tiers", est_principal=True)

        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["contact"]["id"], principal.pk)


class ContactAssocieAdresseViewTests(TestCase):
    """Endpoint GET .../adresses/<id>/contact-associe/ : contact associé à
    une adresse de livraison précise (Contact.adresse_livraison) — utilisé
    quand l'utilisateur change l'adresse de livraison d'un devis après
    coup, indépendamment de la sélection du client."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        self.user = User.objects.create_superuser("contact-adresse-admin", "ca@example.com", "pass1234")
        self.client.force_login(self.user)

        self.tiers = Tiers.objects.create(
            code="CLI-CONTACT-ASSOCIE", raison_sociale="Client Contact Associé", type_tiers=Tiers.TypeTiers.CLIENT
        )
        self.livraison = Adresse.objects.create(
            tiers=self.tiers,
            type_adresse=Adresse.TypeAdresse.LIVRAISON,
            adresse="Site Est",
            code_postal="67000",
            ville="Strasbourg",
        )

    def _url(self, adresse_id=None):
        return f"/admin/chiffrage/devis/adresses/{adresse_id or self.livraison.pk}/contact-associe/"

    def test_aucun_contact_associe_renvoie_null(self):
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIsNone(response.json()["contact"])

    def test_contact_associe_renvoye(self):
        contact = Contact.objects.create(tiers=self.tiers, nom="Site Est", adresse_livraison=self.livraison)
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["contact"]["id"], contact.pk)

    def test_adresse_introuvable_404(self):
        response = self.client.get(self._url(adresse_id=999999))
        self.assertEqual(response.status_code, 404)

    def test_anonyme_refuse(self):
        self.client.logout()
        response = self.client.get(self._url())
        self.assertNotEqual(response.status_code, 200)


class ContactEstPrincipalTests(TestCase):
    """Contact.est_principal suit le même garde-fou "un seul par tiers" que
    Adresse.est_principale (voir commercial.models.Adresse.clean)."""

    def setUp(self):
        self.tiers = Tiers.objects.create(
            code="CLI-CONTACT-PRINCIPAL", raison_sociale="Client Contact Principal", type_tiers=Tiers.TypeTiers.CLIENT
        )

    def test_un_seul_contact_principal_par_tiers(self):
        Contact.objects.create(tiers=self.tiers, nom="Premier", est_principal=True)
        second = Contact(tiers=self.tiers, nom="Second", est_principal=True)
        with self.assertRaises(ValidationError):
            second.full_clean()

    def test_deux_tiers_differents_peuvent_chacun_avoir_un_contact_principal(self):
        autre_tiers = Tiers.objects.create(
            code="CLI-CONTACT-PRINCIPAL-2", raison_sociale="Autre Client", type_tiers=Tiers.TypeTiers.CLIENT
        )
        Contact.objects.create(tiers=self.tiers, nom="Premier", est_principal=True)
        autre = Contact(tiers=autre_tiers, nom="Second", est_principal=True)
        autre.full_clean()  # ne doit pas lever
