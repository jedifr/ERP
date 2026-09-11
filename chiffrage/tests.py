import datetime
import json
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from commercial.models import Adresse, Contact, DelaiPropose, Devise, TauxTVA, Tiers
from stock.models import Emplacement, Lot, MouvementStock
from technique.models import Article, Gamme, Matiere, Nomenclature, PosteTravail, TarifPoste

from .builder import ajouter_ligne_devis, creer_article_fabrique
from .models import (
    Commande,
    CommandeLigne,
    CommandeLigneModification,
    Devis,
    DevisLigne,
    DevisLigneOperation,
    Livraison,
    LivraisonError,
    LivraisonLigne,
    OrdreFabrication,
)
from .moteur import (
    ChiffrageError,
    calculer_devis,
    calculer_ligne,
    cout_matiere_article,
    previsualiser_ligne,
    previsualiser_ligne_commande,
    resoudre_taux_tva,
)
from .planning_sync import PlanningSyncError, resynchroniser, tenter_synchronisation
from .production import lancer_en_production, lancer_ligne_en_production, synchroniser_lignes_commande


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

    def test_cout_natures_achetees_directes(self):
        # Service acheté, consommable, composant : achetés tels quels (comme
        # une matière première), pas décomposés via une nomenclature.
        for nature in (Article.Nature.SERVICE_ACHETE, Article.Nature.CONSOMMABLE, Article.Nature.COMPOSANT):
            article = Article.objects.create(
                reference=f"ART-{nature}", nature=nature, unite_cout=Article.UniteCout.PIECE, cout_unitaire=3.0
            )
            self.assertAlmostEqual(cout_matiere_article(article, 4), 12.0)


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
        # temps_fixe/temps_variable sont en MINUTES : (10 + 5*3) = 25 min,
        # converties en heures avant le tarif horaire -> 25/60 * 50 = 20.8333...
        self.assertAlmostEqual(operation.cout_calcule, 25 / 60 * 50)
        self.assertEqual(operation.taux_marge_applique, 15)
        self.assertAlmostEqual(operation.prix_vente, 25 / 60 * 50 * 1.15)

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
        # 25 min converties en heures -> 25/60 * 60 = 25
        self.assertAlmostEqual(operation.cout_calcule, 25 / 60 * 60)

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
        # matière : 111.426 * 1.2 = 133.7112 ; opération : 25/60*50 * 1.15 = 23.9583...
        operation_attendue = 25 / 60 * 50 * 1.15
        self.assertAlmostEqual(self.ligne.prix_vente_operations, operation_attendue)
        self.assertAlmostEqual(self.ligne.prix_vente_total, 133.7112 + operation_attendue, places=3)

    def test_prix_vente_total_ligne_none_si_matiere_non_calculee(self):
        self.assertIsNone(self.ligne.prix_vente_matiere)
        self.assertIsNone(self.ligne.prix_vente_total)

    def test_prix_vente_unitaire_ramene_le_total_a_une_unite(self):
        calculer_devis(self.devis)
        self.ligne.refresh_from_db()
        # quantite=3 ; prix_vente_unitaire = prix_vente_total / 3
        self.assertAlmostEqual(self.ligne.prix_vente_unitaire, self.ligne.prix_vente_total / 3)

    def test_prix_vente_unitaire_none_si_matiere_non_calculee(self):
        self.assertIsNone(self.ligne.prix_vente_unitaire)

    def test_montants_devis_integrent_matiere_et_operations(self):
        calculer_devis(self.devis)
        operation_attendue = 25 / 60 * 50 * 1.15
        self.assertAlmostEqual(self.devis.montant_matiere_ht, 133.7112, places=3)
        self.assertAlmostEqual(self.devis.montant_operations_ht, operation_attendue)
        self.assertAlmostEqual(self.devis.montant_total_ht, 133.7112 + operation_attendue, places=3)


class ResoudreTauxTvaTests(TestCase):
    """Taux de TVA = combinaison Article/Client (signalé par l'utilisateur) :
    un client soumis à la TVA française applique le taux "normal" de
    l'article (ou le taux par défaut du référentiel s'il n'en a pas), un
    client exonéré/intracommunautaire/hors UE applique toujours 0 %."""

    def setUp(self):
        self.taux_normal = TauxTVA.objects.create(nom="Taux normal Résoudre", taux=20, est_defaut=False)
        self.taux_reduit = TauxTVA.objects.create(nom="Taux réduit Résoudre", taux=5.5)
        self.article_sans_taux = Article.objects.create(
            reference="ART-TVA-SANS", nature=Article.Nature.MATIERE_PREMIERE
        )
        self.article_avec_taux = Article.objects.create(
            reference="ART-TVA-AVEC", nature=Article.Nature.MATIERE_PREMIERE, taux_tva=self.taux_reduit
        )

    def _client(self, regime_fiscal, code):
        return Tiers.objects.create(
            code=code, raison_sociale=f"Client {regime_fiscal}",
            type_tiers=Tiers.TypeTiers.CLIENT, regime_fiscal=regime_fiscal,
        )

    def test_client_france_sans_taux_article_prend_le_defaut_referentiel(self):
        client = self._client(Tiers.RegimeFiscal.FRANCE, "CLI-TVA-1")
        taux = resoudre_taux_tva(self.article_sans_taux, client)
        self.assertEqual(taux, TauxTVA.objects.get(est_defaut=True))

    def test_client_france_avec_taux_article_prend_celui_de_larticle(self):
        client = self._client(Tiers.RegimeFiscal.FRANCE, "CLI-TVA-2")
        taux = resoudre_taux_tva(self.article_avec_taux, client)
        self.assertEqual(taux, self.taux_reduit)

    def test_client_france_exoneree_taux_zero(self):
        client = self._client(Tiers.RegimeFiscal.FRANCE_EXONERE, "CLI-TVA-3")
        taux = resoudre_taux_tva(self.article_avec_taux, client)
        self.assertEqual(taux.taux, 0)

    def test_client_intra_ue_taux_zero(self):
        client = self._client(Tiers.RegimeFiscal.INTRA_UE, "CLI-TVA-4")
        taux = resoudre_taux_tva(self.article_avec_taux, client)
        self.assertEqual(taux.taux, 0)

    def test_client_hors_ue_taux_zero(self):
        client = self._client(Tiers.RegimeFiscal.HORS_UE, "CLI-TVA-5")
        taux = resoudre_taux_tva(self.article_avec_taux, client)
        self.assertEqual(taux.taux, 0)


class PrevisualiserLigneCommandeTests(TestCase):
    """Prix d'une ligne de commande calculé depuis quantité/gamme/nomenclature
    (signalé par l'utilisateur) — mêmes règles que le devis, mais toujours
    avec les marges par défaut (CommandeLigne n'a pas de surcharge de marge)."""

    def setUp(self):
        self.matiere = Article.objects.create(
            reference="MP-CDE-CALC", nature=Article.Nature.MATIERE_PREMIERE,
            unite_cout=Article.UniteCout.PIECE, cout_unitaire=10, taux_marge_defaut=20,
        )
        self.fabrique = Article.objects.create(
            reference="FAB-CDE-CALC", nature=Article.Nature.FABRIQUE, taux_marge_defaut=25,
        )
        Nomenclature.objects.create(article_parent=self.fabrique, article_composant=self.matiere, quantite=2)
        self.poste = PosteTravail.objects.create(
            nom="Poste-CDE-CALC", mode_calcul=PosteTravail.ModeCalcul.HORAIRE, taux_marge_defaut=10,
        )
        TarifPoste.objects.create(poste=self.poste, cout_horaire=60, date_debut=datetime.date(2020, 1, 1))
        Gamme.objects.create(
            article=self.fabrique, poste=self.poste, ordre=1,
            temps_fixe=0, temps_variable=6, date_debut=datetime.date(2020, 1, 1),
        )

    def test_article_matiere_premiere(self):
        # coût = 10 * 3 = 30 ; prix = 30 * 1.20 = 36 ; unitaire = 36 / 3 = 12
        resultat = previsualiser_ligne_commande(self.matiere, 3, datetime.date(2026, 1, 1))
        self.assertAlmostEqual(resultat["montant_ht"], 36)
        self.assertAlmostEqual(resultat["prix_vente_unitaire"], 12)

    def test_article_fabrique_matiere_et_operations(self):
        # matière : coût = (2*10)*2 = 40 ; prix = 40*1.25 = 50
        # opération : 6 min/pièce * 2 pièces = 12 min -> 12/60*60 = 12 ; prix = 12*1.10 = 13.2
        resultat = previsualiser_ligne_commande(self.fabrique, 2, datetime.date(2026, 1, 1))
        self.assertAlmostEqual(resultat["montant_ht"], 50 + 13.2)
        self.assertAlmostEqual(resultat["prix_vente_unitaire"], (50 + 13.2) / 2)

    def test_quantite_nulle_prix_unitaire_none(self):
        resultat = previsualiser_ligne_commande(self.matiere, 0, datetime.date(2026, 1, 1))
        self.assertIsNone(resultat["prix_vente_unitaire"])


class LancerEnProductionTests(TestCase):
    def setUp(self):
        self.client_tiers = Tiers.objects.create(
            code="CLI-002", raison_sociale="Client Prod", type_tiers=Tiers.TypeTiers.CLIENT
        )
        Adresse.objects.create(
            tiers=self.client_tiers,
            est_facturation=True,
            adresse="1 rue A",
            code_postal="75000",
            ville="Paris",
            est_principale=True,
        )
        Adresse.objects.create(
            tiers=self.client_tiers,
            est_livraison=True,
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

    def test_cree_une_ligne_de_commande_par_ligne_de_devis(self):
        # Une CommandeLigne par ligne de devis, quelle que soit la nature de
        # l'article (contrairement aux OF, qui ne concernent que le FABRIQUE) —
        # c'est elle qui porte le suivi de livraison.
        commande = lancer_en_production(self.devis)
        self.assertEqual(commande.lignes.count(), 2)

        ligne_fabrique = commande.lignes.get(article=self.article_fabrique)
        self.assertEqual(ligne_fabrique.quantite_commandee, 2)
        self.assertEqual(ligne_fabrique.quantite_livree, 0)
        self.assertEqual(ligne_fabrique.reliquat, 2)
        self.assertFalse(ligne_fabrique.entierement_livree)

        ligne_mp = commande.lignes.get(article=self.article_mp)
        self.assertEqual(ligne_mp.quantite_commandee, 50)

    def test_devise_de_la_commande_reprend_celle_du_client(self):
        eur = Devise.objects.get(code="EUR")
        self.client_tiers.devise = eur
        self.client_tiers.save()
        commande = lancer_en_production(self.devis)
        self.assertEqual(commande.devise, eur)

    def test_devise_none_si_client_sans_devise(self):
        commande = lancer_en_production(self.devis)
        self.assertIsNone(commande.devise)

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
        Adresse.objects.filter(tiers=self.client_tiers, est_livraison=True).delete()
        with self.assertRaises(ChiffrageError):
            lancer_en_production(self.devis)

    def test_ordre_des_lignes_de_commande_suit_celui_des_lignes_de_devis(self):
        # Signalé par l'utilisateur : l'ordre des lignes doit être préservé
        # à la conversion en commande — pas seulement l'ordre de création
        # des DevisLigne (créées ici mp puis fabrique, ordre 0/1 inversé
        # ensuite pour simuler un glisser-déposer sur la fiche devis).
        ligne_fabrique = self.devis.lignes.get(article=self.article_fabrique)
        ligne_mp = self.devis.lignes.get(article=self.article_mp)
        ligne_fabrique.ordre = 1
        ligne_fabrique.save(update_fields=["ordre"])
        ligne_mp.ordre = 0
        ligne_mp.save(update_fields=["ordre"])

        self.assertEqual(list(self.devis.lignes.all()), [ligne_mp, ligne_fabrique])

        commande = lancer_en_production(self.devis)
        self.assertEqual(
            [ligne.article for ligne in commande.lignes.all()],
            [self.article_mp, self.article_fabrique],
        )

    def test_ligne_de_commande_reliee_a_sa_ligne_de_devis(self):
        # Sert à afficher prix de vente unitaire / taux de TVA / montants sur
        # la commande sans les dupliquer (voir CommandeLigne.taux_tva etc.).
        commande = lancer_en_production(self.devis)
        ligne_devis_fabrique = self.devis.lignes.get(article=self.article_fabrique)
        ligne_commande = commande.lignes.get(article=self.article_fabrique)
        self.assertEqual(ligne_commande.devis_ligne, ligne_devis_fabrique)
        self.assertEqual(ligne_commande.taux_tva, ligne_devis_fabrique.taux_tva)
        self.assertEqual(ligne_commande.prix_vente_unitaire, ligne_devis_fabrique.prix_vente_unitaire)
        self.assertEqual(ligne_commande.montant_ht, ligne_devis_fabrique.prix_vente_total)
        self.assertEqual(ligne_commande.montant_ttc, ligne_devis_fabrique.prix_vente_ttc)


class ReordonnerLignesDevisAdminTests(TestCase):
    """DevisLigne.ordre (glisser-déposer côté JS, devisligne_reorder.js) :
    régression Python de bout en bout sur le POST équivalent à un
    réordonnancement — la ligne "extra" (jamais remplie) de l'inline ne
    doit jamais recevoir d'ordre, sous peine d'être considérée comme
    modifiée par Django et donc exiger d'être intégralement remplie
    (article, quantité), ce qui faisait échouer tout l'enregistrement."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        self.user = User.objects.create_superuser("reorder-admin", "reorder@example.com", "pass1234")
        self.client.force_login(self.user)

        self.tiers = Tiers.objects.create(
            code="CLI-REORDER", raison_sociale="Client Reorder", type_tiers=Tiers.TypeTiers.CLIENT
        )
        self.article = Article.objects.create(
            reference="ART-REORDER",
            nature=Article.Nature.MATIERE_PREMIERE,
            unite_cout=Article.UniteCout.PIECE,
            cout_unitaire=1.0,
        )
        self.devis = Devis.objects.create(
            numero="DEV-REORDER",
            client=self.tiers,
            date_creation=datetime.date(2026, 1, 1),
            statut=Devis.Statut.BROUILLON,
        )
        self.ligne_a = DevisLigne.objects.create(devis=self.devis, article=self.article, quantite=1, ordre=0)
        self.ligne_b = DevisLigne.objects.create(devis=self.devis, article=self.article, quantite=2, ordre=1)

    def _payload(self, ordre_a, ordre_b):
        # Reproduit exactement ce qu'envoie le navigateur après un
        # glisser-déposer (voir devisligne_reorder.js : renumeroter() ne
        # touche jamais la ligne "extra" lignes-2-* tant qu'aucun article
        # n'y est choisi — son champ ordre reste donc vide ici aussi).
        return {
            "numero": "DEV-REORDER",
            "client": self.tiers.pk,
            "date_creation": "01/01/2026",
            "statut": Devis.Statut.BROUILLON,
            "taux_marge_globale": "",
            "adresse_facturation": "",
            "adresse_livraison": "",
            "contact": "",
            "delai": "",
            "lignes-TOTAL_FORMS": "3",
            "lignes-INITIAL_FORMS": "2",
            "lignes-MIN_NUM_FORMS": "0",
            "lignes-MAX_NUM_FORMS": "1000",
            "lignes-0-id": str(self.ligne_a.pk),
            "lignes-0-devis": "DEV-REORDER",
            "lignes-0-ordre": str(ordre_a),
            "lignes-0-article": self.article.pk,
            "lignes-0-quantite": "1.0",
            "lignes-0-taux_marge_matiere_applique": "",
            "lignes-0-prix_vente_unitaire_force": "",
            "lignes-0-taux_tva": "",
            "lignes-1-id": str(self.ligne_b.pk),
            "lignes-1-devis": "DEV-REORDER",
            "lignes-1-ordre": str(ordre_b),
            "lignes-1-article": self.article.pk,
            "lignes-1-quantite": "2.0",
            "lignes-1-taux_marge_matiere_applique": "",
            "lignes-1-prix_vente_unitaire_force": "",
            "lignes-1-taux_tva": "",
            # Ligne "extra" jamais touchée : ordre laissé vide, comme le
            # ferait devisligne_reorder.js.
            "lignes-2-ordre": "",
            "lignes-2-quantite": "",
            "lignes-2-taux_marge_matiere_applique": "",
            "lignes-2-prix_vente_unitaire_force": "",
            "lignes-2-taux_tva": "",
            "_continue": "Enregistrer et continuer les modifications",
        }

    def test_inversion_de_deux_lignes_persiste_sans_toucher_la_ligne_vide(self):
        payload = self._payload(ordre_a=1, ordre_b=0)
        response = self.client.post(
            f"/admin/chiffrage/devis/{self.devis.pk}/change/", data=payload, follow=False
        )
        self.assertEqual(response.status_code, 302, getattr(response, "context", None))

        self.ligne_a.refresh_from_db()
        self.ligne_b.refresh_from_db()
        self.assertEqual(self.ligne_a.ordre, 1)
        self.assertEqual(self.ligne_b.ordre, 0)
        self.assertEqual(list(self.devis.lignes.all()), [self.ligne_b, self.ligne_a])


class CommandeModelTests(TestCase):
    """Commande.devis facultatif + Commande.client/reference_client
    (signalé par l'utilisateur : pouvoir créer une commande directement,
    sans devis d'origine)."""

    def setUp(self):
        self.tiers = Tiers.objects.create(
            code="CLI-CDE-MODEL", raison_sociale="Client Commande Modèle", type_tiers=Tiers.TypeTiers.CLIENT
        )
        self.adresse = Adresse.objects.create(
            tiers=self.tiers, est_facturation=True, est_livraison=True,
            adresse="1 rue", code_postal="75000", ville="Paris",
        )
        self.autre_tiers = Tiers.objects.create(
            code="CLI-CDE-AUTRE", raison_sociale="Autre Client", type_tiers=Tiers.TypeTiers.CLIENT
        )
        self.adresse_autre_tiers = Adresse.objects.create(
            tiers=self.autre_tiers, est_facturation=True,
            adresse="2 rue", code_postal="75000", ville="Paris",
        )

    def test_creation_sans_devis(self):
        commande = Commande.objects.create(
            numero="CDE-SANS-DEVIS",
            client=self.tiers,
            reference_client="PO-12345",
            date_commande=datetime.date(2026, 1, 1),
            adresse_facturation=self.adresse,
            adresse_livraison=self.adresse,
        )
        self.assertIsNone(commande.devis)

    def test_client_requis(self):
        commande = Commande(
            numero="CDE-SANS-CLIENT",
            reference_client="PO-1",
            date_commande=datetime.date(2026, 1, 1),
            adresse_facturation=self.adresse,
            adresse_livraison=self.adresse,
        )
        with self.assertRaises(ValidationError):
            commande.full_clean()

    def test_reference_client_requise(self):
        commande = Commande(
            numero="CDE-SANS-REF",
            client=self.tiers,
            date_commande=datetime.date(2026, 1, 1),
            adresse_facturation=self.adresse,
            adresse_livraison=self.adresse,
        )
        with self.assertRaises(ValidationError):
            commande.full_clean()

    def test_adresse_facturation_dun_autre_client_refusee(self):
        commande = Commande(
            numero="CDE-ADR-AUTRE",
            client=self.tiers,
            reference_client="PO-2",
            date_commande=datetime.date(2026, 1, 1),
            adresse_facturation=self.adresse_autre_tiers,
            adresse_livraison=self.adresse,
        )
        with self.assertRaises(ValidationError):
            commande.full_clean()

    def test_adresse_livraison_dun_autre_client_refusee(self):
        commande = Commande(
            numero="CDE-ADR-AUTRE-2",
            client=self.tiers,
            reference_client="PO-3",
            date_commande=datetime.date(2026, 1, 1),
            adresse_facturation=self.adresse,
            adresse_livraison=self.adresse_autre_tiers,
        )
        with self.assertRaises(ValidationError):
            commande.full_clean()


class CommandeLigneSansDevisLigneTests(TestCase):
    def test_proprietes_a_none_sans_devis_ligne(self):
        client_tiers = Tiers.objects.create(code="CLI-SANSDL", raison_sociale="Sans DL")
        article = Article.objects.create(reference="ART-SANSDL", nature=Article.Nature.MATIERE_PREMIERE)
        adresse = Adresse.objects.create(
            tiers=client_tiers, est_facturation=True,
            adresse="1 rue A", code_postal="75000", ville="Paris",
        )
        devis = Devis.objects.create(
            numero="DEV-SANSDL", client=client_tiers, date_creation=datetime.date(2026, 1, 1),
        )
        commande = Commande.objects.create(
            numero="CDE-SANSDL", devis=devis, client=devis.client, date_commande=datetime.date(2026, 1, 1),
            adresse_facturation=adresse, adresse_livraison=adresse,
        )
        ligne = CommandeLigne.objects.create(commande=commande, article=article, quantite_commandee=1)
        self.assertIsNone(ligne.devis_ligne)
        self.assertIsNone(ligne.taux_tva)
        self.assertIsNone(ligne.prix_vente_unitaire)
        self.assertIsNone(ligne.montant_ht)
        self.assertIsNone(ligne.montant_ttc)


class SynchroniserLignesCommandeTests(TestCase):
    """synchroniser_lignes_commande() : filet de sécurité pour une commande
    créée avant l'ajout de CommandeLigne.devis_ligne, ou dont une ligne de
    commande manquerait par rapport au devis d'origine (le bug remonté :
    "Lignes de commande" vide sur une commande existante)."""

    def setUp(self):
        self.client_tiers = Tiers.objects.create(code="CLI-SYNC", raison_sociale="Client Sync")
        self.adresse = Adresse.objects.create(
            tiers=self.client_tiers, est_facturation=True,
            adresse="1 rue A", code_postal="75000", ville="Paris",
        )
        self.article = Article.objects.create(reference="ART-SYNC", nature=Article.Nature.MATIERE_PREMIERE)
        self.devis = Devis.objects.create(
            numero="DEV-SYNC", client=self.client_tiers, date_creation=datetime.date(2026, 1, 1),
        )
        self.devis_ligne = DevisLigne.objects.create(devis=self.devis, article=self.article, quantite=5)
        self.commande = Commande.objects.create(
            numero="CDE-SYNC", devis=self.devis, client=self.devis.client, date_commande=datetime.date(2026, 1, 1),
            adresse_facturation=self.adresse, adresse_livraison=self.adresse,
        )

    def test_recree_une_ligne_manquante(self):
        self.assertEqual(self.commande.lignes.count(), 0)
        creees = synchroniser_lignes_commande(self.commande)
        self.assertEqual(len(creees), 1)
        ligne = self.commande.lignes.get()
        self.assertEqual(ligne.article, self.article)
        self.assertEqual(ligne.quantite_commandee, 5)
        self.assertEqual(ligne.devis_ligne, self.devis_ligne)

    def test_relie_devis_ligne_sur_une_ligne_existante_non_reliee(self):
        ligne = CommandeLigne.objects.create(
            commande=self.commande, article=self.article, quantite_commandee=5
        )
        creees = synchroniser_lignes_commande(self.commande)
        self.assertEqual(creees, [])
        ligne.refresh_from_db()
        self.assertEqual(ligne.devis_ligne, self.devis_ligne)

    def test_idempotent_ne_duplique_rien(self):
        synchroniser_lignes_commande(self.commande)
        creees = synchroniser_lignes_commande(self.commande)
        self.assertEqual(creees, [])
        self.assertEqual(self.commande.lignes.count(), 1)

    def test_ne_touche_pas_une_quantite_deja_divergente(self):
        # Une ligne existante avec une quantité différente du devis (décision
        # manuelle assumée) n'est jamais réécrite par la synchronisation.
        ligne = CommandeLigne.objects.create(
            commande=self.commande, article=self.article, quantite_commandee=3
        )
        synchroniser_lignes_commande(self.commande)
        ligne.refresh_from_db()
        self.assertEqual(ligne.quantite_commandee, 3)


class LivraisonPartielleTests(TestCase):
    """Livraison partielle d'une commande, article par article : la quantité
    livrée peut être inférieure à la quantité commandée (reliquat), et
    plusieurs livraisons successives peuvent compléter une même ligne —
    même principe que achats.ReceptionLigne côté réception fournisseur."""

    def setUp(self):
        self.client_tiers = Tiers.objects.create(
            code="CLI-LIV", raison_sociale="Client Livraison", type_tiers=Tiers.TypeTiers.CLIENT
        )
        for champ_type in ["est_facturation", "est_livraison"]:
            Adresse.objects.create(
                tiers=self.client_tiers,
                adresse="1 rue",
                code_postal="75000",
                ville="Paris",
                est_principale=True,
                **{champ_type: True},
            )

        self.article = Article.objects.create(
            reference="VIS-LIV",
            nature=Article.Nature.MATIERE_PREMIERE,
            unite_cout=Article.UniteCout.PIECE,
            cout_unitaire=1.0,
            gere_en_stock=True,
        )
        self.devis = Devis.objects.create(
            numero="DEV-LIV",
            client=self.client_tiers,
            date_creation=datetime.date(2026, 1, 1),
            statut=Devis.Statut.VALIDE,
        )
        DevisLigne.objects.create(devis=self.devis, article=self.article, quantite=10)
        self.commande = lancer_en_production(self.devis)
        self.commande_ligne = self.commande.lignes.get(article=self.article)

    def _livraison(self, numero="LIV-1"):
        return Livraison.objects.create(numero=numero, commande=self.commande, date_livraison=datetime.date(2026, 2, 1))

    def test_livraison_partielle_laisse_un_reliquat(self):
        livraison = self._livraison()
        LivraisonLigne.objects.create(livraison=livraison, commande_ligne=self.commande_ligne, quantite_livree=6)

        self.commande_ligne.refresh_from_db()
        self.assertEqual(self.commande_ligne.quantite_livree, 6)
        self.assertEqual(self.commande_ligne.reliquat, 4)
        self.assertFalse(self.commande_ligne.entierement_livree)

    def test_deux_livraisons_successives_completent_la_commande(self):
        livraison1 = self._livraison("LIV-1")
        LivraisonLigne.objects.create(livraison=livraison1, commande_ligne=self.commande_ligne, quantite_livree=6)

        livraison2 = self._livraison("LIV-2")
        LivraisonLigne.objects.create(livraison=livraison2, commande_ligne=self.commande_ligne, quantite_livree=4)

        self.commande_ligne.refresh_from_db()
        self.assertEqual(self.commande_ligne.quantite_livree, 10)
        self.assertEqual(self.commande_ligne.reliquat, 0)
        self.assertTrue(self.commande_ligne.entierement_livree)

    def test_depasser_la_quantite_commandee_refuse(self):
        livraison = self._livraison()
        ligne = LivraisonLigne(livraison=livraison, commande_ligne=self.commande_ligne, quantite_livree=11)
        with self.assertRaises(ValidationError):
            ligne.full_clean()

    def test_deuxieme_livraison_qui_depasse_le_reliquat_refusee(self):
        livraison1 = self._livraison("LIV-1")
        LivraisonLigne.objects.create(livraison=livraison1, commande_ligne=self.commande_ligne, quantite_livree=6)
        self.commande_ligne.refresh_from_db()  # quantite_livree mise à jour en base via .update(), pas en mémoire

        livraison2 = self._livraison("LIV-2")
        ligne = LivraisonLigne(livraison=livraison2, commande_ligne=self.commande_ligne, quantite_livree=5)
        with self.assertRaises(ValidationError):
            ligne.full_clean()  # reliquat = 4, on tente d'en livrer 5

    def test_quantite_livree_negative_ou_nulle_refusee(self):
        livraison = self._livraison()
        ligne = LivraisonLigne(livraison=livraison, commande_ligne=self.commande_ligne, quantite_livree=0)
        with self.assertRaises(ValidationError):
            ligne.full_clean()

    def test_livraison_decremente_le_lot_de_stock(self):
        emplacement = Emplacement.objects.create(code="EMP-LIV")
        lot = Lot.objects.create(article=self.article, emplacement=emplacement, quantite=20)

        livraison = self._livraison()
        LivraisonLigne.objects.create(livraison=livraison, commande_ligne=self.commande_ligne, quantite_livree=6)

        lot.refresh_from_db()
        self.assertEqual(lot.quantite, 14)
        mouvement = MouvementStock.objects.get(lot=lot)
        self.assertEqual(mouvement.type_mouvement, MouvementStock.TypeMouvement.SORTIE)
        self.assertEqual(mouvement.quantite, 6)
        self.assertEqual(mouvement.reference_origine, f"LIVRAISON-{livraison.numero}")

    def test_livraison_sans_lot_ne_bloque_pas(self):
        # Article non géré en stock (cas courant d'un fabriqué sur mesure) :
        # aucun Lot n'existe, la livraison doit quand même passer.
        self.assertEqual(Lot.objects.filter(article=self.article).count(), 0)
        livraison = self._livraison()
        LivraisonLigne.objects.create(livraison=livraison, commande_ligne=self.commande_ligne, quantite_livree=6)
        self.commande_ligne.refresh_from_db()
        self.assertEqual(self.commande_ligne.quantite_livree, 6)

    def test_plusieurs_lots_leve_erreur_et_annule_tout(self):
        emplacement1 = Emplacement.objects.create(code="EMP-LIV-1")
        emplacement2 = Emplacement.objects.create(code="EMP-LIV-2")
        Lot.objects.create(article=self.article, emplacement=emplacement1, quantite=5)
        Lot.objects.create(article=self.article, emplacement=emplacement2, quantite=5)

        livraison = self._livraison()
        with self.assertRaises(LivraisonError):
            LivraisonLigne.objects.create(
                livraison=livraison, commande_ligne=self.commande_ligne, quantite_livree=6
            )

        # Tout ou rien : ni la ligne, ni le cumul livré ne doivent être
        # enregistrés (pas de ligne "fantôme" avec une quantité jamais
        # répercutée) — voir LivraisonLigne.save().
        self.assertEqual(LivraisonLigne.objects.count(), 0)
        self.commande_ligne.refresh_from_db()
        self.assertEqual(self.commande_ligne.quantite_livree, 0)


class PlanningSyncTests(TestCase):
    def setUp(self):
        tiers = Tiers.objects.create(code="CLI-003", raison_sociale="Client Sync", type_tiers="client")
        commande_devis = Devis.objects.create(
            numero="DEV-200", client=tiers, date_creation=datetime.date(2026, 1, 1), statut="valide"
        )
        adresse = Adresse.objects.create(
            tiers=tiers,
            est_facturation=True,
            adresse="1 rue",
            code_postal="75000",
            ville="Paris",
        )
        commande = Commande.objects.create(
            numero="CDE-200",
            devis=commande_devis, client=commande_devis.client,
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
            libelle="Platine support moteur",
        )
        self.assertEqual(article.nature, Article.Nature.FABRIQUE)
        self.assertEqual(article.libelle, "Platine support moteur")
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
        # date_creation du devis exposée au JS (voir devis_builder.js :
        # les nouvelles étapes de gamme doivent par défaut être datées de
        # la date de création du devis, pas du jour — régression :
        # une étape datée d'aujourd'hui sur un devis créé à une date
        # antérieure était silencieusement exclue du calcul des opérations).
        self.assertContains(response, '"date_creation": "2026-01-01"')

    def test_lien_vers_la_fiche_article_sur_chaque_ligne(self):
        ligne = DevisLigne.objects.create(devis=self.devis, article=self.composant, quantite=1)
        response = self.client.get(f"/admin/chiffrage/devis/{self.devis.pk}/constructeur/")
        self.assertContains(response, f"/admin/technique/article/{ligne.article.pk}/change/")

    def test_post_nouvel_article_cree_tout(self):
        payload = {
            "quantite": 3,
            "nouvel_article": {
                "reference": "PIECE-VIEW-1",
                "libelle": "Platine support moteur",
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
        self.assertEqual(Article.objects.get(pk="PIECE-VIEW-1").libelle, "Platine support moteur")
        self.assertEqual(self.devis.lignes.count(), 1)
        data = response.json()
        # matière : 3 * (5 * 0.2) = 3, marge 10% -> prix_vente_matiere = 3.3
        # opération : 3 * 50 (étape forfaitaire) = 150, marge par défaut du poste (0%) -> 150
        self.assertEqual(data["cout_matiere_calcule"], 3)
        self.assertAlmostEqual(data["prix_vente_operations"], 150)
        self.assertAlmostEqual(data["prix_vente_total"], 153.3, places=3)
        self.assertAlmostEqual(data["montant_total_ht"], 153.3, places=3)

    def test_etape_datee_apres_le_devis_est_silencieusement_ignoree(self):
        # Documente le mécanisme derrière la régression signalée par
        # l'utilisateur : une étape de gamme dont la date de début est
        # POSTÉRIEURE à devis.date_creation (2026-01-01 ici) n'est pas
        # active pour ce devis (gamme_active(), chiffrage/moteur.py) — le
        # prix des opérations retombe à 0 sans aucune erreur. C'est
        # exactement ce que produisait l'ancien défaut JS (date du jour)
        # sur un devis créé à une date antérieure — voir devis_builder.js,
        # addGammeRow(), qui utilise désormais devis.date_creation par défaut.
        payload = {
            "quantite": 3,
            "nouvel_article": {
                "reference": "PIECE-VIEW-DATE-POSTERIEURE",
                "composants": [{"article_composant": "VIS-VIEW", "quantite": 5}],
                "etapes": [
                    {"poste": "Poste-View", "ordre": 1, "cout_forfaitaire": 50, "date_debut": "2026-06-01"}
                ],
            },
        }
        response = self.client.post(
            f"/admin/chiffrage/devis/{self.devis.pk}/constructeur/",
            data=payload,
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertEqual(data["prix_vente_operations"], 0)

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

    def test_post_article_sans_cout_unitaire_bloque_la_ligne(self):
        # Régression : le prix de la ligne doit pouvoir être calculé pour
        # qu'elle soit validée — un article (matière) sans coût unitaire ne
        # doit plus créer une ligne "en attente" avec un simple avertissement.
        Article.objects.create(
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
        self.assertEqual(response.status_code, 400)
        self.assertIn("detail", response.json())
        self.assertEqual(self.devis.lignes.count(), 0)

    def test_post_nouvel_article_composant_sans_cout_unitaire_ne_cree_rien(self):
        # Même règle côté "nouvel article fabriqué" : si un composant de la
        # nomenclature n'a pas de coût unitaire, ni l'article, ni sa
        # nomenclature/gamme, ni la ligne de devis ne doivent être créés.
        Article.objects.create(
            reference="VIS-SANS-COUT",
            nature=Article.Nature.MATIERE_PREMIERE,
            unite_cout=Article.UniteCout.PIECE,
        )
        payload = {
            "quantite": 3,
            "nouvel_article": {
                "reference": "PIECE-VIEW-COMPOSANT-SANS-COUT",
                "composants": [{"article_composant": "VIS-SANS-COUT", "quantite": 5}],
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
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Article.objects.filter(pk="PIECE-VIEW-COMPOSANT-SANS-COUT").exists())
        self.assertEqual(self.devis.lignes.count(), 0)

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
        # opération : (10 + 5*3) = 25 min -> 25/60*50 = 20.8333..., marge 15% -> 23.9583...
        operation_attendue = 25 / 60 * 50 * 1.15
        self.assertAlmostEqual(data["prix_vente_matiere"], 133.7112, places=3)
        self.assertAlmostEqual(data["prix_vente_operations"], operation_attendue)
        self.assertAlmostEqual(data["prix_vente_total"], 133.7112 + operation_attendue, places=3)
        self.assertAlmostEqual(data["montant_total_ht"], 133.7112 + operation_attendue, places=3)


class DevisDelaiTests(TestCase):
    """Devis.delai : texte libre, jamais contraint au référentiel
    DelaiPropose (qui ne fournit que des suggestions — voir DelaiWidget,
    chiffrage/widgets.py)."""

    def setUp(self):
        self.client_tiers = Tiers.objects.create(
            code="CLI-DELAI", raison_sociale="Client Délai", type_tiers=Tiers.TypeTiers.CLIENT
        )
        from django.contrib.auth import get_user_model

        User = get_user_model()
        self.user = User.objects.create_superuser("delai-admin", "delai@example.com", "pass1234")
        self.client.force_login(self.user)

    def test_delai_hors_referentiel_accepte(self):
        devis = Devis(
            numero="DEV-DELAI-1",
            client=self.client_tiers,
            date_creation=datetime.date(2026, 1, 1),
            delai="Livraison sous 3 jours ouvrés, à confirmer",
        )
        devis.full_clean()  # ne doit pas lever, même sans entrée DelaiPropose correspondante
        devis.save()
        self.assertEqual(devis.delai, "Livraison sous 3 jours ouvrés, à confirmer")

    def test_delai_vide_reste_valide(self):
        devis = Devis(
            numero="DEV-DELAI-2", client=self.client_tiers, date_creation=datetime.date(2026, 1, 1)
        )
        devis.full_clean()  # optionnel : ne doit pas lever
        self.assertEqual(devis.delai, "")

    def test_formulaire_ajout_affiche_les_suggestions_du_referentiel(self):
        DelaiPropose.objects.create(libelle="2 semaines", ordre=1)
        DelaiPropose.objects.create(libelle="Sur stock", ordre=0)
        response = self.client.get("/admin/chiffrage/devis/add/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'list="delai-suggestions"')
        self.assertContains(response, "<datalist")
        self.assertContains(response, "2 semaines")
        self.assertContains(response, "Sur stock")


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
            est_facturation=True,
            adresse="1 rue de la Facture",
            code_postal="75000",
            ville="Paris",
        )
        self.adresse_livraison = Adresse.objects.create(
            tiers=self.client_tiers,
            est_livraison=True,
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
            est_facturation=True,
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
        # prix_vente_unitaire = prix_vente_total / quantite = 10 / 4
        self.assertEqual(resultat["prix_vente_unitaire"], 2.5)
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
        # matière : 111.426 * 1.2 = 133.7112
        # opération : (10+5*3) = 25 min -> 25/60*50 = 20.8333..., marge 15% -> 23.9583...
        operation_attendue = 25 / 60 * 50 * 1.15
        self.assertAlmostEqual(resultat["prix_vente_matiere"], 133.7112, places=3)
        self.assertAlmostEqual(resultat["prix_vente_operations"], operation_attendue)
        self.assertAlmostEqual(resultat["prix_vente_total"], 133.7112 + operation_attendue, places=3)
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
        # pas d'opérations (article matière première) : prix_vente_unitaire = 30/3 = 10 (= le prix forcé)
        self.assertEqual(data["prix_vente_unitaire"], 10)
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


class ConvertirEnCommandeViewTests(TestCase):
    """Bouton "Convertir en commande" (object-tools de la fiche Devis) :
    permet de convertir un devis validé en commande en un clic, sans passer
    par l'action d'admin "Lancer en production" de la liste des devis —
    signalé par l'utilisateur."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        self.user = User.objects.create_superuser("convertir-admin", "cv@example.com", "pass1234")
        self.client.force_login(self.user)

        self.client_tiers = Tiers.objects.create(
            code="CLI-CONVERTIR", raison_sociale="Client Convertir", type_tiers=Tiers.TypeTiers.CLIENT
        )
        Adresse.objects.create(
            tiers=self.client_tiers,
            est_facturation=True,
            adresse="1 rue A",
            code_postal="75000",
            ville="Paris",
            est_principale=True,
        )
        Adresse.objects.create(
            tiers=self.client_tiers,
            est_livraison=True,
            adresse="1 rue A",
            code_postal="75000",
            ville="Paris",
            est_principale=True,
        )
        self.article = Article.objects.create(
            reference="ART-CONVERTIR",
            nature=Article.Nature.MATIERE_PREMIERE,
            unite_cout=Article.UniteCout.PIECE,
            cout_unitaire=2.0,
        )

    def _devis_valide_avec_ligne(self, numero="DEV-CONVERTIR-01"):
        devis = Devis.objects.create(
            numero=numero, client=self.client_tiers, date_creation=datetime.date(2026, 1, 1),
            statut=Devis.Statut.VALIDE,
        )
        DevisLigne.objects.create(devis=devis, article=self.article, quantite=5)
        return devis

    def test_convertit_et_redirige_vers_la_commande(self):
        devis = self._devis_valide_avec_ligne()
        response = self.client.post(f"/admin/chiffrage/devis/{devis.pk}/convertir-commande/", follow=False)

        commande = Commande.objects.get(devis=devis)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, f"/admin/chiffrage/commande/{commande.pk}/change/")
        self.assertEqual(commande.lignes.get().quantite_commandee, 5)

    def test_devis_non_valide_refuse_et_redirige_vers_le_devis(self):
        devis = self._devis_valide_avec_ligne(numero="DEV-CONVERTIR-BROUILLON")
        devis.statut = Devis.Statut.BROUILLON
        devis.save()
        response = self.client.post(f"/admin/chiffrage/devis/{devis.pk}/convertir-commande/", follow=False)
        self.assertEqual(response.url, f"/admin/chiffrage/devis/{devis.pk}/change/")
        self.assertFalse(Commande.objects.filter(devis=devis).exists())

    def test_deja_converti_refuse(self):
        devis = self._devis_valide_avec_ligne(numero="DEV-CONVERTIR-DEJA")
        lancer_en_production(devis)
        response = self.client.post(f"/admin/chiffrage/devis/{devis.pk}/convertir-commande/", follow=False)
        self.assertEqual(response.url, f"/admin/chiffrage/devis/{devis.pk}/change/")
        self.assertEqual(Commande.objects.filter(devis=devis).count(), 1)

    def test_bouton_absent_sur_devis_brouillon(self):
        devis = Devis.objects.create(
            numero="DEV-CONVERTIR-VUE", client=self.client_tiers, date_creation=datetime.date(2026, 1, 1),
            statut=Devis.Statut.BROUILLON,
        )
        response = self.client.get(f"/admin/chiffrage/devis/{devis.pk}/change/")
        self.assertNotContains(response, "Convertir en commande")

    def test_bouton_present_sur_devis_valide(self):
        devis = self._devis_valide_avec_ligne(numero="DEV-CONVERTIR-VUE2")
        response = self.client.get(f"/admin/chiffrage/devis/{devis.pk}/change/")
        self.assertContains(response, "Convertir en commande")

    def test_lien_vers_la_commande_existante_apres_conversion(self):
        devis = self._devis_valide_avec_ligne(numero="DEV-CONVERTIR-VUE3")
        commande = lancer_en_production(devis)
        response = self.client.get(f"/admin/chiffrage/devis/{devis.pk}/change/")
        self.assertContains(response, f"Voir la commande {commande}")
        self.assertNotContains(response, "Convertir en commande")


class CommandeDirecteSupprimeeTests(TestCase):
    """Le bouton "Créer une commande directement" et son flux dédié sont
    supprimés (confirmé par l'utilisateur, remplacés par la création
    directe d'une commande + le calcul en direct par ligne)."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        self.user = User.objects.create_superuser("cdirecte-gone", "cdg@example.com", "pass1234")
        self.client.force_login(self.user)

    def test_bouton_absent_du_formulaire_dajout_devis(self):
        response = self.client.get("/admin/chiffrage/devis/add/")
        self.assertNotContains(response, "Créer une commande directement")

    def test_url_valider_commande_nexiste_plus(self):
        # Cette URL ne correspond plus à aucune route dédiée : elle retombe
        # sur le fallback générique de l'admin (<path:object_id>/, qui
        # redirige vers .../change/, puis vers la liste des devis faute
        # d'objet de cet id) plutôt que de convertir quoi que ce soit.
        devis = Devis.objects.create(
            numero="DEV-VALIDER-GONE", client=Tiers.objects.create(code="CLI-VALIDER-GONE", raison_sociale="X"),
            date_creation=datetime.date(2026, 1, 1), statut=Devis.Statut.VALIDE,
        )
        response = self.client.post(f"/admin/chiffrage/devis/{devis.pk}/valider-commande/", follow=True)
        self.assertFalse(Commande.objects.filter(devis=devis).exists())
        self.assertNotContains(response, "créée")


class LigneCommandeLiveCalcViewTests(TestCase):
    """Calcul automatique du prix d'une ligne de commande depuis la
    quantité/gamme/nomenclature (signalé par l'utilisateur), et suggestion
    du taux de TVA (article + régime fiscal du client) — endpoints AJAX
    utilisés par commande_admin_live.js."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        self.user = User.objects.create_superuser("cde-live-admin", "cl@example.com", "pass1234")
        self.client.force_login(self.user)

        self.tiers = Tiers.objects.create(
            code="CLI-CDE-LIVE", raison_sociale="Client Commande Live", type_tiers=Tiers.TypeTiers.CLIENT,
            regime_fiscal=Tiers.RegimeFiscal.FRANCE,
        )
        self.adresse = Adresse.objects.create(
            tiers=self.tiers, est_facturation=True, est_livraison=True,
            adresse="1 rue", code_postal="75000", ville="Paris",
        )
        self.taux_reduit = TauxTVA.objects.create(nom="Taux réduit CDE Live", taux=5.5)
        self.article = Article.objects.create(
            reference="ART-CDE-LIVE", nature=Article.Nature.MATIERE_PREMIERE,
            unite_cout=Article.UniteCout.PIECE, cout_unitaire=10, taux_marge_defaut=10,
            taux_tva=self.taux_reduit,
        )
        self.devis = Devis.objects.create(
            numero="DEV-CDE-LIVE", client=self.tiers, date_creation=datetime.date(2026, 1, 1),
            statut=Devis.Statut.VALIDE,
        )
        self.devis_ligne = DevisLigne.objects.create(devis=self.devis, article=self.article, quantite=3)
        self.commande = Commande.objects.create(
            numero="CDE-LIVE", devis=self.devis, client=self.tiers, reference_client="PO-LIVE",
            date_commande=datetime.date(2026, 1, 1),
            adresse_facturation=self.adresse, adresse_livraison=self.adresse,
        )

    def test_recalcul_calcule_le_prix_pour_une_ligne_sans_devis_ligne(self):
        ligne = CommandeLigne.objects.create(
            commande=self.commande, article=self.article, quantite_commandee=2,
        )
        response = self.client.post(
            f"/admin/chiffrage/commande/{self.commande.pk}/lignes/{ligne.pk}/recalculer/",
            data=json.dumps({"quantite_commandee": "5"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        # coût = 10*5=50 ; prix = 50*1.10 = 55 ; unitaire = 11
        self.assertAlmostEqual(data["prix_vente_unitaire"], 11)
        self.assertEqual(data["taux_tva_suggere"]["id"], self.taux_reduit.pk)

        ligne.refresh_from_db()
        self.assertAlmostEqual(ligne.prix_vente_unitaire, 11)
        self.assertEqual(ligne.quantite_commandee, 5)

    def test_recalcul_ne_touche_pas_le_prix_dune_ligne_avec_devis_ligne(self):
        # Ligne "surchargée" (héritée d'un devis) : un changement de quantité
        # ne doit jamais recalculer automatiquement le prix — c'est une
        # décision manuelle, comme documenté sur CommandeLigne.
        ligne = CommandeLigne.objects.create(
            commande=self.commande, article=self.article, devis_ligne=self.devis_ligne,
            quantite_commandee=3, prix_vente_unitaire=99.0,
        )
        response = self.client.post(
            f"/admin/chiffrage/commande/{self.commande.pk}/lignes/{ligne.pk}/recalculer/",
            data=json.dumps({"quantite_commandee": "8"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        ligne.refresh_from_db()
        self.assertEqual(ligne.quantite_commandee, 8)
        self.assertAlmostEqual(ligne.prix_vente_unitaire, 99.0)

    def test_recalcul_avec_prix_explicite_ne_recalcule_pas(self):
        ligne = CommandeLigne.objects.create(
            commande=self.commande, article=self.article, quantite_commandee=2,
        )
        response = self.client.post(
            f"/admin/chiffrage/commande/{self.commande.pk}/lignes/{ligne.pk}/recalculer/",
            data=json.dumps({"quantite_commandee": "5", "prix_vente_unitaire": "42"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        ligne.refresh_from_db()
        self.assertAlmostEqual(ligne.prix_vente_unitaire, 42)

    def test_previsualiser_ligne_commande_existante(self):
        response = self.client.post(
            f"/admin/chiffrage/commande/{self.commande.pk}/lignes/previsualiser/",
            data=json.dumps({"article": self.article.pk, "quantite": "4"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        # coût = 10*4=40 ; prix = 40*1.10=44 ; unitaire = 11
        self.assertAlmostEqual(data["prix_vente_unitaire"], 11)
        self.assertEqual(data["taux_tva_suggere"]["id"], self.taux_reduit.pk)
        self.assertFalse(CommandeLigne.objects.filter(commande=self.commande, quantite_commandee=4).exists())

    def test_previsualiser_ligne_nouvelle_commande(self):
        response = self.client.post(
            "/admin/chiffrage/commande/nouvelle-commande/previsualiser-ligne/",
            data=json.dumps(
                {
                    "article": self.article.pk,
                    "quantite": "2",
                    "date_commande": "2026-01-01",
                    "client": self.tiers.pk,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        # coût = 10*2=20 ; prix = 20*1.10=22 ; unitaire = 11
        self.assertAlmostEqual(data["prix_vente_unitaire"], 11)
        self.assertEqual(data["taux_tva_suggere"]["id"], self.taux_reduit.pk)

    def test_previsualiser_ligne_nouvelle_commande_sans_client_pas_de_suggestion_tva(self):
        response = self.client.post(
            "/admin/chiffrage/commande/nouvelle-commande/previsualiser-ligne/",
            data=json.dumps({"article": self.article.pk, "quantite": "2"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIsNone(response.json()["taux_tva_suggere"])


class CreerCommandeDirectementAdminTests(TestCase):
    """Une commande peut désormais être créée directement depuis son propre
    formulaire d'admin, sans devis d'origine (signalé par l'utilisateur)."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        self.user = User.objects.create_superuser("cde-directe-admin", "cda@example.com", "pass1234")
        self.client.force_login(self.user)

        self.tiers = Tiers.objects.create(
            code="CLI-CDE-DIRECTE", raison_sociale="Client Commande Directe Admin",
            type_tiers=Tiers.TypeTiers.CLIENT,
        )
        self.adresse = Adresse.objects.create(
            tiers=self.tiers, est_facturation=True, est_livraison=True,
            adresse="1 rue", code_postal="75000", ville="Paris",
        )
        self.article = Article.objects.create(
            reference="ART-CDE-DIRECTE", nature=Article.Nature.MATIERE_PREMIERE,
            unite_cout=Article.UniteCout.PIECE, cout_unitaire=5,
        )

    def test_creation_sans_devis_via_le_formulaire_dadmin(self):
        payload = {
            "numero": "CDE-DIRECTE-01",
            "devis": "",
            "client": self.tiers.pk,
            "reference_client": "PO-ADMIN-01",
            "date_commande": "2026-01-01",
            "statut": "",
            "adresse_facturation": self.adresse.pk,
            "adresse_livraison": self.adresse.pk,
            "lignes-TOTAL_FORMS": "1",
            "lignes-INITIAL_FORMS": "0",
            "lignes-MIN_NUM_FORMS": "0",
            "lignes-MAX_NUM_FORMS": "1000",
            "lignes-0-id": "",
            "lignes-0-article": self.article.pk,
            "lignes-0-designation": "",
            "lignes-0-quantite_commandee": "10",
            "lignes-0-prix_vente_unitaire": "8",
            "lignes-0-taux_tva": "",
            "lignes-0-date_livraison_prevue": "",
            "_save": "Enregistrer",
        }
        response = self.client.post("/admin/chiffrage/commande/add/", data=payload, follow=False)
        self.assertEqual(response.status_code, 302, getattr(response, "context", None))

        commande = Commande.objects.get(pk="CDE-DIRECTE-01")
        self.assertIsNone(commande.devis)
        self.assertEqual(commande.client, self.tiers)
        self.assertEqual(commande.reference_client, "PO-ADMIN-01")
        self.assertEqual(commande.lignes.get().quantite_commandee, 10)


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
            est_facturation=True,
            adresse="1 rue de la Facture",
            code_postal="75000",
            ville="Paris",
            est_principale=True,
        )
        # Une adresse de livraison non principale ne doit jamais être renvoyée.
        Adresse.objects.create(
            tiers=self.tiers,
            est_livraison=True,
            adresse="2 rue Secondaire",
            code_postal="75000",
            ville="Paris",
            est_principale=False,
        )
        livraison = Adresse.objects.create(
            tiers=self.tiers,
            est_livraison=True,
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
            est_livraison=True,
            adresse="Site Nord",
            code_postal="59000",
            ville="Lille",
            est_principale=True,
        )
        Contact.objects.create(tiers=self.tiers, nom="Principal Tiers", est_principal=True)
        contact_site = Contact.objects.create(
            tiers=self.tiers, nom="Contact Site", adresse_associee=livraison
        )

        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["contact"]["id"], contact_site.pk)

    def test_contact_replie_sur_le_principal_du_tiers_si_aucun_lie_a_l_adresse(self):
        Adresse.objects.create(
            tiers=self.tiers,
            est_livraison=True,
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
    une adresse de livraison précise (Contact.adresse_associee) — utilisé
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
            est_livraison=True,
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
        contact = Contact.objects.create(tiers=self.tiers, nom="Site Est", adresse_associee=self.livraison)
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


class LivraisonAdminTests(TestCase):
    """Fiche admin Livraison : numérotation automatique (codification) et
    remontée propre d'une LivraisonError (pas une page 500) si le stock
    automatique ne peut pas être appliqué (plusieurs lots pour l'article)."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        self.user = User.objects.create_superuser("livraison-admin", "l@example.com", "pass1234")
        self.client.force_login(self.user)

        self.client_tiers = Tiers.objects.create(
            code="CLI-LIV-ADMIN", raison_sociale="Client Livraison Admin", type_tiers=Tiers.TypeTiers.CLIENT
        )
        for champ_type in ["est_facturation", "est_livraison"]:
            Adresse.objects.create(
                tiers=self.client_tiers,
                adresse="1 rue",
                code_postal="75000",
                ville="Paris",
                est_principale=True,
                **{champ_type: True},
            )
        self.article = Article.objects.create(
            reference="VIS-LIV-ADMIN",
            nature=Article.Nature.MATIERE_PREMIERE,
            unite_cout=Article.UniteCout.PIECE,
            cout_unitaire=1.0,
        )
        self.devis = Devis.objects.create(
            numero="DEV-LIV-ADMIN",
            client=self.client_tiers,
            date_creation=datetime.date(2026, 1, 1),
            statut=Devis.Statut.VALIDE,
        )
        DevisLigne.objects.create(devis=self.devis, article=self.article, quantite=10)
        self.commande = lancer_en_production(self.devis)
        self.commande_ligne = self.commande.lignes.get(article=self.article)

    def test_formulaire_ajout_pre_rempli_avec_le_code_genere(self):
        response = self.client.get("/admin/chiffrage/livraison/add/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "LIV-00001")

    def test_erreur_lots_multiples_affichee_sans_500(self):
        emplacement1 = Emplacement.objects.create(code="EMP-LIV-ADMIN-1")
        emplacement2 = Emplacement.objects.create(code="EMP-LIV-ADMIN-2")
        Lot.objects.create(article=self.article, emplacement=emplacement1, quantite=5)
        Lot.objects.create(article=self.article, emplacement=emplacement2, quantite=5)

        payload = {
            "numero": "LIV-ADMIN-TEST",
            "commande": self.commande.pk,
            "date_livraison": "2026-02-01",
            "lignes-TOTAL_FORMS": "1",
            "lignes-INITIAL_FORMS": "0",
            "lignes-MIN_NUM_FORMS": "0",
            "lignes-MAX_NUM_FORMS": "1000",
            "lignes-0-commande_ligne": self.commande_ligne.pk,
            "lignes-0-quantite_livree": "6",
            "lignes-0-id": "",
            "lignes-0-livraison": "",
            "_save": "Enregistrer",
        }
        response = self.client.post("/admin/chiffrage/livraison/add/", data=payload, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Plusieurs lots existent")
        # La livraison (l'objet parent) est enregistrée normalement — c'est
        # l'admin lui-même qui la sauvegarde avant de traiter les inlines.
        self.assertTrue(Livraison.objects.filter(pk="LIV-ADMIN-TEST").exists())
        # Mais la ligne, elle, ne doit pas être enregistrée "à moitié" :
        # LivraisonLigne.save() est atomique (voir le modèle) — ni la ligne
        # ni le cumul livré ne doivent survivre à l'échec de résolution du lot.
        self.assertEqual(LivraisonLigne.objects.count(), 0)
        self.commande_ligne.refresh_from_db()
        self.assertEqual(self.commande_ligne.quantite_livree, 0)


class CommandeLigneInlineAdminTests(TestCase):
    """Fiche admin Commande : la date de livraison prévue est la seule
    donnée éditable par ligne (le reste vient du devis) — chaque ligne peut
    avoir sa propre date, différente des autres lignes de la même commande."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        self.user = User.objects.create_superuser("cligne-admin", "cl@example.com", "pass1234")
        self.client.force_login(self.user)

        client_tiers = Tiers.objects.create(code="CLI-CLIGNE", raison_sociale="Client CLigne")
        adresse = Adresse.objects.create(
            tiers=client_tiers, est_facturation=True,
            adresse="1 rue A", code_postal="75000", ville="Paris",
        )
        article = Article.objects.create(reference="ART-CLIGNE", nature=Article.Nature.MATIERE_PREMIERE)
        devis = Devis.objects.create(
            numero="DEV-CLIGNE", client=client_tiers, date_creation=datetime.date(2026, 1, 1),
        )
        self.commande = Commande.objects.create(
            numero="CDE-CLIGNE", devis=devis, client=devis.client, date_commande=datetime.date(2026, 1, 1),
            adresse_facturation=adresse, adresse_livraison=adresse,
        )
        self.ligne = CommandeLigne.objects.create(
            commande=self.commande, article=article, quantite_commandee=4
        )

    def test_date_livraison_prevue_editable_par_ligne(self):
        payload = {
            "numero": self.commande.pk,
            "devis": self.commande.devis_id,
            "client": self.commande.client_id,
            "reference_client": self.commande.reference_client or "REF-TEST",
            "date_commande": "2026-01-01",
            "statut": "",
            "adresse_facturation": self.commande.adresse_facturation_id,
            "adresse_livraison": self.commande.adresse_livraison_id,
            "lignes-TOTAL_FORMS": "1",
            "lignes-INITIAL_FORMS": "1",
            "lignes-MIN_NUM_FORMS": "0",
            "lignes-MAX_NUM_FORMS": "1000",
            "lignes-0-id": self.ligne.pk,
            "lignes-0-article": self.ligne.article_id,
            "lignes-0-designation": "",
            "lignes-0-quantite_commandee": "4",
            "lignes-0-prix_vente_unitaire": "",
            "lignes-0-taux_tva": "",
            "lignes-0-date_livraison_prevue": "15/02/2026",
            "_save": "Enregistrer",
        }
        response = self.client.post(
            f"/admin/chiffrage/commande/{self.commande.pk}/change/", data=payload, follow=True
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.ligne.refresh_from_db()
        self.assertEqual(self.ligne.date_livraison_prevue, datetime.date(2026, 2, 15))


class ActionSynchroniserLignesAdminTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        self.user = User.objects.create_superuser("sync-admin", "s@example.com", "pass1234")
        self.client.force_login(self.user)

        client_tiers = Tiers.objects.create(code="CLI-SYNCADM", raison_sociale="Client Sync Admin")
        adresse = Adresse.objects.create(
            tiers=client_tiers, est_facturation=True,
            adresse="1 rue A", code_postal="75000", ville="Paris",
        )
        article = Article.objects.create(reference="ART-SYNCADM", nature=Article.Nature.MATIERE_PREMIERE)
        devis = Devis.objects.create(
            numero="DEV-SYNCADM", client=client_tiers, date_creation=datetime.date(2026, 1, 1),
        )
        DevisLigne.objects.create(devis=devis, article=article, quantite=7)
        self.commande = Commande.objects.create(
            numero="CDE-SYNCADM", devis=devis, client=devis.client, date_commande=datetime.date(2026, 1, 1),
            adresse_facturation=adresse, adresse_livraison=adresse,
        )

    def test_action_recree_les_lignes_manquantes(self):
        self.assertEqual(self.commande.lignes.count(), 0)
        response = self.client.post(
            "/admin/chiffrage/commande/",
            data={
                "action": "action_synchroniser_lignes",
                "_selected_action": [self.commande.pk],
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.commande.lignes.count(), 1)
        self.assertContains(response, "1 ligne(s) de commande recréée(s)")


class SurchargesCommandeLigneTests(TestCase):
    """CommandeLigne.quantite_commandee/prix_vente_unitaire/taux_tva/
    designation sont des surcharges (valeur de départ = devis, modifiable
    ensuite sans jamais toucher le devis) — voir CommandeLigne.clean() pour
    les garde-fous et montant_ht/montant_ttc pour le recalcul."""

    def setUp(self):
        client_tiers = Tiers.objects.create(code="CLI-SURCH", raison_sociale="Client Surcharge")
        adresse = Adresse.objects.create(
            tiers=client_tiers, est_facturation=True,
            adresse="1 rue A", code_postal="75000", ville="Paris",
        )
        self.article = Article.objects.create(reference="ART-SURCH", nature=Article.Nature.MATIERE_PREMIERE)
        devis = Devis.objects.create(
            numero="DEV-SURCH", client=client_tiers, date_creation=datetime.date(2026, 1, 1),
        )
        self.commande = Commande.objects.create(
            numero="CDE-SURCH", devis=devis, client=devis.client, date_commande=datetime.date(2026, 1, 1),
            adresse_facturation=adresse, adresse_livraison=adresse,
        )
        self.taux = TauxTVA.objects.create(nom="Taux Surch", taux=10)
        self.ligne = CommandeLigne.objects.create(
            commande=self.commande, article=self.article, quantite_commandee=10,
            prix_vente_unitaire=5.0, taux_tva=self.taux,
        )

    def test_quantite_inferieure_a_livree_refusee(self):
        CommandeLigne.objects.filter(pk=self.ligne.pk).update(quantite_livree=6)
        self.ligne.refresh_from_db()
        self.ligne.quantite_commandee = 5
        with self.assertRaises(ValidationError):
            self.ligne.full_clean()

    def test_quantite_egale_a_livree_acceptee(self):
        CommandeLigne.objects.filter(pk=self.ligne.pk).update(quantite_livree=6)
        self.ligne.refresh_from_db()
        self.ligne.quantite_commandee = 6
        self.ligne.full_clean()  # ne lève pas

    def test_changer_article_ligne_livree_refuse(self):
        autre_article = Article.objects.create(reference="ART-SURCH-2", nature=Article.Nature.MATIERE_PREMIERE)
        CommandeLigne.objects.filter(pk=self.ligne.pk).update(quantite_livree=3)
        self.ligne.refresh_from_db()
        self.ligne.article = autre_article
        with self.assertRaises(ValidationError):
            self.ligne.full_clean()

    def test_changer_article_ligne_non_livree_accepte(self):
        autre_article = Article.objects.create(reference="ART-SURCH-3", nature=Article.Nature.MATIERE_PREMIERE)
        self.ligne.article = autre_article
        self.ligne.full_clean()  # ne lève pas, rien n'a encore été livré

    def test_montant_recalcule_depuis_les_valeurs_de_la_ligne(self):
        self.assertEqual(self.ligne.montant_ht, 50.0)
        self.assertAlmostEqual(self.ligne.montant_ttc, 55.0)

        self.ligne.prix_vente_unitaire = 8.0
        self.ligne.save()
        self.assertEqual(self.ligne.montant_ht, 80.0)
        self.assertAlmostEqual(self.ligne.montant_ttc, 88.0)

    def test_montant_none_sans_prix(self):
        ligne_sans_prix = CommandeLigne.objects.create(
            commande=self.commande, article=self.article, quantite_commandee=3
        )
        self.assertIsNone(ligne_sans_prix.montant_ht)
        self.assertIsNone(ligne_sans_prix.montant_ttc)


class LancerLigneEnProductionTests(TestCase):
    """production.lancer_ligne_en_production : crée l'OF d'UNE ligne de
    commande précise (ex. ligne ajoutée après coup pour représenter une
    augmentation de quantité) sans repasser par tout le devis."""

    def setUp(self):
        client_tiers = Tiers.objects.create(code="CLI-SOLO", raison_sociale="Client Solo")
        adresse = Adresse.objects.create(
            tiers=client_tiers, est_facturation=True,
            adresse="1 rue A", code_postal="75000", ville="Paris",
        )
        self.article_fabrique = Article.objects.create(reference="PIECE-SOLO", nature=Article.Nature.FABRIQUE)
        self.poste = PosteTravail.objects.create(nom="Poste-Solo", mode_calcul=PosteTravail.ModeCalcul.HORAIRE)
        TarifPoste.objects.create(poste=self.poste, cout_horaire=30, date_debut=datetime.date(2020, 1, 1))
        Gamme.objects.create(
            article=self.article_fabrique, poste=self.poste, ordre=1,
            temps_fixe=10, temps_variable=1, date_debut=datetime.date(2020, 1, 1),
        )
        self.article_mp = Article.objects.create(reference="MP-SOLO", nature=Article.Nature.MATIERE_PREMIERE)
        devis = Devis.objects.create(
            numero="DEV-SOLO", client=client_tiers, date_creation=datetime.date(2026, 1, 1),
        )
        self.commande = Commande.objects.create(
            numero="CDE-SOLO", devis=devis, client=devis.client, date_commande=datetime.date(2026, 1, 1),
            adresse_facturation=adresse, adresse_livraison=adresse,
        )

    def test_cree_of_pour_ligne_ajoutee_apres_coup(self):
        ligne = CommandeLigne.objects.create(
            commande=self.commande, article=self.article_fabrique, quantite_commandee=5,
        )
        of = lancer_ligne_en_production(ligne)
        self.assertEqual(of.article, self.article_fabrique)
        self.assertEqual(of.quantite, 5)
        operation = of.operations.get(ordre=1)
        # (10 + 1*5) = 15
        self.assertAlmostEqual(operation.temps_prevu, 15)

    def test_refuse_si_article_pas_fabrique(self):
        ligne = CommandeLigne.objects.create(
            commande=self.commande, article=self.article_mp, quantite_commandee=5,
        )
        with self.assertRaises(ChiffrageError):
            lancer_ligne_en_production(ligne)

    def test_refuse_si_of_deja_existant_pour_cet_article(self):
        ligne = CommandeLigne.objects.create(
            commande=self.commande, article=self.article_fabrique, quantite_commandee=5,
        )
        lancer_ligne_en_production(ligne)
        ligne_supplementaire = CommandeLigne.objects.create(
            commande=self.commande, article=self.article_fabrique, quantite_commandee=2,
        )
        with self.assertRaises(ChiffrageError):
            lancer_ligne_en_production(ligne_supplementaire)


class CommandeLigneAuditAdminTests(TestCase):
    """Traçabilité des surcharges (CommandeLigneModification) et
    avertissement si la quantité augmente alors qu'un OF existe déjà —
    posés par CommandeAdmin.save_formset / CommandeLigneAdmin.save_model,
    qui ont accès à request.user (impossible au niveau du modèle)."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        self.user = User.objects.create_superuser("audit-admin", "au@example.com", "pass1234")
        self.client.force_login(self.user)

        client_tiers = Tiers.objects.create(code="CLI-AUDIT", raison_sociale="Client Audit")
        self.adresse = Adresse.objects.create(
            tiers=client_tiers, est_facturation=True,
            adresse="1 rue A", code_postal="75000", ville="Paris",
        )
        self.article = Article.objects.create(reference="ART-AUDIT", nature=Article.Nature.MATIERE_PREMIERE)
        self.article_fabrique = Article.objects.create(reference="ART-AUDIT-OF", nature=Article.Nature.FABRIQUE)
        self.poste = PosteTravail.objects.create(nom="Poste-Audit", mode_calcul=PosteTravail.ModeCalcul.HORAIRE)
        TarifPoste.objects.create(poste=self.poste, cout_horaire=20, date_debut=datetime.date(2020, 1, 1))
        Gamme.objects.create(
            article=self.article_fabrique, poste=self.poste, ordre=1,
            temps_fixe=5, temps_variable=1, date_debut=datetime.date(2020, 1, 1),
        )
        self.devis = Devis.objects.create(
            numero="DEV-AUDIT", client=client_tiers, date_creation=datetime.date(2026, 1, 1),
        )
        self.commande = Commande.objects.create(
            numero="CDE-AUDIT", devis=self.devis, client=self.devis.client, date_commande=datetime.date(2026, 1, 1),
            adresse_facturation=self.adresse, adresse_livraison=self.adresse,
        )

    def _payload_ligne_unique(self, ligne, **overrides):
        payload = {
            "numero": self.commande.pk,
            "devis": self.commande.devis_id,
            "client": self.commande.client_id,
            "reference_client": self.commande.reference_client or "REF-TEST",
            "date_commande": "2026-01-01",
            "statut": "",
            "adresse_facturation": self.commande.adresse_facturation_id,
            "adresse_livraison": self.commande.adresse_livraison_id,
            "lignes-TOTAL_FORMS": "1",
            "lignes-INITIAL_FORMS": "1",
            "lignes-MIN_NUM_FORMS": "0",
            "lignes-MAX_NUM_FORMS": "1000",
            "lignes-0-id": ligne.pk,
            "lignes-0-article": ligne.article_id,
            "lignes-0-designation": ligne.designation,
            "lignes-0-quantite_commandee": str(ligne.quantite_commandee),
            "lignes-0-prix_vente_unitaire": "" if ligne.prix_vente_unitaire is None else str(ligne.prix_vente_unitaire),
            "lignes-0-taux_tva": "",
            "lignes-0-date_livraison_prevue": "",
            "_save": "Enregistrer",
        }
        payload.update(overrides)
        return payload

    def test_modification_prix_via_inline_est_tracee(self):
        ligne = CommandeLigne.objects.create(
            commande=self.commande, article=self.article, quantite_commandee=10, prix_vente_unitaire=5.0,
        )
        payload = self._payload_ligne_unique(ligne, **{"lignes-0-prix_vente_unitaire": "7.5"})
        response = self.client.post(
            f"/admin/chiffrage/commande/{self.commande.pk}/change/", data=payload, follow=True
        )
        self.assertEqual(response.status_code, 200, response.content)

        modification = CommandeLigneModification.objects.get(commande_ligne=ligne, champ="prix_vente_unitaire")
        self.assertEqual(modification.ancienne_valeur, "5.0")
        self.assertEqual(modification.nouvelle_valeur, "7.5")
        self.assertEqual(modification.utilisateur, self.user)

    def test_aucune_trace_si_rien_ne_change(self):
        ligne = CommandeLigne.objects.create(
            commande=self.commande, article=self.article, quantite_commandee=10, prix_vente_unitaire=5.0,
        )
        payload = self._payload_ligne_unique(ligne)
        self.client.post(f"/admin/chiffrage/commande/{self.commande.pk}/change/", data=payload, follow=True)
        self.assertEqual(CommandeLigneModification.objects.filter(commande_ligne=ligne).count(), 0)

    def test_avertissement_si_augmentation_quantite_avec_of_existant(self):
        ligne = CommandeLigne.objects.create(
            commande=self.commande, article=self.article_fabrique, quantite_commandee=5,
        )
        lancer_ligne_en_production(ligne)
        payload = self._payload_ligne_unique(ligne, **{"lignes-0-quantite_commandee": "8"})
        response = self.client.post(
            f"/admin/chiffrage/commande/{self.commande.pk}/change/", data=payload, follow=True
        )
        self.assertContains(response, "ordre de fabrication existe déjà")

    def test_pas_avertissement_si_diminution_quantite(self):
        ligne = CommandeLigne.objects.create(
            commande=self.commande, article=self.article_fabrique, quantite_commandee=5,
        )
        lancer_ligne_en_production(ligne)
        payload = self._payload_ligne_unique(ligne, **{"lignes-0-quantite_commandee": "3"})
        response = self.client.post(
            f"/admin/chiffrage/commande/{self.commande.pk}/change/", data=payload, follow=True
        )
        self.assertNotContains(response, "ordre de fabrication existe déjà")

    def test_ajout_dune_ligne_via_inline(self):
        payload = {
            "numero": self.commande.pk,
            "devis": self.commande.devis_id,
            "client": self.commande.client_id,
            "reference_client": self.commande.reference_client or "REF-TEST",
            "date_commande": "2026-01-01",
            "statut": "",
            "adresse_facturation": self.commande.adresse_facturation_id,
            "adresse_livraison": self.commande.adresse_livraison_id,
            "lignes-TOTAL_FORMS": "1",
            "lignes-INITIAL_FORMS": "0",
            "lignes-MIN_NUM_FORMS": "0",
            "lignes-MAX_NUM_FORMS": "1000",
            "lignes-0-id": "",
            "lignes-0-article": self.article.pk,
            "lignes-0-designation": "Complément de commande",
            "lignes-0-quantite_commandee": "4",
            "lignes-0-prix_vente_unitaire": "6",
            "lignes-0-taux_tva": "",
            "lignes-0-date_livraison_prevue": "",
            "_save": "Enregistrer",
        }
        response = self.client.post(
            f"/admin/chiffrage/commande/{self.commande.pk}/change/", data=payload, follow=True
        )
        self.assertEqual(response.status_code, 200, response.content)
        ligne = self.commande.lignes.get(article=self.article)
        self.assertEqual(ligne.quantite_commandee, 4)
        self.assertEqual(ligne.designation, "Complément de commande")
        self.assertIsNone(ligne.devis_ligne)

    def test_options_taux_tva_affichent_uniquement_le_pourcentage(self):
        # Le champ est éditable (select), mais ses options ne doivent montrer
        # que le taux ("20%"), jamais le libellé du référentiel — sinon la
        # colonne de l'inline s'élargit inutilement (voir
        # CommandeLigneForm/TauxTVACompactChoiceField).
        taux = TauxTVA.objects.create(nom="Taux unique pour ce test", taux=20)
        ligne = CommandeLigne.objects.create(
            commande=self.commande, article=self.article, quantite_commandee=1, taux_tva=taux,
        )
        response = self.client.get(f"/admin/chiffrage/commandeligne/{ligne.pk}/change/")
        self.assertContains(response, ">20%<")
        self.assertNotContains(response, "Taux unique pour ce test")

    def test_options_taux_tva_devis_affichent_uniquement_le_pourcentage(self):
        # Même correctif que ci-dessus, côté DevisLigne (DevisLigneForm) : la
        # ligne de devis a son propre champ taux_tva, distinct de celui de
        # CommandeLigne, jamais touché par la première correction.
        taux = TauxTVA.objects.create(nom="Taux unique devis pour ce test", taux=20)
        ligne = DevisLigne.objects.create(
            devis=self.devis, article=self.article, quantite=1, taux_tva=taux,
        )
        response = self.client.get(f"/admin/chiffrage/devis/{self.devis.pk}/change/")
        self.assertContains(response, ">20%<")
        self.assertNotContains(response, "Taux unique devis pour ce test")

        response = self.client.get(f"/admin/chiffrage/devisligne/{ligne.pk}/change/")
        self.assertContains(response, ">20%<")
        self.assertNotContains(response, "Taux unique devis pour ce test")

    def test_action_lancer_en_production_succes(self):
        ligne = CommandeLigne.objects.create(
            commande=self.commande, article=self.article_fabrique, quantite_commandee=5,
        )
        response = self.client.post(
            "/admin/chiffrage/commandeligne/",
            data={"action": "action_lancer_en_production", "_selected_action": [ligne.pk]},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(OrdreFabrication.objects.filter(commande=self.commande, article=self.article_fabrique).exists())

    def test_action_lancer_en_production_erreur_affichee(self):
        ligne = CommandeLigne.objects.create(
            commande=self.commande, article=self.article, quantite_commandee=5,
        )
        response = self.client.post(
            "/admin/chiffrage/commandeligne/",
            data={"action": "action_lancer_en_production", "_selected_action": [ligne.pk]},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(OrdreFabrication.objects.filter(commande=self.commande, article=self.article).exists())
