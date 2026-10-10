import datetime
import json
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone

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
from .validation import verifier_validation_devis
from .planning_sync import PlanningSyncError, resynchroniser, tenter_synchronisation
from .production import (
    creer_ordres_fabrication,
    lancer_en_production,
    lancer_ligne_en_production,
    synchroniser_lignes_commande,
)


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
        self.assertAlmostEqual(float(cout_unitaire), 37.142, places=3)

        cout_total = cout_matiere_article(parent, 3)
        self.assertAlmostEqual(float(cout_total), 111.426, places=3)

    def test_cout_matiere_premiere_directe(self):
        vis = Article.objects.create(
            reference="VIS-M8",
            nature=Article.Nature.MATIERE_PREMIERE,
            unite_cout=Article.UniteCout.PIECE,
            cout_unitaire=0.10,
        )
        self.assertAlmostEqual(float(cout_matiere_article(vis, 100)), 10.0)

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
            self.assertAlmostEqual(float(cout_matiere_article(article, 4)), 12.0)


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
        self.assertAlmostEqual(float(self.ligne.cout_matiere_calcule), 111.426, places=3)
        self.assertEqual(self.ligne.taux_marge_matiere_applique, 20)
        self.assertEqual(self.ligne.prix_vente_matiere, Decimal("133.71"))  # arrondi au centime

    def test_calcul_operations_gamme(self):
        calculer_devis(self.devis)
        operation = self.ligne.operations.get(ordre=1)
        # temps_fixe/temps_variable sont en MINUTES : (10 + 5*3) = 25 min,
        # converties en heures avant le tarif horaire -> 25/60 * 50 = 20.8333...
        self.assertEqual(operation.cout_calcule, Decimal("20.8333"))
        self.assertEqual(operation.taux_marge_applique, 15)
        self.assertEqual(operation.prix_vente, Decimal("23.96"))

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
        self.assertAlmostEqual(float(operation.cout_calcule), 25 / 60 * 60)

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
        self.assertAlmostEqual(float(self.ligne.cout_matiere_calcule), 111.426, places=3)

    def test_prix_vente_total_ligne_integre_les_operations(self):
        calculer_devis(self.devis)
        self.ligne.refresh_from_db()
        # matière : 111.426 * 1.2 = 133.7112 ; opération : 25/60*50 * 1.15 = 23.9583...
        operation_attendue = 25 / 60 * 50 * 1.15
        self.assertEqual(self.ligne.prix_vente_operations, Decimal("23.96"))
        self.assertEqual(self.ligne.prix_vente_total, Decimal("133.71") + Decimal("23.96"))

    def test_prix_vente_total_ligne_none_si_matiere_non_calculee(self):
        self.assertIsNone(self.ligne.prix_vente_matiere)
        self.assertIsNone(self.ligne.prix_vente_total)

    def test_prix_vente_unitaire_ramene_le_total_a_une_unite(self):
        calculer_devis(self.devis)
        self.ligne.refresh_from_db()
        # quantite=3 ; prix_vente_unitaire = prix_vente_total / 3
        self.assertAlmostEqual(float(self.ligne.prix_vente_unitaire), float(self.ligne.prix_vente_total) / 3, places=5)

    def test_prix_vente_unitaire_none_si_matiere_non_calculee(self):
        self.assertIsNone(self.ligne.prix_vente_unitaire)

    def test_montants_devis_integrent_matiere_et_operations(self):
        calculer_devis(self.devis)
        operation_attendue = 25 / 60 * 50 * 1.15
        # Les montants affichés sont arrondis au centime (comme sur la facture).
        self.assertEqual(self.devis.montant_matiere_ht, Decimal("133.71"))
        self.assertEqual(self.devis.montant_operations_ht, Decimal("23.96"))
        self.assertEqual(self.devis.montant_total_ht, Decimal("157.67"))


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
        self.assertAlmostEqual(float(resultat["montant_ht"]), 36)
        self.assertAlmostEqual(float(resultat["prix_vente_unitaire"]), 12)

    def test_article_fabrique_matiere_et_operations(self):
        # matière : coût = (2*10)*2 = 40 ; prix = 40*1.25 = 50
        # opération : 6 min/pièce * 2 pièces = 12 min -> 12/60*60 = 12 ; prix = 12*1.10 = 13.2
        resultat = previsualiser_ligne_commande(self.fabrique, 2, datetime.date(2026, 1, 1))
        self.assertAlmostEqual(float(resultat["montant_ht"]), 50 + 13.2)
        self.assertAlmostEqual(float(resultat["prix_vente_unitaire"]), (50 + 13.2) / 2)

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
        # Le devis ne crée plus que la commande : les OF se créent ensuite depuis la commande.
        self.assertEqual(commande.ordres_fabrication.count(), 0)

        ordres = creer_ordres_fabrication(commande)
        self.assertEqual(len(ordres), 1)
        of = ordres[0]
        self.assertEqual(of.article, self.article_fabrique)
        self.assertEqual(of.quantite, 2)
        self.assertEqual(list(of.lignes_commande.all()), list(commande.lignes.filter(article=self.article_fabrique)))

        operation = of.operations.get(ordre=1)
        # (5 + 2*2) = 9
        self.assertAlmostEqual(float(operation.temps_prevu), 9)

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
        of = creer_ordres_fabrication(lancer_en_production(self.devis))[0]
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
            "_continue": "Enregistrer",
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

    def test_plusieurs_lots_sont_consommes_en_fifo(self):
        emplacement1 = Emplacement.objects.create(code="EMP-LIV-1")
        emplacement2 = Emplacement.objects.create(code="EMP-LIV-2")
        ancien = Lot.objects.create(article=self.article, emplacement=emplacement1, quantite=5)
        recent = Lot.objects.create(article=self.article, emplacement=emplacement2, quantite=5)

        LivraisonLigne.objects.create(
            livraison=self._livraison(), commande_ligne=self.commande_ligne, quantite_livree=6
        )

        ancien.refresh_from_db()
        recent.refresh_from_db()
        self.assertEqual((ancien.quantite, recent.quantite), (0, 4))
        self.assertEqual(MouvementStock.objects.filter(reference_origine="LIVRAISON-LIV-1").count(), 2)

    def test_stock_total_insuffisant_sur_plusieurs_lots_annule_tout(self):
        emplacement1 = Emplacement.objects.create(code="EMP-LIV-1")
        emplacement2 = Emplacement.objects.create(code="EMP-LIV-2")
        ancien = Lot.objects.create(article=self.article, emplacement=emplacement1, quantite=5)
        recent = Lot.objects.create(article=self.article, emplacement=emplacement2, quantite=3)

        livraison = self._livraison()
        with self.assertRaises(LivraisonError):
            LivraisonLigne.objects.create(
                livraison=livraison, commande_ligne=self.commande_ligne, quantite_livree=9
            )

        # Tout ou rien : ni la ligne, ni le cumul livré, ni aucune sortie de
        # stock partielle ne doivent subsister — voir LivraisonLigne.save().
        self.assertEqual(LivraisonLigne.objects.count(), 0)
        self.commande_ligne.refresh_from_db()
        self.assertEqual(self.commande_ligne.quantite_livree, 0)
        ancien.refresh_from_db()
        recent.refresh_from_db()
        self.assertEqual((ancien.quantite, recent.quantite), (5, 3))
        self.assertFalse(MouvementStock.objects.filter(reference_origine="LIVRAISON-LIV-1").exists())


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
        self.assertTrue(kwargs["headers"]["Idempotency-Key"].startswith(f"{self.of.numero}-"))

    @override_settings(PLANNING_API_URL="http://planning-atelier.local/api")
    def test_synchronisation_http_en_erreur_est_geree(self):
        import requests

        with patch("chiffrage.planning_sync.requests.post", side_effect=requests.ConnectionError("down")):
            with self.assertRaises(PlanningSyncError):
                from .planning_sync import PlanningSyncClient

                PlanningSyncClient().envoyer_ordre_fabrication(self.of)

    def test_echec_enregistre_l_erreur_et_programme_une_reprise(self):
        tenter_synchronisation(self.of)
        self.of.refresh_from_db()
        self.assertIn("PLANNING_API_URL", self.of.derniere_erreur)
        self.assertGreater(self.of.prochaine_tentative, timezone.now())

    def test_delai_de_reprise_croissant_et_plafonne(self):
        from .planning_sync import delai_reprise

        self.assertEqual(delai_reprise(1), datetime.timedelta(minutes=5))
        self.assertEqual(delai_reprise(3), datetime.timedelta(minutes=20))
        self.assertEqual(delai_reprise(30), datetime.timedelta(hours=6))

    @override_settings(PLANNING_API_URL="http://planning-atelier.local/api")
    def test_cle_d_idempotence_stable_puis_changee_si_contenu_modifie(self):
        cles = []
        with patch("chiffrage.planning_sync.requests.post") as mock_post:
            mock_post.return_value.raise_for_status.return_value = None
            tenter_synchronisation(self.of)
            tenter_synchronisation(self.of)
            self.of.quantite = 9
            self.of.save()
            tenter_synchronisation(self.of)
            cles = [c.kwargs["headers"]["Idempotency-Key"] for c in mock_post.call_args_list]
        self.assertEqual(cles[0], cles[1])
        self.assertNotEqual(cles[1], cles[2])

    @override_settings(PLANNING_API_URL="http://planning-atelier.local/api")
    def test_reussite_efface_l_erreur_et_la_reprise(self):
        tenter_synchronisation(self.of)  # échec simulé : requête réelle impossible -> patch ci-dessous
        with patch("chiffrage.planning_sync.requests.post") as mock_post:
            mock_post.return_value.raise_for_status.return_value = None
            self.assertTrue(tenter_synchronisation(self.of))
        self.of.refresh_from_db()
        self.assertEqual(self.of.derniere_erreur, "")
        self.assertIsNone(self.of.prochaine_tentative)
        self.assertTrue(self.of.empreinte_envoyee)

    @override_settings(PLANNING_API_URL="http://planning-atelier.local/api")
    def test_of_modifie_apres_envoi_est_detecte_comme_perime(self):
        from .planning_sync import a_resynchroniser

        with patch("chiffrage.planning_sync.requests.post") as mock_post:
            mock_post.return_value.raise_for_status.return_value = None
            tenter_synchronisation(self.of)
        self.of.refresh_from_db()
        self.assertFalse(a_resynchroniser(self.of))
        self.of.quantite = 12
        self.of.save()
        self.assertTrue(a_resynchroniser(self.of))

    def test_empreinte_vide_historique_n_est_pas_consideree_perimee(self):
        from .planning_sync import a_resynchroniser

        self.of.statut_synchro = OrdreFabrication.StatutSynchro.SYNCHRONISE
        self.of.save()
        self.assertFalse(a_resynchroniser(self.of))


class CommandeRetrySynchroTests(TestCase):
    """retry_sync_ordres_fabrication : respect du délai, OF modifiés, échecs persistants."""

    def setUp(self):
        PlanningSyncTests.setUp(self)
        from io import StringIO

        self.out = StringIO()

    def _lancer(self, *args):
        from django.core.management import call_command

        call_command("retry_sync_ordres_fabrication", *args, stdout=self.out)
        self.of.refresh_from_db()

    @override_settings(PLANNING_API_URL="http://planning-atelier.local/api")
    def test_reprise_differee_tant_que_le_delai_n_est_pas_ecoule(self):
        self.of.prochaine_tentative = timezone.now() + datetime.timedelta(hours=1)
        self.of.save()
        with patch("chiffrage.planning_sync.requests.post") as mock_post:
            mock_post.return_value.raise_for_status.return_value = None
            self._lancer()
        mock_post.assert_not_called()
        self.assertEqual(self.of.statut_synchro, OrdreFabrication.StatutSynchro.EN_ATTENTE)

    @override_settings(PLANNING_API_URL="http://planning-atelier.local/api")
    def test_reprise_apres_delai(self):
        self.of.prochaine_tentative = timezone.now() - datetime.timedelta(minutes=1)
        self.of.save()
        with patch("chiffrage.planning_sync.requests.post") as mock_post:
            mock_post.return_value.raise_for_status.return_value = None
            self._lancer()
        self.assertEqual(self.of.statut_synchro, OrdreFabrication.StatutSynchro.SYNCHRONISE)

    @override_settings(PLANNING_API_URL="http://planning-atelier.local/api")
    def test_of_synchronise_puis_modifie_est_renvoye(self):
        with patch("chiffrage.planning_sync.requests.post") as mock_post:
            mock_post.return_value.raise_for_status.return_value = None
            tenter_synchronisation(self.of)
            self.of.quantite = 7
            self.of.save()
            self._lancer()
            self.assertEqual(mock_post.call_count, 2)
            self.assertEqual(mock_post.call_args.kwargs["json"]["quantite"], 7)

    @override_settings(PLANNING_API_URL="http://planning-atelier.local/api")
    def test_echecs_persistants_ignores_sauf_option(self):
        self.of.statut_synchro = OrdreFabrication.StatutSynchro.ECHEC_PERSISTANT
        self.of.nombre_tentatives = 5
        self.of.save()
        with patch("chiffrage.planning_sync.requests.post") as mock_post:
            mock_post.return_value.raise_for_status.return_value = None
            self._lancer()
            mock_post.assert_not_called()
            self._lancer("--inclure-echecs")
            mock_post.assert_called_once()
        self.assertEqual(self.of.statut_synchro, OrdreFabrication.StatutSynchro.SYNCHRONISE)


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

    def test_get_redirige_vers_l_onglet_lignes_de_la_fiche(self):
        response = self.client.get(f"/admin/chiffrage/devis/{self.devis.pk}/constructeur/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, f"/admin/chiffrage/devis/{self.devis.pk}/change/#onglet=lignes")

    def test_la_fiche_porte_l_assistant_ajouter_une_ligne(self):
        response = self.client.get(f"/admin/chiffrage/devis/{self.devis.pk}/change/")
        self.assertContains(response, 'id="lignes-constructeur"')
        self.assertContains(response, 'id="gamme-editeur"')  # opérations : éditeur partagé
        self.assertContains(response, "Ajouter une ligne")
        # date_creation du devis exposée au JS (devis_builder.js) : les nouvelles étapes de gamme sont datées de la date de création du
        # DEVIS, pas du jour — sinon une étape plus récente que le devis serait exclue du calcul (prix d'opérations à 0).
        self.assertContains(response, '"date_creation": "2026-01-01"')
        self.devis.statut = Devis.Statut.VALIDE
        self.devis.save()
        self.assertNotContains(self.client.get(f"/admin/chiffrage/devis/{self.devis.pk}/change/"), 'id="lignes-constructeur"')

    def test_apercu_ne_cree_rien(self):
        payload = {
            "quantite": 3, "apercu": True,
            "nouvel_article": {
                "reference": "PIECE-APERCU", "taux_marge_defaut": 10,
                "composants": [{"article_composant": "VIS-VIEW", "quantite": 5}],
                "etapes": [{"poste": "Poste-View", "ordre": 1, "cout_forfaitaire": 50, "date_debut": "2026-01-01"}],
            },
        }
        reponse = self.client.post(f"/admin/chiffrage/devis/{self.devis.pk}/constructeur/", data=payload, content_type="application/json")
        self.assertEqual(reponse.status_code, 200, reponse.content)
        donnees = reponse.json()
        self.assertTrue(donnees["apercu"])
        self.assertIsNotNone(donnees["prix_vente_total"])
        self.assertFalse(Article.objects.filter(pk="PIECE-APERCU").exists())
        self.assertEqual(self.devis.lignes.count(), 0)

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
        self.assertAlmostEqual(float(data["prix_vente_operations"]), 150)
        self.assertAlmostEqual(float(data["prix_vente_total"]), 153.3, places=3)
        self.assertAlmostEqual(float(data["montant_total_ht"]), 153.3, places=3)

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
        self.assertAlmostEqual(float(data["prix_vente_matiere"]), 133.71)
        self.assertAlmostEqual(float(data["prix_vente_operations"]), 23.96)
        self.assertAlmostEqual(float(data["prix_vente_total"]), 133.71 + 23.96)
        self.assertAlmostEqual(float(data["montant_total_ht"]), 133.71 + 23.96)


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
        self.assertAlmostEqual(float(resultat["prix_vente_matiere"]), 133.71)
        self.assertAlmostEqual(float(resultat["prix_vente_operations"]), 23.96)
        self.assertAlmostEqual(float(resultat["prix_vente_total"]), 133.71 + 23.96)
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
        self.assertAlmostEqual(float(data["prix_vente_matiere"]), 11)
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
        self.assertAlmostEqual(float(response.json()["prix_vente_matiere"]), 11)

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
        self.assertAlmostEqual(float(ligne.prix_vente_ttc), 21.1, places=3)

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
        self.assertAlmostEqual(float(self.devis.montant_total_ttc), 12 + 21.1, places=3)

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
        self.assertAlmostEqual(float(data["prix_vente_ttc"]), 21.1, places=3)
        self.assertAlmostEqual(float(data["montant_total_ttc"]), 21.1, places=3)
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
            "numero": "DEV-CONSTRUIRE-01", "client": self.client_tiers.pk, "date_creation": "2026-01-01", "statut": Devis.Statut.BROUILLON,
            "taux_marge_globale": "", "adresse_facturation": "", "adresse_livraison": "", "contact": "",
            "lignes-TOTAL_FORMS": "0", "lignes-INITIAL_FORMS": "0", "lignes-MIN_NUM_FORMS": "0", "lignes-MAX_NUM_FORMS": "1000",
        }

    def test_creation_puis_fiche_avec_les_onglets(self):
        data = self._formulaire_de_base()
        data["_continue"] = "1"
        response = self.client.post("/admin/chiffrage/devis/add/", data=data, follow=False)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/admin/chiffrage/devis/DEV-CONSTRUIRE-01/change/")
        self.assertContains(self.client.get(response.url), 'id="lignes-constructeur"')

    def test_formulaire_d_ajout_sans_bouton_constructeur_et_marqueur_nouveau(self):
        page = self.client.get("/admin/chiffrage/devis/add/")
        self.assertNotContains(page, "_construire")
        self.assertContains(page, 'id="devis-nouveau"')

    def test_le_tableau_ne_sert_plus_a_ajouter_des_lignes(self):
        """Les lignes s'ajoutent par l'assistant ; le tableau (inline) ne crée plus de ligne."""
        data = self._formulaire_de_base()
        data.update({"lignes-TOTAL_FORMS": "1", "lignes-0-article": self.article.pk, "lignes-0-quantite": "4", "lignes-0-id": "", "lignes-0-devis": "", "_save": "1"})
        self.client.post("/admin/chiffrage/devis/add/", data=data, follow=False)
        self.assertEqual(Devis.objects.get(pk="DEV-CONSTRUIRE-01").lignes.count(), 0)


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
        self.assertAlmostEqual(float(data["prix_vente_unitaire"]), 11)
        self.assertEqual(data["taux_tva_suggere"]["id"], self.taux_reduit.pk)

        ligne.refresh_from_db()
        self.assertAlmostEqual(float(ligne.prix_vente_unitaire), 11)
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
        self.assertAlmostEqual(float(ligne.prix_vente_unitaire), 99.0)

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
        self.assertAlmostEqual(float(ligne.prix_vente_unitaire), 42)

    def test_previsualiser_ligne_commande_existante(self):
        response = self.client.post(
            f"/admin/chiffrage/commande/{self.commande.pk}/lignes/previsualiser/",
            data=json.dumps({"article": self.article.pk, "quantite": "4"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        # coût = 10*4=40 ; prix = 40*1.10=44 ; unitaire = 11
        self.assertAlmostEqual(float(data["prix_vente_unitaire"]), 11)
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
        self.assertAlmostEqual(float(data["prix_vente_unitaire"]), 11)
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

    def test_stock_insuffisant_affiche_sans_500(self):
        emplacement1 = Emplacement.objects.create(code="EMP-LIV-ADMIN-1")
        emplacement2 = Emplacement.objects.create(code="EMP-LIV-ADMIN-2")
        Lot.objects.create(article=self.article, emplacement=emplacement1, quantite=2)
        Lot.objects.create(article=self.article, emplacement=emplacement2, quantite=2)

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
        self.assertContains(response, "Stock insuffisant")
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
        self.assertAlmostEqual(float(self.ligne.montant_ttc), 55.0)

        self.ligne.prix_vente_unitaire = 8.0
        self.ligne.save()
        self.assertEqual(self.ligne.montant_ht, 80.0)
        self.assertAlmostEqual(float(self.ligne.montant_ttc), 88.0)

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
        self.assertAlmostEqual(float(operation.temps_prevu), 15)

    def test_refuse_si_article_pas_fabrique(self):
        ligne = CommandeLigne.objects.create(
            commande=self.commande, article=self.article_mp, quantite_commandee=5,
        )
        with self.assertRaises(ChiffrageError):
            lancer_ligne_en_production(ligne)

    def test_refuse_si_la_ligne_a_deja_un_of(self):
        ligne = CommandeLigne.objects.create(
            commande=self.commande, article=self.article_fabrique, quantite_commandee=5,
        )
        lancer_ligne_en_production(ligne)
        with self.assertRaises(ChiffrageError):
            lancer_ligne_en_production(ligne)

    def test_une_autre_ligne_du_meme_article_a_son_propre_of(self):
        premiere = CommandeLigne.objects.create(
            commande=self.commande, article=self.article_fabrique, quantite_commandee=5,
        )
        lancer_ligne_en_production(premiere)
        seconde = CommandeLigne.objects.create(
            commande=self.commande, article=self.article_fabrique, quantite_commandee=2,
        )
        of = lancer_ligne_en_production(seconde)
        self.assertEqual((of.quantite, list(of.lignes_commande.all())), (2, [seconde]))


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
        self.assertEqual(modification.ancienne_valeur, "5")
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
        of = OrdreFabrication.objects.get(commande=self.commande, article=self.article_fabrique)
        self.assertContains(response, f'href="/admin/chiffrage/ordrefabrication/{of.pk}/change/"')  # le bandeau ouvre l'OF

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


class _FixtureModuleA:
    """Client avec adresses principales, article matière, devis (brouillon par
    défaut) à une ligne — socle commun aux tests de recette du module A."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        self.user = get_user_model().objects.create_superuser("recette-a", "ra@example.com", "pass1234")
        self.client.force_login(self.user)
        self.tiers = Tiers.objects.create(code="CLI-REC-A", raison_sociale="Client Recette A", type_tiers=Tiers.TypeTiers.CLIENT)
        for champ in ("est_facturation", "est_livraison"):
            Adresse.objects.create(
                tiers=self.tiers, adresse="1 rue", code_postal="75000", ville="Paris",
                est_principale=True, **{champ: True},
            )
        self.article = Article.objects.create(
            reference="ART-REC-A", nature=Article.Nature.MATIERE_PREMIERE,
            unite_cout=Article.UniteCout.PIECE, cout_unitaire=10, taux_marge_defaut=10,
        )
        self.devis = Devis.objects.create(
            numero="DEV-REC-A", client=self.tiers, date_creation=datetime.date(2026, 1, 1),
        )
        self.ligne = DevisLigne.objects.create(devis=self.devis, article=self.article, quantite=3)

    def _valider(self):
        self.devis.statut = Devis.Statut.VALIDE
        self.devis.save()


class ValidationDesSaisiesTests(_FixtureModuleA, TestCase):
    """A-NV-02 : quantités et prix absurdes rejetés dès la validation de modèle
    (donc par l'admin, les vues AJAX et l'API, qui passent toutes par full_clean)."""

    def _erreurs(self, instance):
        with self.assertRaises(ValidationError) as cm:
            instance.full_clean()
        return cm.exception.message_dict

    def test_quantite_de_ligne_devis_doit_etre_strictement_positive(self):
        for valeur in (0, -5):
            ligne = DevisLigne(devis=self.devis, article=self.article, quantite=valeur)
            self.assertIn("quantite", self._erreurs(ligne), valeur)

    def test_prix_force_et_marges_negatifs_refuses(self):
        ligne = DevisLigne(
            devis=self.devis, article=self.article, quantite=1,
            prix_vente_unitaire_force=-1, taux_marge_matiere_applique=-10,
        )
        erreurs = self._erreurs(ligne)
        self.assertIn("prix_vente_unitaire_force", erreurs)
        self.assertIn("taux_marge_matiere_applique", erreurs)

    def test_marge_globale_du_devis_negative_refusee(self):
        devis = Devis(numero="DEV-NEG", client=self.tiers, date_creation=datetime.date(2026, 1, 1), taux_marge_globale=-5)
        self.assertIn("taux_marge_globale", self._erreurs(devis))

    def test_ligne_de_commande_refuse_quantite_ou_prix_negatifs(self):
        self._valider()
        commande = lancer_en_production(self.devis)
        ligne = commande.lignes.get()
        ligne.quantite_commandee = -1
        ligne.prix_vente_unitaire = -3
        erreurs = self._erreurs(ligne)
        self.assertIn("quantite_commandee", erreurs)
        self.assertIn("prix_vente_unitaire", erreurs)

    def test_api_refuse_une_quantite_negative(self):
        response = self.client.post(
            "/api/v1/devis-lignes/",
            data=json.dumps({"devis": self.devis.pk, "article": self.article.pk, "quantite": -2}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400, response.content)

    def test_valeurs_valides_toujours_acceptees(self):
        ligne = DevisLigne(
            devis=self.devis, article=self.article, quantite=0.5,
            prix_vente_unitaire_force=0, taux_marge_matiere_applique=0,
        )
        ligne.full_clean()


class DevisValideVerrouilleTests(_FixtureModuleA, TestCase):
    """A-OP-02 : un devis validé est le prix engagé auprès du client — il ne
    se modifie plus sans repasser explicitement en brouillon."""

    def setUp(self):
        super().setUp()
        self._valider()

    def test_formulaire_admin_rend_les_champs_en_lecture_seule(self):
        response = self.client.get(f"/admin/chiffrage/devis/{self.devis.pk}/change/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="delai"')
        self.assertNotContains(response, 'name="taux_marge_globale"')
        self.assertContains(response, 'name="statut"')

    def test_inline_lignes_sans_ajout_ni_suppression(self):
        response = self.client.get(f"/admin/chiffrage/devis/{self.devis.pk}/change/")
        self.assertNotContains(response, 'name="lignes-0-quantite"')
        self.assertNotContains(response, "Ajouter un autre")

    def test_post_ne_modifie_pas_un_devis_valide(self):
        self.client.post(
            f"/admin/chiffrage/devis/{self.devis.pk}/change/",
            {"statut": "valide", "taux_marge_globale": "99", "lignes-TOTAL_FORMS": "0", "lignes-INITIAL_FORMS": "0"},
        )
        self.devis.refresh_from_db()
        self.assertIsNone(self.devis.taux_marge_globale)

    def test_repasser_en_brouillon_possible_sans_commande(self):
        self.client.post(
            f"/admin/chiffrage/devis/{self.devis.pk}/change/",
            {"statut": "brouillon", "lignes-TOTAL_FORMS": "1", "lignes-INITIAL_FORMS": "1",
             "lignes-0-id": self.ligne.pk, "lignes-0-devis": self.devis.pk},
        )
        self.devis.refresh_from_db()
        self.assertEqual(self.devis.statut, Devis.Statut.BROUILLON)

    def test_repasser_en_brouillon_refuse_si_commande_existante(self):
        lancer_en_production(self.devis)
        response = self.client.post(
            f"/admin/chiffrage/devis/{self.devis.pk}/change/",
            {"statut": "brouillon", "lignes-TOTAL_FORMS": "0", "lignes-INITIAL_FORMS": "0"},
        )
        self.assertContains(response, "commande est déjà issue")
        self.devis.refresh_from_db()
        self.assertEqual(self.devis.statut, Devis.Statut.VALIDE)

    def test_action_recalculer_ignore_un_devis_valide(self):
        self.ligne.refresh_from_db()
        avant = self.ligne.prix_vente_matiere
        self.article.cout_unitaire = 999
        self.article.save()
        response = self.client.post(
            "/admin/chiffrage/devis/",
            {"action": "action_recalculer", "_selected_action": [self.devis.pk]}, follow=True,
        )
        self.assertContains(response, "verrouillé")
        self.ligne.refresh_from_db()
        self.assertEqual(self.ligne.prix_vente_matiere, avant)

    def test_recalcul_live_dune_ligne_refuse(self):
        response = self.client.post(
            f"/admin/chiffrage/devis/{self.devis.pk}/lignes/{self.ligne.pk}/recalculer/",
            data=json.dumps({"quantite": "50"}), content_type="application/json",
        )
        self.assertEqual(response.status_code, 409)
        self.ligne.refresh_from_db()
        self.assertEqual(self.ligne.quantite, 3)

    def test_api_refuse_modification_et_suppression_de_ligne(self):
        url = f"/api/v1/devis-lignes/{self.ligne.pk}/"
        response = self.client.patch(url, data=json.dumps({"quantite": 99}), content_type="application/json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(self.client.delete(url).status_code, 400)
        self.ligne.refresh_from_db()
        self.assertEqual(self.ligne.quantite, 3)

    def test_api_refuse_de_deplacer_une_ligne_hors_dun_devis_valide(self):
        autre = Devis.objects.create(numero="DEV-AUTRE", client=self.tiers, date_creation=datetime.date(2026, 1, 1))
        response = self.client.patch(
            f"/api/v1/devis-lignes/{self.ligne.pk}/", data=json.dumps({"devis": autre.pk}), content_type="application/json"
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.ligne.refresh_from_db()
        self.assertEqual(self.ligne.devis_id, self.devis.pk)

    def test_api_refuse_ajout_de_ligne_et_modification_du_devis(self):
        response = self.client.post(
            "/api/v1/devis-lignes/",
            data=json.dumps({"devis": self.devis.pk, "article": self.article.pk, "quantite": 1}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400, response.content)
        response = self.client.patch(
            f"/api/v1/devis/{self.devis.pk}/", data=json.dumps({"delai": "hier"}), content_type="application/json"
        )
        self.assertEqual(response.status_code, 400, response.content)

    def test_api_recalculer_refuse(self):
        response = self.client.post(f"/api/v1/devis/{self.devis.pk}/recalculer/")
        self.assertEqual(response.status_code, 400)

    def test_api_repasse_en_brouillon_possible_sans_commande(self):
        response = self.client.patch(
            f"/api/v1/devis/{self.devis.pk}/", data=json.dumps({"statut": "brouillon"}), content_type="application/json"
        )
        self.assertEqual(response.status_code, 200, response.content)

    def test_brouillon_reste_librement_modifiable(self):
        self.devis.statut = Devis.Statut.BROUILLON
        self.devis.save()
        response = self.client.patch(
            f"/api/v1/devis-lignes/{self.ligne.pk}/", data=json.dumps({"quantite": 7}), content_type="application/json"
        )
        self.assertEqual(response.status_code, 200, response.content)


class LancementEnProductionRobusteTests(_FixtureModuleA, TestCase):
    """A-OP-06 / A-NV-01 : lancer deux fois (double-clic) ne doit jamais
    produire une erreur 500 ni une commande en double."""

    def setUp(self):
        super().setUp()
        self._valider()

    def test_second_lancement_refuse_proprement(self):
        lancer_en_production(self.devis)
        with self.assertRaises(ChiffrageError):
            lancer_en_production(self.devis)
        self.assertEqual(Commande.objects.filter(devis=self.devis).count(), 1)

    def test_numero_de_commande_deja_pris_donne_une_erreur_metier(self):
        Commande.objects.create(
            numero=f"CDE-{self.devis.numero}", client=self.tiers, reference_client="X",
            date_commande=datetime.date(2026, 1, 1),
            adresse_facturation=self.tiers.adresses.first(), adresse_livraison=self.tiers.adresses.first(),
        )
        with self.assertRaises(ChiffrageError):
            lancer_en_production(self.devis)
        self.assertFalse(Commande.objects.filter(devis=self.devis).exists())

    def test_double_post_de_laction_admin_ne_plante_pas(self):
        donnees = {"action": "action_lancer_en_production", "_selected_action": [self.devis.pk]}
        premier = self.client.post("/admin/chiffrage/devis/", donnees, follow=True)
        second = self.client.post("/admin/chiffrage/devis/", donnees, follow=True)
        self.assertEqual(premier.status_code, 200)
        commande = Commande.objects.get(devis=self.devis)
        self.assertContains(premier, f'href="/admin/chiffrage/commande/{commande.pk}/change/"')  # le bandeau ouvre la commande
        self.assertEqual(second.status_code, 200)
        self.assertContains(second, "déjà été lancé")
        self.assertEqual(Commande.objects.filter(devis=self.devis).count(), 1)

    def test_lancement_depuis_un_devis_brouillon_refuse(self):
        self.devis.statut = Devis.Statut.BROUILLON
        self.devis.save()
        with self.assertRaises(ChiffrageError):
            lancer_en_production(self.devis)


class StatutCommandeTests(_FixtureModuleA, TestCase):
    """A-OP-05 : la commande a un vrai cycle de vie (en cours / soldée / annulée)."""

    def setUp(self):
        super().setUp()
        self._valider()
        self.commande = lancer_en_production(self.devis)
        self.ligne_cde = self.commande.lignes.get()

    def _livrer(self, quantite, numero="LIV-REC-A"):
        livraison = Livraison.objects.create(numero=numero, commande=self.commande, date_livraison=datetime.date(2026, 2, 1))
        return LivraisonLigne.objects.create(livraison=livraison, commande_ligne=self.ligne_cde, quantite_livree=quantite)

    def test_une_commande_nouvelle_est_en_cours(self):
        self.assertEqual(self.commande.statut, Commande.Statut.EN_COURS)

    def test_livraison_partielle_laisse_en_cours_puis_solde(self):
        self._livrer(1, "LIV-1")
        self.commande.refresh_from_db()
        self.assertEqual(self.commande.statut, Commande.Statut.EN_COURS)
        self._livrer(2, "LIV-2")
        self.commande.refresh_from_db()
        self.assertEqual(self.commande.statut, Commande.Statut.SOLDEE)

    def test_annulation_possible_sans_livraison(self):
        self.commande.annuler()
        self.commande.refresh_from_db()
        self.assertEqual(self.commande.statut, Commande.Statut.ANNULEE)

    def test_annulation_refusee_apres_livraison(self):
        from .models import CommandeError

        self._livrer(1)
        with self.assertRaises(CommandeError):
            self.commande.annuler()
        self.commande.refresh_from_db()
        self.assertEqual(self.commande.statut, Commande.Statut.EN_COURS)

    def test_double_annulation_refusee(self):
        from .models import CommandeError

        self.commande.annuler()
        with self.assertRaises(CommandeError):
            self.commande.annuler()

    def test_livraison_refusee_sur_commande_annulee(self):
        self.commande.annuler()
        livraison = Livraison.objects.create(numero="LIV-ANN", commande=self.commande, date_livraison=datetime.date(2026, 2, 1))
        ligne = LivraisonLigne(livraison=livraison, commande_ligne=self.ligne_cde, quantite_livree=1)
        with self.assertRaises(ValidationError):
            ligne.full_clean()

    def test_action_admin_annuler(self):
        response = self.client.post(
            "/admin/chiffrage/commande/",
            {"action": "action_annuler", "_selected_action": [self.commande.pk]}, follow=True,
        )
        self.assertContains(response, "commande annulée")
        self.commande.refresh_from_db()
        self.assertEqual(self.commande.statut, Commande.Statut.ANNULEE)

    def test_statut_non_editable_dans_la_fiche_admin(self):
        response = self.client.get(f"/admin/chiffrage/commande/{self.commande.pk}/change/")
        self.assertNotContains(response, 'name="statut"')

    def test_api_annuler_et_statut_en_lecture_seule(self):
        response = self.client.patch(
            f"/api/v1/commandes/{self.commande.pk}/", data=json.dumps({"statut": "annulee"}), content_type="application/json"
        )
        self.commande.refresh_from_db()
        self.assertEqual(self.commande.statut, Commande.Statut.EN_COURS)
        response = self.client.post(f"/api/v1/commandes/{self.commande.pk}/annuler/")
        self.assertEqual(response.status_code, 200, response.content)
        self.commande.refresh_from_db()
        self.assertEqual(self.commande.statut, Commande.Statut.ANNULEE)
        self.assertEqual(self.client.post(f"/api/v1/commandes/{self.commande.pk}/annuler/").status_code, 400)


class PisteDAuditTests(_FixtureModuleA, TestCase):
    """A-AD-02 : qui a modifié quoi, quand, avec l'ancienne et la nouvelle valeur,
    sur le devis, ses lignes et la commande (django-simple-history)."""

    def test_modification_du_devis_tracee_avec_utilisateur_et_valeurs(self):
        avant = self.devis.history.count()
        response = self.client.patch(
            f"/api/v1/devis/{self.devis.pk}/", data=json.dumps({"delai": "3 semaines"}), content_type="application/json"
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.devis.history.count(), avant + 1)
        derniere, precedente = self.devis.history.all()[0], self.devis.history.all()[1]
        self.assertEqual(derniere.history_user, self.user)
        changements = {c.field: (c.old, c.new) for c in derniere.diff_against(precedente).changes}
        self.assertEqual(changements["delai"], ("", "3 semaines"))

    def test_modification_de_ligne_tracee(self):
        self.client.patch(
            f"/api/v1/devis-lignes/{self.ligne.pk}/", data=json.dumps({"quantite": 8}), content_type="application/json"
        )
        derniere, precedente = self.ligne.history.all()[0], self.ligne.history.all()[1]
        changements = {c.field: (c.old, c.new) for c in derniere.diff_against(precedente).changes}
        self.assertEqual(changements["quantite"], (3.0, 8.0))
        self.assertEqual(derniere.history_user, self.user)

    def test_suppression_de_ligne_conservee_dans_lhistorique(self):
        pk = self.ligne.pk
        self.client.delete(f"/api/v1/devis-lignes/{pk}/")
        self.assertFalse(DevisLigne.objects.filter(pk=pk).exists())
        self.assertEqual(DevisLigne.history.filter(id=pk, history_type="-").count(), 1)

    def test_annulation_de_commande_tracee(self):
        self._valider()
        commande = lancer_en_production(self.devis)
        self.client.post(
            "/admin/chiffrage/commande/",
            {"action": "action_annuler", "_selected_action": [commande.pk]}, follow=True,
        )
        derniere = commande.history.first()
        self.assertEqual(derniere.statut, Commande.Statut.ANNULEE)
        self.assertEqual(derniere.history_user, self.user)

    def test_restauration_dune_version_precedente_impossible(self):
        self.client.patch(
            f"/api/v1/devis/{self.devis.pk}/", data=json.dumps({"delai": "ASAP"}), content_type="application/json"
        )
        ancienne = self.devis.history.last()
        response = self.client.post(f"/admin/chiffrage/devis/{self.devis.pk}/history/{ancienne.pk}/", {})
        self.assertEqual(response.status_code, 403)
        self.devis.refresh_from_db()
        self.assertEqual(self.devis.delai, "ASAP")

    def test_validation_refusee_laisse_une_trace_coherente(self):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Permission

        simple = get_user_model().objects.create_user("trace-a", "t@example.com", "pass1234", is_staff=True)
        simple.user_permissions.set(Permission.objects.filter(
            content_type__app_label="chiffrage",
            codename__in=["add_devis", "change_devis", "view_devis", "change_devisligne", "view_devisligne", "valider_devis"],
        ))
        self.ligne.prix_vente_unitaire_force = 1
        self.ligne.save()
        self.client.force_login(simple)
        self.client.post(
            f"/admin/chiffrage/devis/{self.devis.pk}/change/",
            {"numero": self.devis.pk, "client": self.tiers.pk, "date_creation": "2026-01-01", "statut": "valide",
             "taux_marge_globale": "", "adresse_facturation": "", "adresse_livraison": "", "contact": "",
             "lignes-TOTAL_FORMS": "1", "lignes-INITIAL_FORMS": "1", "lignes-MIN_NUM_FORMS": "0",
             "lignes-MAX_NUM_FORMS": "1000", "lignes-0-id": self.ligne.pk, "lignes-0-devis": self.devis.pk,
             "lignes-0-article": self.article.pk, "lignes-0-quantite": "3",
             "lignes-0-taux_marge_matiere_applique": "", "lignes-0-prix_vente_unitaire_force": "1",
             "lignes-0-taux_tva": "", "_save": "Enregistrer"},
        )
        self.devis.refresh_from_db()
        self.assertEqual(self.devis.statut, Devis.Statut.BROUILLON)
        dernier = self.devis.history.first()
        self.assertEqual(dernier.statut, Devis.Statut.BROUILLON)
        self.assertIn("Validation refusée", dernier.history_change_reason)
        self.assertIn(Devis.Statut.VALIDE, [h.statut for h in self.devis.history.all()])

    def test_page_historique_de_ladmin(self):
        self.client.patch(
            f"/api/v1/devis/{self.devis.pk}/", data=json.dumps({"delai": "ASAP"}), content_type="application/json"
        )
        response = self.client.get(f"/admin/chiffrage/devis/{self.devis.pk}/history/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "recette-a")


class ArrondisMontantsTests(_FixtureModuleA, TestCase):
    """A-TD-01 : tous les montants affichés sont au centime, et le total TTC est la
    somme des lignes arrondies (ce que la facture Tiime affichera)."""

    def test_devis_a_tva_mixte_total_ttc_est_la_somme_des_lignes_arrondies(self):
        tva20 = TauxTVA.objects.create(nom="N20 arrondi", taux=20)
        tva55 = TauxTVA.objects.create(nom="R55 arrondi", taux=5.5)
        for taux, prix in ((tva20, 3.333), (tva55, 3.333), (tva55, 7.777)):
            DevisLigne.objects.create(
                devis=self.devis, article=self.article, quantite=3,
                prix_vente_unitaire_force=prix, taux_tva=taux,
            )
        DevisLigne.objects.filter(pk=self.ligne.pk).delete()
        calculer_devis(self.devis)
        lignes = list(self.devis.lignes.all())
        for ligne in lignes:
            self.assertEqual(ligne.prix_vente_ttc, round(ligne.prix_vente_ttc, 2))
        attendu = round(sum(l.prix_vente_ttc for l in lignes), 2)
        self.assertEqual(self.devis.montant_total_ttc, attendu)
        self.assertEqual(self.devis.montant_total_ht, round(self.devis.montant_total_ht, 2))

    def test_montants_de_ligne_de_commande_au_centime(self):
        self._valider()
        commande = lancer_en_production(self.devis)
        tva = TauxTVA.objects.create(nom="T20 arrondi cde", taux=20)
        ligne = commande.lignes.get()
        ligne.prix_vente_unitaire = 3.333
        ligne.quantite_commandee = 3
        ligne.taux_tva = tva
        ligne.save()
        self.assertEqual(ligne.montant_ht, 10.0)  # 9.999 -> 10.00
        self.assertEqual(ligne.montant_ttc, 12.0)  # 10.00 * 1.2, sans résidu flottant

    def test_somme_de_flottants_sans_residu(self):
        # 0.1 + 0.2 ne doit pas afficher 0.30000000000000004
        tiers = self.tiers
        devis = Devis.objects.create(numero="DEV-FLOAT", client=tiers, date_creation=datetime.date(2026, 1, 1))
        for prix in (0.1, 0.2):
            DevisLigne.objects.create(devis=devis, article=self.article, quantite=1, prix_vente_unitaire_force=prix)
        calculer_devis(devis)
        self.assertEqual(devis.montant_total_ht, Decimal("0.30"))


class ValidationDuDevisTests(_FixtureModuleA, TestCase):
    """A-MG-01 : on ne valide pas n'importe quel devis — vide, non chiffrable ou
    vendu sous le coût sans habilitation — et seul un utilisateur habilité valide."""

    def _devis_brouillon(self, **champs_ligne):
        """Devis en brouillon avec une ligne (les lignes ne se créent plus par le tableau de la fiche : voir l'assistant « Ajouter une ligne »)."""
        devis = Devis.objects.create(numero="DEV-VALID-01", client=self.tiers, date_creation=datetime.date(2026, 1, 1))
        ligne = DevisLigne.objects.create(devis=devis, article=self.article, quantite=3, **champs_ligne)
        return devis, ligne

    def _formulaire(self, ligne, **champs):
        data = {
            "numero": "DEV-VALID-01", "client": self.tiers.pk, "date_creation": "2026-01-01",
            "statut": Devis.Statut.VALIDE, "taux_marge_globale": "", "adresse_facturation": "",
            "adresse_livraison": "", "contact": "",
            "lignes-TOTAL_FORMS": "1", "lignes-INITIAL_FORMS": "1",
            "lignes-MIN_NUM_FORMS": "0", "lignes-MAX_NUM_FORMS": "1000",
            "lignes-0-article": self.article.pk, "lignes-0-quantite": "3",
            "lignes-0-taux_marge_matiere_applique": "", "lignes-0-prix_vente_unitaire_force": "",
            "lignes-0-taux_tva": "", "lignes-0-id": ligne.pk, "lignes-0-devis": ligne.devis_id, "_save": "Enregistrer",
        }
        data.update({f"lignes-0-{cle}": valeur for cle, valeur in champs.items()})
        return data

    def _utilisateur_habilite(self, *, sous_cout=False):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Permission

        codes = ["add_devis", "change_devis", "view_devis", "add_devisligne", "change_devisligne",
                 "view_devisligne", "delete_devisligne", "valider_devis"]
        if sous_cout:
            codes.append("valider_vente_sous_cout")
        utilisateur = get_user_model().objects.create_user(
            f"habilite-{int(sous_cout)}", "h@example.com", "pass1234", is_staff=True
        )
        utilisateur.user_permissions.set(Permission.objects.filter(content_type__app_label="chiffrage", codename__in=codes))
        return utilisateur

    def test_devis_normal_valide_et_chiffre_automatiquement(self):
        devis, ligne = self._devis_brouillon()
        response = self.client.post(f"/admin/chiffrage/devis/{devis.pk}/change/", self._formulaire(ligne), follow=True)
        devis.refresh_from_db()
        self.assertEqual(devis.statut, Devis.Statut.VALIDE, response.content.decode()[:0])
        self.assertIsNotNone(devis.lignes.get().prix_vente_matiere)

    def test_devis_vendu_sous_le_cout_refuse_sans_permission(self):
        self.client.force_login(self._utilisateur_habilite())
        devis, ligne = self._devis_brouillon()
        response = self.client.post(
            f"/admin/chiffrage/devis/{devis.pk}/change/", self._formulaire(ligne, prix_vente_unitaire_force="1"), follow=True
        )
        devis = Devis.objects.get(pk="DEV-VALID-01")
        self.assertEqual(devis.statut, Devis.Statut.BROUILLON)
        self.assertContains(response, "vendu sous le coût")

    def test_devis_vendu_sous_le_cout_accepte_avec_permission(self):
        self.client.force_login(self._utilisateur_habilite(sous_cout=True))
        devis, ligne = self._devis_brouillon()
        self.client.post(f"/admin/chiffrage/devis/{devis.pk}/change/", self._formulaire(ligne, prix_vente_unitaire_force="1"), follow=True)
        self.assertEqual(Devis.objects.get(pk="DEV-VALID-01").statut, Devis.Statut.VALIDE)

    def test_utilisateur_sans_permission_de_validation_ne_voit_pas_le_statut(self):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Permission

        simple = get_user_model().objects.create_user("simple-a", "s@example.com", "pass1234", is_staff=True)
        simple.user_permissions.set(
            Permission.objects.filter(content_type__app_label="chiffrage", codename__in=["add_devis", "view_devis", "change_devis"])
        )
        self.client.force_login(simple)
        response = self.client.get("/admin/chiffrage/devis/add/")
        self.assertNotContains(response, 'name="statut"')

    def test_forcer_le_statut_sans_permission_est_ignore(self):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Permission

        simple = get_user_model().objects.create_user("simple-b", "s2@example.com", "pass1234", is_staff=True)
        simple.user_permissions.set(
            Permission.objects.filter(
                content_type__app_label="chiffrage",
                codename__in=["add_devis", "view_devis", "change_devis", "add_devisligne", "change_devisligne", "view_devisligne"],
            )
        )
        self.client.force_login(simple)
        devis, ligne = self._devis_brouillon()
        self.client.post(f"/admin/chiffrage/devis/{devis.pk}/change/", self._formulaire(ligne), follow=True)
        self.assertEqual(Devis.objects.get(pk="DEV-VALID-01").statut, Devis.Statut.BROUILLON)

    def test_devis_sans_ligne_ne_peut_pas_etre_valide(self):
        self.devis.lignes.all().delete()
        raisons = verifier_validation_devis(self.devis)
        self.assertEqual(raisons, ["Le devis ne contient aucune ligne."])

    def test_ligne_non_chiffrable_bloque_la_validation(self):
        article_sans_cout = Article.objects.create(reference="ART-SANS-COUT", nature=Article.Nature.MATIERE_PREMIERE)
        DevisLigne.objects.create(devis=self.devis, article=article_sans_cout, quantite=1)
        raisons = verifier_validation_devis(self.devis)
        self.assertTrue(raisons)
        self.assertTrue(any("Chiffrage impossible" in r or "non chiffrée" in r for r in raisons), raisons)

    def test_api_refuse_la_validation_sous_le_cout_et_annule_tout(self):
        self.client.force_login(self._utilisateur_habilite())
        self.ligne.prix_vente_unitaire_force = 1
        self.ligne.save()
        response = self.client.patch(
            f"/api/v1/devis/{self.devis.pk}/", data=json.dumps({"statut": "valide"}), content_type="application/json"
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("sous le coût", response.content.decode())
        self.devis.refresh_from_db()
        self.assertEqual(self.devis.statut, Devis.Statut.BROUILLON)

    def test_api_valide_un_devis_normal_et_le_chiffre(self):
        self.client.force_login(self._utilisateur_habilite())
        response = self.client.patch(
            f"/api/v1/devis/{self.devis.pk}/", data=json.dumps({"statut": "valide"}), content_type="application/json"
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.devis.refresh_from_db()
        self.assertEqual(self.devis.statut, Devis.Statut.VALIDE)
        self.assertIsNotNone(self.devis.lignes.get().prix_vente_matiere)

    def test_api_refuse_la_validation_sans_permission(self):
        from django.contrib.auth import get_user_model

        get_user_model().objects.create_user("api-simple", "x@example.com", "pass1234")
        self.client.force_login(get_user_model().objects.get(username="api-simple"))
        response = self.client.patch(
            f"/api/v1/devis/{self.devis.pk}/", data=json.dumps({"statut": "valide"}), content_type="application/json"
        )
        self.assertEqual(response.status_code, 403, response.content)

    def test_annulation_de_commande_exige_la_permission(self):
        self._valider()
        commande = lancer_en_production(self.devis)
        from django.contrib.auth import get_user_model

        get_user_model().objects.create_user("sans-droit", "z@example.com", "pass1234", is_staff=True)
        self.client.force_login(get_user_model().objects.get(username="sans-droit"))
        response = self.client.post(f"/api/v1/commandes/{commande.pk}/annuler/")
        self.assertEqual(response.status_code, 403)
        commande.refresh_from_db()
        self.assertEqual(commande.statut, Commande.Statut.EN_COURS)


class RolesMetierModuleATests(_FixtureModuleA, TestCase):
    """A-AD-01 / D-AD-02 : rôles prédéfinis (Commercial, Responsable commercial,
    Direction, Atelier) et API soumise aux permissions du modèle."""

    def _utilisateur(self, nom, groupe=None, *, staff=True):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Group

        utilisateur = get_user_model().objects.create_user(nom, f"{nom}@example.com", "pass1234", is_staff=staff)
        if groupe:
            utilisateur.groups.add(Group.objects.get(name=groupe))
        return utilisateur

    def _patch(self, url, donnees):
        return self.client.patch(url, data=json.dumps(donnees), content_type="application/json")

    def test_groupes_par_defaut_existent_avec_les_bonnes_habilitations(self):
        from django.contrib.auth.models import Group

        def codes(nom):
            return set(Group.objects.get(name=nom).permissions.values_list("codename", flat=True))

        self.assertNotIn("valider_devis", codes("Commercial"))
        self.assertNotIn("annuler_commande", codes("Commercial"))
        self.assertIn("valider_devis", codes("Responsable commercial"))
        self.assertIn("annuler_commande", codes("Responsable commercial"))
        self.assertNotIn("valider_vente_sous_cout", codes("Responsable commercial"))
        self.assertIn("valider_vente_sous_cout", codes("Direction"))
        self.assertNotIn("view_devis", codes("Atelier"))
        self.assertIn("change_ordrefabrication", codes("Atelier"))

    def test_groupes_personnalises_ne_sont_jamais_reecrits(self):
        from django.contrib.auth.models import Group, Permission

        from comptes.groupes import creer_groupes_par_defaut

        groupe = Group.objects.get(name="Commercial")
        groupe.permissions.add(Permission.objects.get(codename="valider_devis"))
        creer_groupes_par_defaut(sender=None)
        self.assertTrue(groupe.permissions.filter(codename="valider_devis").exists())

    def test_api_sans_permission_ni_lecture_ni_ecriture(self):
        self.client.force_login(self._utilisateur("anonyme-a"))
        self.assertEqual(self.client.get("/api/v1/devis/").status_code, 403)
        self.assertEqual(self.client.get(f"/api/v1/devis/{self.devis.pk}/").status_code, 403)
        response = self.client.post(
            "/api/v1/devis/",
            data=json.dumps({"numero": "DEV-X", "client": self.tiers.pk, "date_creation": "2026-01-01"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)

    def test_commercial_lit_et_cree_mais_ne_valide_pas(self):
        self.client.force_login(self._utilisateur("com-a", "Commercial"))
        self.assertEqual(self.client.get("/api/v1/devis/").status_code, 200)
        response = self._patch(f"/api/v1/devis/{self.devis.pk}/", {"delai": "2 semaines"})
        self.assertEqual(response.status_code, 200, response.content)
        response = self._patch(f"/api/v1/devis/{self.devis.pk}/", {"statut": "valide"})
        self.assertEqual(response.status_code, 403, response.content)
        self.devis.refresh_from_db()
        self.assertEqual(self.devis.statut, Devis.Statut.BROUILLON)

    def test_commercial_ne_supprime_pas_un_devis(self):
        self.client.force_login(self._utilisateur("com-b", "Commercial"))
        self.assertEqual(self.client.delete(f"/api/v1/devis/{self.devis.pk}/").status_code, 403)
        self.assertTrue(Devis.objects.filter(pk=self.devis.pk).exists())

    def test_responsable_valide_mais_pas_sous_le_cout(self):
        self.client.force_login(self._utilisateur("resp-a", "Responsable commercial"))
        self.ligne.prix_vente_unitaire_force = 1
        self.ligne.save()
        refus = self._patch(f"/api/v1/devis/{self.devis.pk}/", {"statut": "valide"})
        self.assertEqual(refus.status_code, 400, refus.content)
        self.ligne.prix_vente_unitaire_force = None
        self.ligne.save()
        accepte = self._patch(f"/api/v1/devis/{self.devis.pk}/", {"statut": "valide"})
        self.assertEqual(accepte.status_code, 200, accepte.content)

    def test_direction_valide_sous_le_cout(self):
        self.client.force_login(self._utilisateur("dir-a", "Direction"))
        self.ligne.prix_vente_unitaire_force = 1
        self.ligne.save()
        reponse = self._patch(f"/api/v1/devis/{self.devis.pk}/", {"statut": "valide"})
        self.assertEqual(reponse.status_code, 200, reponse.content)

    def test_seul_un_responsable_annule_une_commande(self):
        self._valider()
        commande = lancer_en_production(self.devis)
        self.client.force_login(self._utilisateur("com-c", "Commercial"))
        self.assertEqual(self.client.post(f"/api/v1/commandes/{commande.pk}/annuler/").status_code, 403)
        self.client.force_login(self._utilisateur("resp-b", "Responsable commercial"))
        self.assertEqual(self.client.post(f"/api/v1/commandes/{commande.pk}/annuler/").status_code, 200)

    def test_lancer_en_production_exige_la_creation_de_commande(self):
        self._valider()
        self.client.force_login(self._utilisateur("atelier-a", "Atelier"))
        self.assertEqual(self.client.post(f"/api/v1/devis/{self.devis.pk}/lancer-en-production/").status_code, 403)
        self.client.force_login(self._utilisateur("com-d", "Commercial"))
        self.assertEqual(self.client.post(f"/api/v1/devis/{self.devis.pk}/lancer-en-production/").status_code, 201)

    def test_atelier_voit_les_commandes_mais_pas_les_devis(self):
        self.client.force_login(self._utilisateur("atelier-b", "Atelier"))
        self.assertEqual(self.client.get("/api/v1/commandes/").status_code, 200)
        self.assertEqual(self.client.get("/api/v1/devis/").status_code, 403)
        self.assertEqual(self.client.get("/admin/chiffrage/devis/").status_code, 403)
        self.assertEqual(self.client.get("/admin/chiffrage/commande/").status_code, 200)

    def test_commercial_dans_ladmin_sans_champ_statut(self):
        self.client.force_login(self._utilisateur("com-e", "Commercial"))
        self.assertEqual(self.client.get("/admin/chiffrage/devis/").status_code, 200)
        self.assertNotContains(self.client.get("/admin/chiffrage/devis/add/"), 'name="statut"')

    def test_responsable_voit_le_champ_statut(self):
        self.client.force_login(self._utilisateur("resp-c", "Responsable commercial"))
        self.assertContains(self.client.get("/admin/chiffrage/devis/add/"), 'name="statut"')


class AnnulationLivraisonTests(_FixtureModuleA, TestCase):
    """C-OP-02 : on corrige une livraison en l'annulant (cumul livré, stock et statut de
    commande rétablis), jamais en éditant ou supprimant ses lignes."""

    def setUp(self):
        super().setUp()
        self._valider()
        self.commande = lancer_en_production(self.devis)
        self.ligne_cde = self.commande.lignes.get()
        emplacement = Emplacement.objects.create(code="LIV-ANN")
        self.lot = Lot.objects.create(article=self.article, emplacement=emplacement)
        MouvementStock.objects.create(lot=self.lot, type_mouvement="entree", quantite=10, motif="stock")

    def _livrer(self, quantite, numero="LIV-ANN-1"):
        livraison = Livraison.objects.create(numero=numero, commande=self.commande, date_livraison=datetime.date(2026, 2, 1))
        ligne = LivraisonLigne.objects.create(livraison=livraison, commande_ligne=self.ligne_cde, quantite_livree=quantite)
        return livraison, ligne

    def test_annulation_retablit_livre_stock_et_statut(self):
        livraison, _ = self._livrer(3)  # commande de 3 : soldée
        self.commande.refresh_from_db()
        self.assertEqual(self.commande.statut, Commande.Statut.SOLDEE)
        livraison.annuler(utilisateur=self.user, motif="Erreur de saisie")
        livraison.refresh_from_db()
        self.assertEqual(livraison.statut, Livraison.Statut.ANNULEE)
        self.assertEqual((livraison.motif_annulation, livraison.utilisateur_annulation), ("Erreur de saisie", self.user))
        self.assertIsNotNone(livraison.date_annulation)
        self.ligne_cde.refresh_from_db()
        self.assertEqual(self.ligne_cde.quantite_livree, 0)
        self.lot.refresh_from_db()
        self.assertEqual(self.lot.quantite, 10)
        self.commande.refresh_from_db()
        self.assertEqual(self.commande.statut, Commande.Statut.EN_COURS)

    def test_annulation_conserve_les_lignes_et_contre_passe_les_sorties(self):
        livraison, _ = self._livrer(2)
        livraison.annuler(utilisateur=self.user)
        self.assertEqual(livraison.lignes.count(), 1)
        sortie = MouvementStock.objects.get(reference_origine="LIVRAISON-LIV-ANN-1")
        self.assertTrue(MouvementStock.objects.filter(annule_mouvement=sortie).exists())

    def test_annulation_dune_livraison_en_fifo_sur_plusieurs_lots(self):
        autre = Lot.objects.create(article=self.article, emplacement=Emplacement.objects.create(code="LIV-ANN-2"))
        MouvementStock.objects.create(lot=autre, type_mouvement="entree", quantite=10, motif="stock")
        self.lot.refresh_from_db()
        livraison, _ = self._livrer(3)
        livraison.annuler(utilisateur=self.user)
        self.lot.refresh_from_db()
        autre.refresh_from_db()
        self.assertEqual((self.lot.quantite, autre.quantite), (10, 10))

    def test_double_annulation_refusee(self):
        livraison, _ = self._livrer(1)
        livraison.annuler()
        with self.assertRaises(LivraisonError):
            livraison.annuler()
        self.ligne_cde.refresh_from_db()
        self.assertEqual(self.ligne_cde.quantite_livree, 0)

    def test_annulation_refusee_si_la_commande_est_deja_facturee(self):
        from facturation.models import Facture

        livraison, _ = self._livrer(1)
        Facture.objects.create(
            numero="FAC-ANN", commande=self.commande, date_facturation=datetime.date(2026, 2, 5), montant_ht=1, montant_ttc=1
        )
        with self.assertRaises(LivraisonError):
            livraison.annuler()
        self.ligne_cde.refresh_from_db()
        self.assertEqual(self.ligne_cde.quantite_livree, 1)

    def test_lignes_et_livraison_immuables(self):
        livraison, ligne = self._livrer(1)
        ligne.quantite_livree = 3
        with self.assertRaises(LivraisonError):
            ligne.save()
        with self.assertRaises(LivraisonError):
            ligne.delete()
        with self.assertRaises(LivraisonError):
            livraison.delete()
        self.ligne_cde.refresh_from_db()
        self.assertEqual(self.ligne_cde.quantite_livree, 1)

    def test_pas_de_nouvelle_ligne_sur_une_livraison_annulee(self):
        livraison, _ = self._livrer(1)
        livraison.annuler()
        nouvelle = LivraisonLigne(livraison=livraison, commande_ligne=self.ligne_cde, quantite_livree=1)
        with self.assertRaises(ValidationError):
            nouvelle.full_clean()

    def test_admin_inline_sans_modification_ni_suppression(self):
        livraison, ligne = self._livrer(1)
        page = self.client.get(f"/admin/chiffrage/livraison/{livraison.pk}/change/")
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, f'name="lignes-0-quantite_livree"' + ' value="1')
        self.assertEqual(self.client.post(f"/admin/chiffrage/livraison/{livraison.pk}/delete/", {"post": "yes"}).status_code, 403)
        self.assertEqual(self.client.post(f"/admin/chiffrage/livraisonligne/{ligne.pk}/delete/", {"post": "yes"}).status_code, 403)

    def test_admin_page_et_action_dannulation(self):
        livraison, _ = self._livrer(2)
        page = self.client.get(f"/admin/chiffrage/livraison/{livraison.pk}/annuler/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Motif")
        self.client.post(f"/admin/chiffrage/livraison/{livraison.pk}/annuler/", {"motif": "Mauvaise commande"}, follow=True)
        livraison.refresh_from_db()
        self.assertEqual(livraison.statut, Livraison.Statut.ANNULEE)
        self.lot.refresh_from_db()
        self.assertEqual(self.lot.quantite, 10)

    def test_annulation_exige_la_permission(self):
        from django.contrib.auth import get_user_model

        livraison, _ = self._livrer(1)
        simple = get_user_model().objects.create_user("liv-simple", "l@example.com", "pass1234", is_staff=True)
        self.client.force_login(simple)
        self.client.post(f"/admin/chiffrage/livraison/{livraison.pk}/annuler/", {"motif": "x"})
        livraison.refresh_from_db()
        self.assertEqual(livraison.statut, Livraison.Statut.VALIDEE)

    def test_historique_de_la_livraison(self):
        livraison, _ = self._livrer(1)
        livraison.annuler(utilisateur=self.user, motif="Erreur")
        derniere = livraison.history.first()
        self.assertEqual(derniere.statut, Livraison.Statut.ANNULEE)
        self.assertEqual(derniere.history_change_reason, "Annulation : Erreur")


class CycleDeVieDevisTests(_FixtureModuleA, TestCase):
    """Gap analysis : validité de l'offre, réponse du client (accepté / refusé), révisions."""

    def _expirer(self, jours=2):
        Devis.objects.filter(pk=self.devis.pk).update(date_validite=datetime.date.today() - datetime.timedelta(days=jours))
        self.devis.refresh_from_db()

    def test_validation_pose_la_date_de_validite(self):
        self._valider()
        self.assertEqual(self.devis.date_validite, datetime.date.today() + datetime.timedelta(days=30))
        self.assertFalse(self.devis.est_expire)
        self.assertEqual(self.devis.jours_avant_expiration, 30)

    def test_une_date_saisie_est_conservee_et_un_brouillon_na_pas_de_date(self):
        self.assertIsNone(self.devis.date_validite)
        voulue = datetime.date.today() + datetime.timedelta(days=90)
        self.devis.date_validite = voulue
        self.devis.statut = Devis.Statut.VALIDE
        self.devis.save()
        self.assertEqual(self.devis.date_validite, voulue)

    def test_retour_en_brouillon_efface_lecheance(self):
        self._valider()
        self.devis.statut = Devis.Statut.BROUILLON
        self.devis.save()
        self.assertIsNone(self.devis.date_validite)
        self._valider()
        self.assertEqual(self.devis.date_validite, datetime.date.today() + datetime.timedelta(days=30))

    def test_offre_expiree_ne_devient_pas_une_commande(self):
        self._valider()
        self._expirer()
        self.assertTrue(self.devis.est_expire)
        with self.assertRaises(ChiffrageError) as cm:
            lancer_en_production(self.devis)
        self.assertIn("a expiré", str(cm.exception))
        self.assertFalse(Commande.objects.filter(devis=self.devis).exists())

    def test_prolonger_la_validite_debloque_la_commande(self):
        self._valider()
        self._expirer()
        self.devis.date_validite = datetime.date.today() + datetime.timedelta(days=15)
        self.devis.save()
        self.assertFalse(self.devis.est_expire)
        lancer_en_production(self.devis)

    def test_la_commande_marque_le_devis_accepte_et_la_trace(self):
        self._valider()
        commande = lancer_en_production(self.devis)
        self.devis.refresh_from_db()
        self.assertEqual(self.devis.issue, Devis.Issue.ACCEPTE)
        self.assertEqual(self.devis.history.first().history_change_reason, f"Commande {commande.numero} créée")

    def test_refus_exige_un_motif_et_un_devis_valide(self):
        self._valider()
        self.devis.issue = Devis.Issue.REFUSE
        with self.assertRaises(ValidationError) as cm:
            self.devis.full_clean()
        self.assertIn("motif_refus", cm.exception.message_dict)
        brouillon = Devis(numero="DEV-BR", client=self.tiers, date_creation=datetime.date(2026, 1, 1), issue="refuse", motif_refus="x")
        with self.assertRaises(ValidationError) as cm:
            brouillon.full_clean()
        self.assertIn("issue", cm.exception.message_dict)

    def test_devis_refuse_ne_devient_pas_une_commande(self):
        self._valider()
        self.devis.issue = Devis.Issue.REFUSE
        self.devis.motif_refus = "Trop cher"
        self.devis.save()
        with self.assertRaises(ChiffrageError) as cm:
            lancer_en_production(self.devis)
        self.assertIn("refusé par le client", str(cm.exception))

    def test_noter_un_refus_depuis_ladmin_sur_un_devis_valide_verrouille(self):
        self._valider()
        page = self.client.get(f"/admin/chiffrage/devis/{self.devis.pk}/change/")
        self.assertContains(page, 'name="issue"')
        self.assertContains(page, 'name="date_validite"')
        self.assertNotContains(page, 'name="delai"')  # le reste demeure verrouillé
        self.client.post(
            f"/admin/chiffrage/devis/{self.devis.pk}/change/",
            {"statut": "valide", "issue": "refuse", "motif_refus": "Concurrent moins cher",
             "date_validite": self.devis.date_validite.isoformat(), "lignes-TOTAL_FORMS": "0", "lignes-INITIAL_FORMS": "0"},
        )
        self.devis.refresh_from_db()
        self.assertEqual((self.devis.issue, self.devis.motif_refus), ("refuse", "Concurrent moins cher"))

    def test_prolonger_la_validite_depuis_ladmin(self):
        self._valider()
        self._expirer()
        nouvelle = datetime.date.today() + datetime.timedelta(days=20)
        self.client.post(
            f"/admin/chiffrage/devis/{self.devis.pk}/change/",
            {"statut": "valide", "issue": "en_attente", "date_validite": nouvelle.isoformat(),
             "lignes-TOTAL_FORMS": "0", "lignes-INITIAL_FORMS": "0"},
        )
        self.devis.refresh_from_db()
        self.assertEqual(self.devis.date_validite, nouvelle)

    def test_issue_accepte_ou_remplace_non_saisissable(self):
        self._valider()
        self.client.post(
            f"/admin/chiffrage/devis/{self.devis.pk}/change/",
            {"statut": "valide", "issue": "accepte", "lignes-TOTAL_FORMS": "0", "lignes-INITIAL_FORMS": "0"},
        )
        self.devis.refresh_from_db()
        self.assertEqual(self.devis.issue, Devis.Issue.EN_ATTENTE)
        r = self.client.patch(f"/api/v1/devis/{self.devis.pk}/", data=json.dumps({"issue": "accepte"}), content_type="application/json")
        self.assertEqual(r.status_code, 400)

    def test_api_noter_un_refus_et_prolonger_sans_deverrouiller_le_reste(self):
        self._valider()
        url = f"/api/v1/devis/{self.devis.pk}/"
        r = self.client.patch(url, data=json.dumps({"issue": "refuse", "motif_refus": "Budget gelé"}), content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content)
        r = self.client.patch(url, data=json.dumps({"delai": "demain"}), content_type="application/json")
        self.assertEqual(r.status_code, 400)

    def test_revision_cree_un_brouillon_et_remplace_loriginal(self):
        from .production import reviser_devis

        self._valider()
        revision = reviser_devis(self.devis, "Quantité portée à 3")
        self.assertEqual((revision.numero, revision.revision, revision.statut), ("DEV-REC-A-B", 2, "brouillon"))
        self.assertEqual((revision.indice, self.devis.indice), ("B", "A"))
        self.assertEqual(revision.motif_revision, "Quantité portée à 3")
        self.assertEqual(revision.devis_origine, self.devis)
        self.assertEqual(revision.client, self.devis.client)
        ligne = revision.lignes.get()
        self.assertEqual((ligne.article, ligne.quantite), (self.article, 3))
        self.devis.refresh_from_db()
        self.assertEqual(self.devis.issue, Devis.Issue.REMPLACE)
        self.assertEqual(self.devis.history.first().history_change_reason, "Remplacé par DEV-REC-A-B : Quantité portée à 3")
        self.assertEqual(self.devis.lignes.count(), 1)  # l'original est intact

    def test_revision_de_revision_et_numerotation(self):
        from .production import reviser_devis

        self._valider()
        r2 = reviser_devis(self.devis, "Premier changement")
        r2.statut = Devis.Statut.VALIDE
        r2.save()
        r3 = reviser_devis(r2, "Second changement")
        self.assertEqual((r3.numero, r3.indice), ("DEV-REC-A-C", "C"))  # numéro bâti sur le devis d'origine
        self.assertEqual([v.indice for v in r3.versions()], ["A", "B", "C"])
        self.assertEqual(r3.racine, self.devis)

    def test_devis_remplace_ne_devient_pas_une_commande(self):
        from .production import reviser_devis

        self._valider()
        reviser_devis(self.devis, "Changement")
        self.devis.refresh_from_db()
        with self.assertRaises(ChiffrageError) as cm:
            lancer_en_production(self.devis)
        self.assertIn("DEV-REC-A-B", str(cm.exception))

    def test_revision_refusee_dans_les_cas_non_valides(self):
        from .production import reviser_devis

        with self.assertRaises(ChiffrageError):
            reviser_devis(self.devis, "x")  # brouillon
        self._valider()
        with self.assertRaises(ChiffrageError) as cm:
            reviser_devis(self.devis, "  ")  # motif obligatoire
        self.assertIn("motif", str(cm.exception))
        lancer_en_production(self.devis)
        with self.assertRaises(ChiffrageError):
            reviser_devis(self.devis, "x")  # déjà commandé

    def test_double_revision_refusee(self):
        from .production import reviser_devis

        self._valider()
        reviser_devis(self.devis, "x")
        with self.assertRaises(ChiffrageError):
            reviser_devis(Devis.objects.get(pk=self.devis.pk), "y")

    def test_action_admin_reviser(self):
        self._valider()
        url = f"/admin/chiffrage/devis/{self.devis.pk}/reviser/"
        page = self.client.get(url)
        self.assertContains(page, "Nouvel indice")  # page de saisie du motif, rien n'est encore créé
        self.assertFalse(Devis.objects.filter(pk="DEV-REC-A-B").exists())
        r = self.client.post(url, {"motif": "Remise de 5 %"}, follow=True)
        self.assertContains(r, "Indice B créé")
        self.assertContains(r, f'href="/admin/chiffrage/devis/{self.devis.pk}/change/"')  # lien vers l'indice remplacé
        self.assertTrue(Devis.objects.filter(pk="DEV-REC-A-B", motif_revision="Remise de 5 %").exists())

    def test_action_admin_reviser_sans_motif_refusee(self):
        self._valider()
        r = self.client.post(f"/admin/chiffrage/devis/{self.devis.pk}/reviser/", {"motif": ""}, follow=True)
        self.assertContains(r, "motif")
        self.assertFalse(Devis.objects.filter(pk="DEV-REC-A-B").exists())

    def test_api_reviser(self):
        self._valider()
        url = f"/api/v1/devis/{self.devis.pk}/reviser/"
        self.assertEqual(self.client.post(url, {}, content_type="application/json").status_code, 400)
        r = self.client.post(url, {"motif": "Délai raccourci"}, content_type="application/json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual((r.json()["numero"], r.json()["indice"], r.json()["motif_revision"]), ("DEV-REC-A-B", "B", "Délai raccourci"))
        self.assertEqual(self.client.get(f"/api/v1/devis/{self.devis.pk}/").json()["indice"], "A")

    def test_supprimer_la_revision_brouillon_rend_l_indice_precedent(self):
        from .production import reviser_devis

        self._valider()
        revision = reviser_devis(self.devis, "Essai abandonné")
        self.devis.refresh_from_db()
        self.assertEqual(self.devis.issue, Devis.Issue.REMPLACE)
        revision.delete()
        self.devis.refresh_from_db()
        self.assertEqual(self.devis.issue, Devis.Issue.EN_ATTENTE)  # redevient transformable en commande

    def test_comparaison_entre_indices(self):
        from .production import comparer_indices, reviser_devis

        self._valider()
        revision = reviser_devis(self.devis, "Quantité doublée")
        ligne = revision.lignes.get()
        ligne.quantite = 6
        ligne.save()
        comparaison = comparer_indices(self.devis, revision)
        self.assertEqual([l["etat"] for l in comparaison["lignes"]], ["modifiee"])
        self.assertEqual(comparaison["lignes"][0]["avant"][0], 3)
        self.assertEqual(comparaison["lignes"][0]["apres"][0], 6)
        self.assertIsNone(comparaison["total_apres"])  # pas encore rechiffré

    def test_fiche_affiche_historique_et_comparaison(self):
        from .production import reviser_devis

        self._valider()
        revision = reviser_devis(self.devis, "Quantité modifiée")
        page = self.client.get(f"/admin/chiffrage/devis/{revision.pk}/change/")
        self.assertContains(page, "Indices de ce devis")
        self.assertContains(page, "Quantité modifiée")
        self.assertContains(page, "Changements depuis l")
        self.assertContains(page, "DEV-REC-A-B")

    def test_filtre_derniers_indices(self):
        from .production import reviser_devis

        self._valider()
        reviser_devis(self.devis, "x")
        page = self.client.get("/admin/chiffrage/devis/?indices=derniers")
        self.assertContains(page, "DEV-REC-A-B")
        self.assertNotContains(page, 'href="/admin/chiffrage/devis/DEV-REC-A/change/"')

    def test_indices_au_dela_de_z(self):
        from .models import indice_pour

        self.assertEqual([indice_pour(n) for n in (1, 2, 26, 27, 28, 52, 53)], ["A", "B", "Z", "AA", "AB", "AZ", "BA"])

    def test_filtres_expires_et_bientot(self):
        self._valider()
        self._expirer()
        autre = Devis.objects.create(numero="DEV-BIENTOT", client=self.tiers, date_creation=datetime.date(2026, 1, 1),
                                      statut="valide", date_validite=datetime.date.today() + datetime.timedelta(days=3))
        page = self.client.get("/admin/chiffrage/devis/?validite=expire")
        self.assertContains(page, "DEV-REC-A")
        self.assertNotContains(page, "DEV-BIENTOT")
        page = self.client.get("/admin/chiffrage/devis/?validite=bientot")
        self.assertContains(page, "DEV-BIENTOT")

    def test_tableau_de_bord_taux_de_transformation_et_devis_sans_reponse(self):
        self._valider()
        Devis.objects.filter(pk=self.devis.pk).update(date_creation=datetime.date.today())
        self.devis.refresh_from_db()
        lancer_en_production(self.devis)  # accepté
        autre = Devis.objects.create(numero="DEV-ATTENTE", client=self.tiers, date_creation=datetime.date.today(),
                                      statut="valide", date_validite=datetime.date.today() + datetime.timedelta(days=2))
        page = self.client.get("/admin/")
        self.assertContains(page, "Taux de transformation")
        self.assertContains(page, "50 %")
        self.assertContains(page, "devis acceptés sur 2 envoyés")
        self.assertContains(page, "1 à relancer")

    def test_migration_marque_accepte_les_devis_deja_commandes(self):
        import importlib

        from django.apps import apps

        self._valider()
        commande = lancer_en_production(self.devis)
        Devis.objects.filter(pk=self.devis.pk).update(issue="en_attente")
        migration = importlib.import_module("chiffrage.migrations.0020_cycle_de_vie_devis")
        migration.devis_deja_commandes_acceptes(apps, None)
        self.devis.refresh_from_db()
        self.assertEqual(self.devis.issue, "accepte")


class DocumentsPdfTests(_FixtureModuleA, TestCase):
    """Gap analysis : devis et bon de livraison au format PDF."""

    def setUp(self):
        super().setUp()
        from comptes.models import Societe

        societe = Societe.charger()
        societe.raison_sociale = "Chaudronnerie Dupont"
        societe.forme_juridique = "SAS"
        societe.siret = "12345678900011"
        societe.tva_intracommunautaire = "FR12345678900"
        societe.mentions_devis = "Paiement a 30 jours - penalites de retard 3 fois le taux legal"
        societe.save()
        calculer_devis(self.devis)

    def _texte(self, pdf):
        return pdf.decode("latin-1")

    def test_pdf_du_devis_valide(self):
        from .documents import generer_pdf_devis

        self._valider()
        pdf = generer_pdf_devis(self.devis)
        texte = self._texte(pdf)
        self.assertTrue(pdf.startswith(b"%PDF"))
        for attendu in ("DEVIS DEV-REC-A", "Chaudronnerie Dupont", "ART-REC-A", "Client Recette A", "Total HT", "Total TTC",
                        "Bon pour accord", "SIRET 12345678900011", "Valable jusqu"):
            self.assertIn(attendu, texte, attendu)
        self.assertNotIn("PROVISOIRE", texte)

    def test_montants_du_devis_dans_le_pdf(self):
        from .documents import generer_pdf_devis

        # coût 10 x 3 = 30 ; marge 10 % -> 33,00 HT ; TVA par défaut -> TTC
        texte = self._texte(generer_pdf_devis(self.devis))
        self.assertIn("33,00", texte)

    def test_devis_brouillon_marque_provisoire(self):
        from .documents import generer_pdf_devis

        self.assertIn("PROVISOIRE", self._texte(generer_pdf_devis(self.devis)))

    def test_devis_non_chiffre_ou_vide_refuse(self):
        from .documents import DocumentError, generer_pdf_devis

        DevisLigne.objects.filter(pk=self.ligne.pk).update(prix_vente_matiere=None)
        with self.assertRaises(DocumentError):
            generer_pdf_devis(Devis.objects.get(pk=self.devis.pk))
        vide = Devis.objects.create(numero="DEV-VIDE", client=self.tiers, date_creation=datetime.date(2026, 1, 1))
        with self.assertRaises(DocumentError):
            generer_pdf_devis(vide)

    def test_texte_utilisateur_echappe(self):
        from .documents import generer_pdf_devis

        self.devis.delai = "<b>9 semaines</b> & plus"
        self.devis.save()
        pdf = generer_pdf_devis(self.devis)  # ne doit pas lever d'erreur de balisage
        self.assertIn("9 semaines", self._texte(pdf))

    def test_revision_mentionne_le_devis_remplace(self):
        from .documents import generer_pdf_devis
        from .production import reviser_devis

        self._valider()
        revision = reviser_devis(self.devis, "Remise commerciale")
        calculer_devis(revision)
        texte = self._texte(generer_pdf_devis(revision))
        self.assertIn("Annule et remplace DEV-REC-A", texte)
        self.assertIn("indice A", texte)
        self.assertIn("Indice B", texte)
        self.assertIn("Modification : Remise commerciale", texte)

    def test_telechargement_depuis_ladmin_et_lapi(self):
        r = self.client.get(f"/admin/chiffrage/devis/{self.devis.pk}/pdf/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "application/pdf")
        self.assertTrue(r.content.startswith(b"%PDF"))
        r = self.client.get(f"/api/v1/devis/{self.devis.pk}/pdf/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("devis-DEV-REC-A.pdf", r["Content-Disposition"])

    def test_pdf_reserve_aux_comptes_habilites(self):
        from django.contrib.auth import get_user_model

        simple = get_user_model().objects.create_user("pdf-nu", "p@example.com", "pass-mot-de-passe-4", is_staff=True)
        self.client.force_login(simple)
        self.assertEqual(self.client.get(f"/api/v1/devis/{self.devis.pk}/pdf/").status_code, 403)
        self.assertEqual(self.client.get(f"/admin/chiffrage/devis/{self.devis.pk}/pdf/").status_code, 403)

    def test_message_clair_si_le_pdf_est_impossible(self):
        vide = Devis.objects.create(numero="DEV-VIDE2", client=self.tiers, date_creation=datetime.date(2026, 1, 1))
        r = self.client.get(f"/admin/chiffrage/devis/{vide.pk}/pdf/", follow=True)
        self.assertContains(r, "aucune ligne")

    def _livraison(self, quantite=2):
        self._valider()
        commande = lancer_en_production(self.devis)
        livraison = Livraison.objects.create(numero="BL-PDF-1", commande=commande, date_livraison=datetime.date(2026, 2, 1))
        LivraisonLigne.objects.create(livraison=livraison, commande_ligne=commande.lignes.get(), quantite_livree=quantite)
        return livraison

    def test_pdf_du_bon_de_livraison(self):
        from .documents import generer_pdf_bon_livraison

        livraison = self._livraison()
        texte = self._texte(generer_pdf_bon_livraison(livraison))
        for attendu in ("BON DE LIVRAISON BL-PDF-1", "ART-REC-A", "Reliquat", "Marchandise re", "Commande : CDE-DEV-REC-A"):
            self.assertIn(attendu, texte, attendu)
        self.assertNotIn("ANNUL", texte)

    def test_bon_de_livraison_annule_filigrane(self):
        from .documents import generer_pdf_bon_livraison

        livraison = self._livraison()
        livraison.annuler(utilisateur=self.user, motif="x")
        self.assertIn("ANNUL", self._texte(generer_pdf_bon_livraison(livraison)))

    def test_telechargement_du_bon_de_livraison_depuis_ladmin(self):
        livraison = self._livraison()
        r = self.client.get(f"/admin/chiffrage/livraison/{livraison.pk}/pdf/")
        self.assertEqual((r.status_code, r["Content-Type"]), (200, "application/pdf"))

    def test_logo_integre_quand_il_existe(self):
        import io

        from django.core.files.base import ContentFile
        from PIL import Image

        from comptes.models import Societe
        from .documents import generer_pdf_devis

        tampon = io.BytesIO()
        Image.new("RGB", (200, 80), (180, 83, 9)).save(tampon, "PNG")
        societe = Societe.charger()
        societe.logo.save("logo-test.png", ContentFile(tampon.getvalue()), save=True)
        self.addCleanup(lambda: societe.logo.delete(save=False))
        self.assertIn("/Subtype /Image", self._texte(generer_pdf_devis(self.devis)))


class LivraisonDepassementAvecLotTests(_FixtureModuleA, TestCase):
    """Garde-fou : le contrôle du lot ne doit jamais masquer celui de la quantité commandée."""

    def test_depassement_toujours_detecte_avec_un_lot_choisi(self):
        self._valider()
        commande = lancer_en_production(self.devis)
        ligne = commande.lignes.get()  # 3 commandés
        lot = Lot.objects.create(article=self.article, emplacement=Emplacement.objects.create(code="DEP-1"))
        MouvementStock.objects.create(lot=lot, type_mouvement="entree", quantite=50, motif="stock")
        livraison = Livraison.objects.create(numero="BL-DEP", commande=commande, date_livraison=datetime.date(2026, 2, 1))
        with self.assertRaises(ValidationError) as cm:
            LivraisonLigne(livraison=livraison, commande_ligne=ligne, quantite_livree=4, lot=lot).full_clean()
        self.assertIn("quantite_livree", cm.exception.message_dict)


class _FixtureOrdresCommande:
    """Commande avec deux lignes du même article fabriqué (F), une autre fabriquée (G) et une matière achetée."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        self.admin = get_user_model().objects.create_superuser("of-admin", "o@example.com", "pass-mot-de-passe-6")
        self.client.force_login(self.admin)
        self.tiers = Tiers.objects.create(code="CLI-OF", raison_sociale="Client Fabrication", type_tiers=Tiers.TypeTiers.CLIENT)
        adresse = Adresse.objects.create(
            tiers=self.tiers, est_facturation=True, est_livraison=True, adresse="1 rue des Forges",
            code_postal="69000", ville="Lyon", est_principale=True,
        )
        self.tole = Article.objects.create(reference="TOLE-OF", nature=Article.Nature.MATIERE_PREMIERE, cout_unitaire=2)
        self.vis = Article.objects.create(reference="VIS-OF", nature=Article.Nature.MATIERE_PREMIERE, cout_unitaire=0.1)
        self.f = Article.objects.create(reference="PIECE-F", libelle="Flasque", nature=Article.Nature.FABRIQUE)
        self.g = Article.objects.create(reference="PIECE-G", nature=Article.Nature.FABRIQUE)
        poste_decoupe = PosteTravail.objects.create(nom="Découpe laser", mode_calcul=PosteTravail.ModeCalcul.HORAIRE)
        poste_plieuse = PosteTravail.objects.create(nom="Plieuse", mode_calcul=PosteTravail.ModeCalcul.HORAIRE)
        TarifPoste.objects.create(poste=poste_decoupe, cout_horaire=60, date_debut=datetime.date(2020, 1, 1))
        TarifPoste.objects.create(poste=poste_plieuse, cout_horaire=50, date_debut=datetime.date(2020, 1, 1))
        for ordre, poste, fixe, variable in ((1, poste_decoupe, 10, 2), (2, poste_plieuse, 5, 1)):
            Gamme.objects.create(article=self.f, poste=poste, ordre=ordre, temps_fixe=fixe, temps_variable=variable,
                                 date_debut=datetime.date(2020, 1, 1))
        Nomenclature.objects.create(article_parent=self.f, article_composant=self.tole, quantite=1, longueur_mm=500, largeur_mm=300)
        Nomenclature.objects.create(article_parent=self.f, article_composant=self.vis, quantite=4)
        self.commande = Commande.objects.create(
            numero="CDE-OF", client=self.tiers, reference_client="BC-4411", date_commande=datetime.date(2026, 10, 1),
            adresse_facturation=adresse, adresse_livraison=adresse,
        )
        taux = TauxTVA.objects.create(nom="Normal OF", taux=20)
        mk = lambda article, qte, jour, prix: CommandeLigne.objects.create(
            commande=self.commande, article=article, quantite_commandee=qte, prix_vente_unitaire=prix, taux_tva=taux,
            date_livraison_prevue=datetime.date(2026, 12, jour) if jour else None,
        )
        self.l1 = mk(self.f, 3, 20, 100)
        self.l2 = mk(self.f, 2, 10, 100)
        self.l3 = mk(self.g, 4, None, 50)
        self.l4 = mk(self.vis, 100, 5, 1)


class OrdresDepuisCommandeTests(_FixtureOrdresCommande, TestCase):
    def test_un_of_par_ligne_fabriquee_quantites_dates_gamme_et_nomenclature(self):
        ordres = creer_ordres_fabrication(self.commande)
        self.assertEqual(len(ordres), 3)  # F, F, G — pas d'OF pour la matière achetée
        of1 = next(o for o in ordres if list(o.lignes_commande.all()) == [self.l1])
        self.assertEqual((of1.article, of1.quantite, of1.date_livraison_prevue), (self.f, 3, datetime.date(2026, 12, 20)))
        # Gamme : 10 + 2×3 = 16 min à la découpe, 5 + 1×3 = 8 min à la plieuse.
        self.assertEqual([(o.poste.nom, o.temps_prevu) for o in of1.operations.order_by("ordre")], [("Découpe laser", 16), ("Plieuse", 8)])
        # Nomenclature : quantités par pièce × quantité à fabriquer.
        composants = {c.article_id: c for c in of1.composants.all()}
        self.assertEqual(composants["TOLE-OF"].quantite_necessaire, 3)
        self.assertEqual((composants["TOLE-OF"].longueur_mm, composants["TOLE-OF"].largeur_mm), (500, 300))
        self.assertEqual((composants["VIS-OF"].quantite_par_unite, composants["VIS-OF"].quantite_necessaire), (4, 12))
        of_g = next(o for o in ordres if o.article == self.g)
        self.assertIsNone(of_g.date_livraison_prevue)  # aucune date saisie sur la ligne
        self.assertEqual(of_g.composants.count(), 0)

    def test_regroupement_des_lignes_du_meme_article(self):
        ordres = creer_ordres_fabrication(self.commande, regrouper=True)
        self.assertEqual(len(ordres), 2)
        of_f = next(o for o in ordres if o.article == self.f)
        self.assertEqual(of_f.quantite, 5)
        self.assertEqual(set(of_f.lignes_commande.all()), {self.l1, self.l2})
        self.assertEqual(of_f.date_livraison_prevue, datetime.date(2026, 12, 10))  # la plus proche
        self.assertEqual(of_f.operations.get(ordre=1).temps_prevu, 20)  # 10 + 2×5
        self.assertEqual(of_f.composants.get(article="VIS-OF").quantite_necessaire, 20)

    def test_rejouer_ne_cree_pas_de_doublon_puis_complete_les_lignes_ajoutees(self):
        creer_ordres_fabrication(self.commande)
        with self.assertRaises(ChiffrageError) as cm:
            creer_ordres_fabrication(self.commande)
        self.assertIn("Aucun ordre", str(cm.exception))
        self.assertEqual(self.commande.ordres_fabrication.count(), 3)
        nouvelle = CommandeLigne.objects.create(commande=self.commande, article=self.g, quantite_commandee=7)
        ordres = creer_ordres_fabrication(self.commande)
        self.assertEqual([(o.quantite, list(o.lignes_commande.all())) for o in ordres], [(7, [nouvelle])])
        self.assertEqual(len({o.numero for o in self.commande.ordres_fabrication.all()}), 4)

    def test_commande_annulee_refusee_et_planification_sans_ecriture(self):
        from .production import planifier_ordres_fabrication

        plan = planifier_ordres_fabrication(self.commande, regrouper=True)
        self.assertEqual(len(plan), 2)
        self.assertEqual(OrdreFabrication.objects.count(), 0)  # un aperçu n'écrit rien
        Commande.objects.filter(pk=self.commande.pk).update(statut=Commande.Statut.ANNULEE)
        with self.assertRaises(ChiffrageError):
            creer_ordres_fabrication(Commande.objects.get(pk=self.commande.pk))

    def test_la_nomenclature_de_l_of_est_figee(self):
        of = creer_ordres_fabrication(self.commande)[0]
        Nomenclature.objects.filter(article_parent=self.f).delete()
        self.assertEqual(of.composants.count(), 2)

    def test_payload_planning_avec_date_et_composants_et_inchange_pour_les_anciens_of(self):
        from .planning_sync import construire_payload

        of = next(o for o in creer_ordres_fabrication(self.commande) if o.article == self.f)
        payload = construire_payload(of)
        self.assertEqual(payload["date_livraison_prevue"], of.date_livraison_prevue.isoformat())
        self.assertEqual({c["article"]: c["quantite"] for c in payload["composants"]}, {"TOLE-OF": of.quantite, "VIS-OF": of.quantite * 4})
        ancien = OrdreFabrication.objects.create(
            numero="OF-ANCIEN", commande=self.commande, article=self.g, quantite=1, date_lancement=datetime.date(2026, 1, 1)
        )
        self.assertNotIn("composants", construire_payload(ancien))
        self.assertNotIn("date_livraison_prevue", construire_payload(ancien))

    def test_admin_apercu_puis_creation_avec_regroupement(self):
        url = f"/admin/chiffrage/commande/{self.commande.pk}/ordres-fabrication/"
        page = self.client.get(url)
        self.assertContains(page, "Créer 3 ordres de fabrication")
        page = self.client.get(url + "?regrouper=1")
        self.assertContains(page, "Créer 2 ordres de fabrication")
        self.assertEqual(OrdreFabrication.objects.count(), 0)
        reponse = self.client.post(url, {"regrouper": "1"}, follow=True)
        self.assertContains(reponse, "2 ordre(s) de fabrication créé(s)")
        for of in self.commande.ordres_fabrication.all():
            self.assertContains(reponse, f'href="/admin/chiffrage/ordrefabrication/{of.pk}/change/"')
        self.assertEqual(self.commande.ordres_fabrication.count(), 2)
        # Plus rien à créer : la page l'explique au lieu de proposer un bouton.
        self.assertContains(self.client.get(url), "rien à créer")

    def test_fiche_of_affiche_date_nomenclature_et_gamme(self):
        of = creer_ordres_fabrication(self.commande, regrouper=True)[0]
        page = self.client.get(f"/admin/chiffrage/ordrefabrication/{of.pk}/change/")
        for attendu in ("Nomenclature", "TOLE-OF", "Gamme", "Découpe laser", "Lignes de commande couvertes"):
            self.assertContains(page, attendu)

    def test_api_creer_les_of_et_droits(self):
        url = f"/api/v1/commandes/{self.commande.pk}/creer-ordres-fabrication/"
        reponse = self.client.post(url, {"regrouper": True}, content_type="application/json")
        self.assertEqual(reponse.status_code, 201, reponse.content)
        donnees = reponse.json()
        self.assertEqual(len(donnees), 2)
        f = next(o for o in donnees if o["article"] == "PIECE-F")
        self.assertEqual((f["quantite"], f["date_livraison_prevue"], len(f["composants"])), (5.0, "2026-12-10", 2))
        self.assertEqual(self.client.post(url, {}, content_type="application/json").status_code, 400)  # plus rien à créer
        from django.contrib.auth import get_user_model

        lecteur = get_user_model().objects.create_user("lecteur-of", password="pass-mot-de-passe-7")
        self.client.force_login(lecteur)
        self.assertEqual(self.client.post(url, {}, content_type="application/json").status_code, 403)

    def test_devis_lance_en_production_ne_cree_que_la_commande(self):
        from .production import lancer_en_production

        devis = Devis.objects.create(numero="DEV-OF", client=self.tiers, date_creation=datetime.date(2026, 10, 1), statut=Devis.Statut.VALIDE)
        DevisLigne.objects.create(devis=devis, article=self.f, quantite=2)
        commande = lancer_en_production(devis)
        self.assertEqual(commande.ordres_fabrication.count(), 0)
        self.assertEqual(len(creer_ordres_fabrication(commande)), 1)


class DocumentsCommandeTests(_FixtureOrdresCommande, TestCase):
    """AR de commande, bon de préparation et fiche de fabrication (PDF)."""

    def setUp(self):
        super().setUp()
        from comptes.models import Societe

        societe = Societe.charger()
        societe.raison_sociale = "Chaudronnerie Dupont"
        societe.mentions_commande = "Conditions generales de vente disponibles sur demande"
        societe.save()

    def _texte(self, pdf):
        # Le texte d'un PDF ReportLab écrit les accents en octal (\351 = é) : on les remet.
        import re

        brut = pdf.decode("latin-1")
        return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), brut).replace("\\(", "(").replace("\\)", ")")

    def test_ar_de_commande(self):
        from .documents import generer_pdf_ar_commande

        pdf = generer_pdf_ar_commande(self.commande)
        texte = self._texte(pdf)
        self.assertTrue(pdf.startswith(b"%PDF"))
        for attendu in ("ACCUSÉ DE RÉCEPTION DE COMMANDE CDE-OF", "Chaudronnerie Dupont", "Client Fabrication", "BC-4411", "PIECE-F", "Flasque",
                        "20/12/2026", "10/12/2026", "Total HT", "Total TTC", "Conditions generales", "1 rue des Forges"):
            self.assertIn(attendu, texte, attendu)
        # 3×100 + 2×100 + 4×50 + 100×1 = 800 € HT ; 960 € TTC
        self.assertIn("800,00", texte)
        self.assertIn("960,00", texte)
        self.assertNotIn("ANNUL", texte)

    def test_ar_refuse_une_ligne_sans_prix_et_marque_une_commande_annulee(self):
        from .documents import DocumentError, generer_pdf_ar_commande

        CommandeLigne.objects.filter(pk=self.l3.pk).update(prix_vente_unitaire=None)
        with self.assertRaises(DocumentError) as cm:
            generer_pdf_ar_commande(Commande.objects.get(pk=self.commande.pk))
        self.assertIn("PIECE-G", str(cm.exception))
        CommandeLigne.objects.filter(pk=self.l3.pk).update(prix_vente_unitaire=50)
        Commande.objects.filter(pk=self.commande.pk).update(statut=Commande.Statut.ANNULEE)
        self.assertIn("ANNUL", self._texte(generer_pdf_ar_commande(Commande.objects.get(pk=self.commande.pk))))

    def test_bon_de_preparation_sans_prix_avec_of_et_dates(self):
        from .documents import generer_pdf_bon_preparation

        creer_ordres_fabrication(self.commande, regrouper=True)
        texte = self._texte(generer_pdf_bon_preparation(self.commande))
        for attendu in ("BON DE PRÉPARATION CDE-OF", "BC-4411", "PIECE-F", "VIS-OF", "OF-CDE-OF-1", "10/12/2026", "Préparé par"):
            self.assertIn(attendu, texte, attendu)
        for interdit in ("Total HT", "PU HT", "TVA", "800,00"):
            self.assertNotIn(interdit, texte, interdit)

    def test_bon_de_preparation_signale_les_of_a_creer(self):
        from .documents import generer_pdf_bon_preparation

        self.assertIn("OF à créer", self._texte(generer_pdf_bon_preparation(self.commande)))

    def test_fiche_de_fabrication(self):
        from .documents import generer_pdf_ordre_fabrication

        of = next(o for o in creer_ordres_fabrication(self.commande, regrouper=True) if o.article == self.f)
        texte = self._texte(generer_pdf_ordre_fabrication(of))
        for attendu in ("ORDRE DE FABRICATION", of.numero, "PIECE-F", "TOLE-OF", "VIS-OF", "500 × 300", "Nomenclature",
                        "Gamme", "Découpe laser", "Plieuse", "20 min", "10/12/2026"):
            self.assertIn(attendu, texte, attendu)

    def test_telechargements_admin_et_api(self):
        of = creer_ordres_fabrication(self.commande)[0]
        for url in (
            f"/admin/chiffrage/commande/{self.commande.pk}/ar-pdf/",
            f"/admin/chiffrage/commande/{self.commande.pk}/bon-preparation-pdf/",
            f"/admin/chiffrage/ordrefabrication/{of.pk}/pdf/",
            f"/api/v1/commandes/{self.commande.pk}/ar-pdf/",
            f"/api/v1/commandes/{self.commande.pk}/bon-preparation-pdf/",
            f"/api/v1/ordres-fabrication/{of.pk}/pdf/",
        ):
            reponse = self.client.get(url)
            self.assertEqual(reponse.status_code, 200, url)
            self.assertEqual(reponse["Content-Type"], "application/pdf", url)

    def test_magasinier_peut_telecharger_le_bon_de_preparation(self):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Group

        magasinier = get_user_model().objects.create_user("magasin-of", password="pass-mot-de-passe-8", is_staff=True)
        magasinier.groups.add(Group.objects.get(name="Magasinier"))
        self.client.force_login(magasinier)
        reponse = self.client.get(f"/admin/chiffrage/commande/{self.commande.pk}/bon-preparation-pdf/")
        self.assertEqual(reponse.status_code, 200)


class ImpressionGroupeeOFTests(_FixtureOrdresCommande, TestCase):
    """Toutes les fiches de fabrication d'une commande dans un seul PDF, une par page."""

    def _pages(self, pdf):
        return pdf.count(b"/Type /Page\n")

    def test_un_seul_pdf_une_page_par_of(self):
        from .documents import generer_pdf_ordres_fabrication

        ordres = creer_ordres_fabrication(self.commande)  # 3 OF
        pdf = generer_pdf_ordres_fabrication(ordres)
        texte = pdf.decode("latin-1")
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertEqual(self._pages(pdf), 3)
        for of in ordres:
            self.assertIn(of.numero, texte)
        self.assertIn("Page 3 / 3", texte)

    def test_aucun_of_a_imprimer(self):
        from .documents import DocumentError, generer_pdf_ordres_fabrication

        with self.assertRaises(DocumentError):
            generer_pdf_ordres_fabrication([])

    def test_creation_propose_le_lien_d_impression_des_of_crees(self):
        url = f"/admin/chiffrage/commande/{self.commande.pk}/ordres-fabrication/"
        reponse = self.client.post(url, {}, follow=True)
        self.assertContains(reponse, "Imprimer les 3 fiche(s) de fabrication")
        self.assertContains(reponse, f"/admin/chiffrage/commande/{self.commande.pk}/fiches-fabrication-pdf/?ofs=")

    def test_action_de_la_commande_toutes_ou_selection(self):
        ordres = creer_ordres_fabrication(self.commande)
        base = f"/admin/chiffrage/commande/{self.commande.pk}/fiches-fabrication-pdf/"
        tout = self.client.get(base)
        self.assertEqual((tout.status_code, tout["Content-Type"]), (200, "application/pdf"))
        self.assertEqual(self._pages(tout.content), 3)
        un = self.client.get(f"{base}?ofs={ordres[0].numero}")
        self.assertEqual(self._pages(un.content), 1)

    def test_commande_sans_of_redirige_avec_message(self):
        reponse = self.client.get(f"/admin/chiffrage/commande/{self.commande.pk}/fiches-fabrication-pdf/", follow=True)
        self.assertContains(reponse, "Aucun ordre de fabrication à imprimer")

    def test_action_de_liste_des_of_selectionnes(self):
        ordres = creer_ordres_fabrication(self.commande)
        reponse = self.client.post(
            "/admin/chiffrage/ordrefabrication/",
            {"action": "action_imprimer_fiches", "_selected_action": [o.pk for o in ordres[:2]]},
        )
        self.assertEqual((reponse.status_code, reponse["Content-Type"]), (200, "application/pdf"))
        self.assertEqual(self._pages(reponse.content), 2)

    def test_api(self):
        creer_ordres_fabrication(self.commande)
        reponse = self.client.get(f"/api/v1/commandes/{self.commande.pk}/fiches-fabrication-pdf/")
        self.assertEqual((reponse.status_code, reponse["Content-Type"]), (200, "application/pdf"))
        self.assertEqual(self._pages(reponse.content), 3)


class AdressesSurTousLesDocumentsDeVenteTests(_FixtureOrdresCommande, TestCase):
    """Entreprise + adresse de facturation ET de livraison sur chaque document de vente, même si identiques."""

    def setUp(self):
        super().setUp()
        import re

        self.texte = lambda pdf: re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), pdf.decode("latin-1"))

    def _verifier(self, pdf):
        texte = self.texte(pdf)
        self.assertIn("Facturé à", texte)
        self.assertIn("Livré à", texte)
        # Adresse identique à la facturation et à la livraison : le bloc apparaît bien deux fois.
        self.assertEqual(texte.count("Client Fabrication"), 2 + texte.count("Client : Client Fabrication"))
        self.assertEqual(texte.count("1 rue des Forges"), 2)
        self.assertEqual(texte.count("69000 Lyon"), 2)

    def test_ar_bon_de_preparation_fiche_de_fabrication(self):
        from .documents import generer_pdf_ar_commande, generer_pdf_bon_preparation, generer_pdf_ordre_fabrication

        of = creer_ordres_fabrication(self.commande)[0]
        for pdf in (generer_pdf_ar_commande(self.commande), generer_pdf_bon_preparation(self.commande),
                    generer_pdf_ordre_fabrication(of)):
            self._verifier(pdf)

    def test_bon_de_livraison(self):
        from .documents import generer_pdf_bon_livraison

        livraison = Livraison.objects.create(numero="BL-ADR", commande=self.commande, date_livraison=datetime.date(2026, 10, 5))
        LivraisonLigne.objects.create(livraison=livraison, commande_ligne=self.l4, quantite_livree=10)
        self._verifier(generer_pdf_bon_livraison(livraison))

    def test_devis_adresses_choisies_ou_par_defaut_et_adresse_manquante(self):
        from .documents import generer_pdf_devis
        from .moteur import calculer_devis

        devis = Devis.objects.create(numero="DEV-ADR", client=self.tiers, date_creation=datetime.date(2026, 10, 1))
        DevisLigne.objects.create(devis=devis, article=self.vis, quantite=10)
        calculer_devis(devis)
        self._verifier(generer_pdf_devis(devis))  # aucune adresse choisie : celles par défaut du client
        # Client sans aucune adresse : les deux blocs restent là, avec la mention explicite.
        sans = Tiers.objects.create(code="CLI-SANS", raison_sociale="Sans Adresse", type_tiers=Tiers.TypeTiers.CLIENT)
        devis2 = Devis.objects.create(numero="DEV-ADR2", client=sans, date_creation=datetime.date(2026, 10, 1))
        DevisLigne.objects.create(devis=devis2, article=self.vis, quantite=10)
        calculer_devis(devis2)
        texte = self.texte(generer_pdf_devis(devis2))
        self.assertEqual(texte.count("Adresse non renseignée"), 2)
        self.assertIn("Facturé à", texte)
        self.assertIn("Livré à", texte)

    def test_adresses_differentes_chacune_a_sa_place(self):
        from commercial.models import Adresse
        from .documents import generer_pdf_ar_commande

        autre = Adresse.objects.create(tiers=self.tiers, est_livraison=True, adresse="9 quai du Port", code_postal="13000", ville="Marseille")
        Commande.objects.filter(pk=self.commande.pk).update(adresse_livraison=autre)
        texte = self.texte(generer_pdf_ar_commande(Commande.objects.get(pk=self.commande.pk)))
        self.assertIn("1 rue des Forges", texte)
        self.assertIn("9 quai du Port", texte)
        self.assertIn("13000 Marseille", texte)


class FicheDevisDeuxColonnesTests(TestCase):
    """Saisie à gauche, récapitulatif (montants, indices) à droite ; le verrou de version reste présent."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        self.admin = get_user_model().objects.create_superuser("dc-admin", "d@example.com", "pass-mot-de-passe-1")
        self.client.force_login(self.admin)
        self.tiers = Tiers.objects.create(code="CLI-DC", raison_sociale="Client DC", type_tiers=Tiers.TypeTiers.CLIENT)
        self.devis = Devis.objects.create(numero="DEV-DC", client=self.tiers, date_creation=datetime.date(2026, 10, 1))

    def test_fieldsets_saisie_et_recapitulatif(self):
        page = self.client.get(f"/admin/chiffrage/devis/{self.devis.pk}/change/")
        self.assertContains(page, "fiche-saisie")
        self.assertContains(page, "fiche-recap")
        self.assertContains(page, "Récapitulatif")
        for libelle in ("Montant total HT", "Montant total TTC", "Indices de ce devis"):
            self.assertContains(page, libelle)
        self.assertContains(page, 'name="version_verrou"')  # protection contre les écrasements conservée

    def test_formulaire_d_ajout_et_enregistrement_inchanges(self):
        self.assertContains(self.client.get("/admin/chiffrage/devis/add/"), "fiche-recap")
        reponse = self.client.post(
            f"/admin/chiffrage/devis/{self.devis.pk}/change/",
            {"numero": "DEV-DC", "client": self.tiers.pk, "date_creation": "2026-10-01", "statut": "brouillon",
             "issue": "en_attente", "delai": "3 semaines", "lignes-TOTAL_FORMS": "0", "lignes-INITIAL_FORMS": "0",
             "version_verrou": ""},
        )
        erreurs = reponse.context["adminform"].form.errors if reponse.context else None
        self.assertEqual(reponse.status_code, 302, erreurs)
        self.devis.refresh_from_db()
        self.assertEqual(self.devis.delai, "3 semaines")


class FicheCommandeDeuxColonnesTests(_FixtureOrdresCommande, TestCase):
    """Fiche commande : saisie à gauche ; totaux, OF, livraisons et factures à droite."""

    def test_recapitulatif_totaux_et_documents_lies(self):
        from facturation.models import Facture

        ordres = creer_ordres_fabrication(self.commande)
        livraison = Livraison.objects.create(numero="BL-REC", commande=self.commande, date_livraison=datetime.date(2026, 10, 5))
        facture = Facture.objects.create(numero="FAC-REC", commande=self.commande, date_facturation=datetime.date(2026, 10, 6))
        page = self.client.get(f"/admin/chiffrage/commande/{self.commande.pk}/change/")
        for attendu in ("fiche-saisie", "fiche-recap", "Récapitulatif", "800,00 €", "960,00 €", "Ordres de fabrication",
                        "Livraisons", "Factures", "BL-REC", "FAC-REC", ordres[0].numero, 'name="version_verrou"'):
            self.assertContains(page, attendu)
        self.assertContains(page, f"/admin/chiffrage/livraison/{livraison.pk}/change/")
        self.assertContains(page, f"/admin/facturation/facture/{facture.pk}/change/")

    def test_commande_vide_et_formulaire_d_ajout(self):
        self.assertContains(self.client.get("/admin/chiffrage/commande/add/"), "fiche-recap")
        vide = Commande.objects.create(
            numero="CDE-VIDE", client=self.tiers, reference_client="X", date_commande=datetime.date(2026, 10, 1),
            adresse_facturation=self.commande.adresse_facturation, adresse_livraison=self.commande.adresse_livraison,
        )
        page = self.client.get(f"/admin/chiffrage/commande/{vide.pk}/change/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Total HT")

    def test_enregistrement_inchange(self):
        reponse = self.client.get(f"/admin/chiffrage/commande/{self.commande.pk}/change/")
        self.assertContains(reponse, 'name="reference_client"')


class FicheLivraisonDeuxColonnesTests(_FixtureOrdresCommande, TestCase):
    """Fiche livraison : saisie à gauche ; client, contenu, reliquat, facturation et annulation à droite."""

    def setUp(self):
        super().setUp()
        self.livraison = Livraison.objects.create(numero="BL-DC", commande=self.commande, date_livraison=datetime.date(2026, 10, 5))
        LivraisonLigne.objects.create(livraison=self.livraison, commande_ligne=self.l4, quantite_livree=40)
        self.url = f"/admin/chiffrage/livraison/{self.livraison.pk}/change/"

    def test_recapitulatif(self):
        page = self.client.get(self.url)
        for attendu in ("fiche-saisie", "fiche-recap", "Récapitulatif", "Client Fabrication", "1 rue des Forges", "69000 Lyon",
                        "VIS-OF × 40", "VIS-OF : 60 restant(s)", "PIECE-F : 3 restant(s)", "VIS-OF : 40 à facturer",
                        'name="version_verrou"'):
            self.assertContains(page, attendu, msg_prefix="")
        self.assertNotContains(page, "Annulée le")

    def test_livraison_annulee_et_commande_soldee(self):
        self.livraison.annuler(utilisateur=self.admin, motif="Erreur de saisie")
        page = self.client.get(self.url)
        self.assertContains(page, "Annulée le")
        self.assertContains(page, "Erreur de saisie")
        self.assertContains(page, "Tout ce qui est livré est facturé")  # plus rien de livré

    def test_formulaire_d_ajout(self):
        page = self.client.get("/admin/chiffrage/livraison/add/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "fiche-recap")


class FicheOrdreFabricationDeuxColonnesTests(_FixtureOrdresCommande, TestCase):
    """Fiche OF : saisie à gauche ; commande, lignes couvertes, avancement et synchro planning à droite."""

    def setUp(self):
        super().setUp()
        self.of = next(o for o in creer_ordres_fabrication(self.commande, regrouper=True) if o.article == self.f)
        self.url = f"/admin/chiffrage/ordrefabrication/{self.of.pk}/change/"

    def test_recapitulatif(self):
        operations = list(self.of.operations.order_by("ordre"))
        operations[0].temps_reel = 25
        operations[0].quantite_bonne = 4
        operations[0].quantite_rebut = 1
        operations[0].save()
        page = self.client.get(self.url)
        for attendu in ("fiche-saisie", "fiche-recap", "Récapitulatif", "CDE-OF", "Client Fabrication", "Livraison prévue le 10/12/2026",
                        "Lignes de commande couvertes", "Flasque × 3", "Flasque × 2", "1 opération(s) sur 2 avec un temps réel",
                        "Temps prévu 30 min — réel 25 min", "Pièces bonnes : 4 — rebuts : 1",
                        "Synchronisation avec le planning", "En attente — 1 tentative(s)", "PLANNING_API_URL non configuré"):
            self.assertContains(page, attendu)
        self.assertContains(page, "/admin/chiffrage/commande/CDE-OF/change/")
        self.assertContains(page, "Nomenclature")  # les inlines restent sous les deux colonnes
        self.assertContains(page, "Gamme")

    def test_of_sans_operation_et_formulaire_d_ajout(self):
        self.of.operations.all().delete()
        self.assertContains(self.client.get(self.url), "Aucune opération de gamme")
        self.assertContains(self.client.get("/admin/chiffrage/ordrefabrication/add/"), "fiche-recap")

    def test_enregistrement_inchange(self):
        reponse = self.client.post(
            self.url,
            {"numero": self.of.pk, "commande": "CDE-OF", "article": "PIECE-F", "quantite": "5", "date_lancement": "2026-10-03",
             "date_livraison_prevue": "2026-12-10", "statut": "En cours",
             "operations-TOTAL_FORMS": "0", "operations-INITIAL_FORMS": "0",
             "composants-TOTAL_FORMS": "0", "composants-INITIAL_FORMS": "0"},
        )
        self.assertEqual(reponse.status_code, 302, reponse.context["adminform"].form.errors if reponse.context else "")
        self.of.refresh_from_db()
        self.assertEqual(self.of.statut, "En cours")
        self.assertEqual(self.of.nombre_tentatives, 1)  # la synchronisation n'est pas modifiable à la main


class EtapeSuivanteDevisTests(_FixtureModuleA, TestCase):
    """Bouton « Étape suivante » du devis, et ouverture directe de la commande créée."""

    def url(self):
        return f"/admin/chiffrage/devis/{self.devis.pk}/etape-suivante/"

    def test_devis_brouillon_pas_de_bouton(self):
        page = self.client.get(f"/admin/chiffrage/devis/{self.devis.pk}/change/")
        self.assertNotContains(page, "Étape suivante")
        reponse = self.client.get(self.url(), follow=True)
        self.assertContains(reponse, "aucune étape suivante")
        self.assertFalse(Commande.objects.filter(devis=self.devis).exists())

    def test_creer_la_commande_puis_ouvrir_la_commande(self):
        self._valider()
        page = self.client.get(f"/admin/chiffrage/devis/{self.devis.pk}/change/")
        self.assertContains(page, "Étape suivante : Créer la commande")
        reponse = self.client.get(self.url(), follow=True)
        commande = Commande.objects.get(devis=self.devis)
        self.assertEqual(reponse.redirect_chain[-1][0], f"/admin/chiffrage/commande/{commande.pk}/change/")
        self.assertContains(reponse, "créée. Étape suivante : créer la livraison.")
        # La commande existe : le bouton devient « Ouvrir la commande » et ne crée rien de plus.
        page = self.client.get(f"/admin/chiffrage/devis/{self.devis.pk}/change/")
        self.assertContains(page, "Étape suivante : Ouvrir la commande")
        reponse = self.client.get(self.url())
        self.assertRedirects(reponse, f"/admin/chiffrage/commande/{commande.pk}/change/", fetch_redirect_response=False)
        self.assertEqual(Commande.objects.filter(devis=self.devis).count(), 1)

    def test_devis_refuse_ou_remplace_pas_d_etape(self):
        self._valider()
        self.devis.issue = Devis.Issue.REFUSE
        self.devis.save()
        self.assertNotContains(self.client.get(f"/admin/chiffrage/devis/{self.devis.pk}/change/"), "Étape suivante")

    def test_action_de_liste_ouvre_directement_la_commande(self):
        self._valider()
        reponse = self.client.post(
            "/admin/chiffrage/devis/", {"action": "action_lancer_en_production", "_selected_action": [self.devis.pk]}, follow=True
        )
        commande = Commande.objects.get(devis=self.devis)
        self.assertEqual(reponse.redirect_chain[-1][0], f"/admin/chiffrage/commande/{commande.pk}/change/")
        self.assertContains(reponse, "créée")

    def test_sans_droit_de_creer_une_commande(self):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Permission

        self._valider()
        simple = get_user_model().objects.create_user("lecteur-etape", "l@example.com", "pass-mot-de-passe-7", is_staff=True)
        simple.user_permissions.add(Permission.objects.get(codename="view_devis"))
        self.client.force_login(simple)
        reponse = self.client.get(self.url(), follow=True)
        self.assertContains(reponse, "pas la permission de créer une commande")
        self.assertFalse(Commande.objects.filter(devis=self.devis).exists())


class EtapeSuivanteCommandeTests(_FixtureOrdresCommande, TestCase):
    """De la commande : ordres de fabrication, puis livraison pré-remplie, puis facture."""

    def url(self):
        return f"/admin/chiffrage/commande/{self.commande.pk}/etape-suivante/"

    def test_parcours_complet(self):
        fiche = f"/admin/chiffrage/commande/{self.commande.pk}/change/"
        self.assertContains(self.client.get(fiche), "Étape suivante : Créer les ordres de fabrication")
        self.assertRedirects(
            self.client.get(self.url()), f"/admin/chiffrage/commande/{self.commande.pk}/ordres-fabrication/", fetch_redirect_response=False
        )
        creer_ordres_fabrication(self.commande, regrouper=True)

        # Plus d'ordre à créer : on passe à la livraison, formulaire ouvert avec les reliquats.
        self.assertContains(self.client.get(fiche), "Étape suivante : Créer la livraison")
        reponse = self.client.get(self.url())
        self.assertRedirects(reponse, f"/admin/chiffrage/livraison/add/?commande={self.commande.pk}", fetch_redirect_response=False)
        page = self.client.get(reponse["Location"])
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.context["adminform"].form.initial.get("commande"), self.commande.pk)
        lignes = page.context["inline_admin_formsets"][0].formset
        self.assertEqual(
            sorted((f.initial["commande_ligne"], f.initial["quantite_livree"]) for f in lignes.forms if f.initial),
            sorted((l.pk, l.reliquat) for l in (self.l1, self.l2, self.l3, self.l4)),
        )
        self.assertEqual(len(lignes.forms), 4)  # une ligne par reliquat

        # Tout livré : reste la facture.
        livraison = Livraison.objects.create(numero="BL-ETAPE", commande=self.commande, date_livraison=datetime.date(2026, 10, 5))
        for ligne in (self.l1, self.l2, self.l3, self.l4):
            LivraisonLigne.objects.create(livraison=livraison, commande_ligne=ligne, quantite_livree=ligne.quantite_commandee)
        self.assertContains(self.client.get(fiche), "Étape suivante : Préparer la facture")
        self.assertRedirects(
            self.client.get(self.url()), f"/admin/facturation/facture/preparer/?commande={self.commande.pk}", fetch_redirect_response=False
        )
        page = self.client.get(f"/admin/facturation/facture/preparer/?commande={self.commande.pk}")
        self.assertContains(page, "CDE-OF")
        self.assertContains(page, "Voir toutes les commandes à facturer")

    def test_livraison_prepare_la_facture(self):
        livraison = Livraison.objects.create(numero="BL-ETAPE2", commande=self.commande, date_livraison=datetime.date(2026, 10, 5))
        LivraisonLigne.objects.create(livraison=livraison, commande_ligne=self.l4, quantite_livree=40)
        creer_ordres_fabrication(self.commande, regrouper=True)
        page = self.client.get(f"/admin/chiffrage/livraison/{livraison.pk}/change/")
        self.assertContains(page, "Étape suivante : Préparer la facture")
        self.assertRedirects(
            self.client.get(f"/admin/chiffrage/livraison/{livraison.pk}/etape-suivante/"),
            f"/admin/facturation/facture/preparer/?commande={self.commande.pk}", fetch_redirect_response=False,
        )

    def test_commande_annulee_pas_d_etape(self):
        self.commande.annuler()
        self.assertNotContains(self.client.get(f"/admin/chiffrage/commande/{self.commande.pk}/change/"), "Étape suivante")

    def test_sans_droit_de_creer_des_ordres(self):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Permission

        simple = get_user_model().objects.create_user("lecteur-etape2", "l2@example.com", "pass-mot-de-passe-8", is_staff=True)
        simple.user_permissions.add(Permission.objects.get(codename="view_commande"))
        self.client.force_login(simple)
        reponse = self.client.get(self.url(), follow=True)
        self.assertContains(reponse, "pas la permission de créer des ordres de fabrication")


class ThemePastillesTests(_FixtureOrdresCommande, TestCase):
    """Thème : statuts en pastilles colorées dans les listes, tuiles colorées à l'accueil, export CSV inchangé."""

    def test_pastilles_dans_les_listes(self):
        devis = Devis.objects.create(numero="DEV-PAST", client=self.tiers, date_creation=datetime.date(2026, 10, 1))
        page = self.client.get("/admin/chiffrage/devis/")
        self.assertContains(page, "Brouillon")
        self.assertContains(page, "bg-blue-100")  # brouillon : bleu
        self.assertContains(page, "bg-orange-100")  # réponse du client en attente : orange
        self.assertContains(self.client.get("/admin/chiffrage/commande/"), "bg-blue-100")  # commande en cours
        Devis.objects.filter(pk=devis.pk).update(statut="valide", issue="refuse")
        page = self.client.get("/admin/chiffrage/devis/")
        self.assertContains(page, "bg-green-100")
        self.assertContains(page, "bg-red-100")

    def test_tri_et_export_csv_inchanges(self):
        Devis.objects.create(numero="DEV-PAST2", client=self.tiers, date_creation=datetime.date(2026, 10, 1))
        self.assertEqual(self.client.get("/admin/chiffrage/devis/?o=5").status_code, 200)  # tri sur la colonne du statut
        reponse = self.client.post(
            "/admin/chiffrage/devis/", {"action": "exporter_csv", "_selected_action": ["DEV-PAST2"]}
        )
        texte = reponse.content.decode("utf-8-sig")
        self.assertIn("Statut", texte.splitlines()[0])
        self.assertIn("Brouillon", texte)

    def test_tuiles_colorees_a_l_accueil(self):
        page = self.client.get("/admin/")
        for attendu in ("tuile-ventes", "tuile-atelier", "tuile-tresorerie", "tuile-icone"):
            self.assertContains(page, attendu)


class RechercheGlobaleEtMenuNouveauTests(_FixtureOrdresCommande, TestCase):
    """Ctrl+K : documents et écrans, selon les droits ; menu « + Nouveau » filtré par les droits."""

    def setUp(self):
        from django.core.cache import cache

        super().setUp()
        cache.clear()  # Unfold met les résultats en cache par utilisateur et par terme

    def chercher(self, terme):
        return self.client.get("/admin/search/", {"s": terme, "extended": "1"})

    def test_trouve_documents_par_numero_et_par_client(self):
        for terme in ("CDE-OF", "Fabrication"):
            page = self.chercher(terme)
            self.assertContains(page, "CDE-OF")
            self.assertContains(page, "/admin/chiffrage/commande/CDE-OF/change/")
            self.assertContains(page, "Client Fabrication")
        self.assertContains(self.chercher("PIECE-F"), "/admin/technique/article/PIECE-F/change/")

    def test_trouve_les_ecrans_sans_tenir_compte_des_accents(self):
        page = self.chercher("retard")
        self.assertContains(page, "Factures en retard")
        self.assertContains(page, "retard=en_retard")
        self.assertContains(self.chercher("echec"), "Ordres de fabrication non transmis")
        self.assertContains(self.chercher("nouveau devis"), "/admin/chiffrage/devis/add/")

    def test_terme_trop_court_ou_inconnu(self):
        self.assertNotContains(self.chercher("C"), "/admin/chiffrage/commande/CDE-OF/")
        self.assertNotContains(self.chercher("zzzintrouvable"), "/change/")

    def test_la_recherche_respecte_les_droits(self):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Permission

        lecteur = get_user_model().objects.create_user("lecteur-recherche", "lr@example.com", "pass-mot-de-passe-9", is_staff=True)
        lecteur.user_permissions.add(Permission.objects.get(codename="view_commande"))
        self.client.force_login(lecteur)
        page = self.chercher("CDE-OF")
        self.assertContains(page, "/admin/chiffrage/commande/CDE-OF/change/")
        self.assertNotContains(page, "Nouveau devis")
        self.assertNotContains(self.chercher("PIECE-F"), "/admin/technique/article/PIECE-F/change/")  # pas le droit de voir les articles
        self.assertNotContains(self.chercher("retard"), "Factures en retard")

    def test_menu_nouveau_selon_les_droits(self):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Permission

        page = self.client.get("/admin/")
        for attendu in ("menu-nouveau", "Nouveau devis", "Nouvelle livraison", "Préparer une facture", "Nouvel article"):
            self.assertContains(page, attendu)
        lecteur = get_user_model().objects.create_user("lecteur-menu", "lm@example.com", "pass-mot-de-passe-10", is_staff=True)
        lecteur.user_permissions.add(Permission.objects.get(codename="add_devis"), Permission.objects.get(codename="view_devis"))
        self.client.force_login(lecteur)
        page = self.client.get("/admin/")
        self.assertContains(page, "Nouveau devis")
        self.assertNotContains(page, "Nouvel article")
        self.assertNotContains(page, "Nouvelle livraison")
        sans_droit = get_user_model().objects.create_user("sans-droit-menu", "sd@example.com", "pass-mot-de-passe-11", is_staff=True)
        self.client.force_login(sans_droit)
        self.assertNotContains(self.client.get("/admin/"), "menu-nouveau-bouton")


class EnregistrerEtValiderTests(_FixtureModuleA, TestCase):
    """Bouton « Enregistrer et valider » : enregistre, valide le devis, ouvre le PDF."""

    def payload(self, devis, ligne=None):
        donnees = {
            "numero": devis.pk, "client": self.tiers.pk, "date_creation": "01/01/2026", "statut": "brouillon", "issue": "en_attente",
            "taux_marge_globale": "", "adresse_facturation": "", "adresse_livraison": "", "contact": "", "delai": "",
            "lignes-TOTAL_FORMS": "1" if ligne else "0", "lignes-INITIAL_FORMS": "1" if ligne else "0",
            "lignes-MIN_NUM_FORMS": "0", "lignes-MAX_NUM_FORMS": "1000",
            "_enregistrer_valider": "1",
        }
        if ligne:
            donnees.update({
                "lignes-0-id": str(ligne.pk), "lignes-0-devis": devis.pk, "lignes-0-ordre": "0", "lignes-0-article": self.article.pk,
                "lignes-0-quantite": "3.0", "lignes-0-taux_marge_matiere_applique": "", "lignes-0-prix_vente_unitaire_force": "",
                "lignes-0-taux_tva": "",
            })
        return donnees

    def test_bouton_visible_pour_qui_peut_valider(self):
        page = self.client.get(f"/admin/chiffrage/devis/{self.devis.pk}/change/")
        self.assertContains(page, "Enregistrer, valider et ouvrir le PDF")
        self.assertContains(page, 'name="_enregistrer_valider"')

    def test_devis_enregistre_valide_puis_pdf(self):
        reponse = self.client.post(f"/admin/chiffrage/devis/{self.devis.pk}/change/", self.payload(self.devis, self.ligne))
        self.assertRedirects(reponse, f"/admin/chiffrage/devis/{self.devis.pk}/pdf/", fetch_redirect_response=False)
        self.devis.refresh_from_db()
        self.assertEqual(self.devis.statut, Devis.Statut.VALIDE)
        pdf = self.client.get(reponse["Location"])
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        self.assertTrue(pdf.content.startswith(b"%PDF"))

    def test_validation_refusee_on_reste_sur_la_fiche(self):
        vide = Devis.objects.create(numero="DEV-VIDE", client=self.tiers, date_creation=datetime.date(2026, 1, 1))
        reponse = self.client.post(f"/admin/chiffrage/devis/{vide.pk}/change/", self.payload(vide), follow=True)
        self.assertEqual(reponse.redirect_chain[-1][0], f"/admin/chiffrage/devis/{vide.pk}/change/")
        self.assertContains(reponse, "aucune ligne")
        self.assertContains(reponse, "enregistré, mais pas validé")
        vide.refresh_from_db()
        self.assertEqual(vide.statut, Devis.Statut.BROUILLON)

    def test_deja_valide_ouvre_simplement_le_pdf(self):
        self._valider()
        donnees = {"statut": "valide", "issue": "en_attente", "lignes-TOTAL_FORMS": "0", "lignes-INITIAL_FORMS": "0",
                   "_enregistrer_valider": "1"}
        reponse = self.client.post(f"/admin/chiffrage/devis/{self.devis.pk}/change/", donnees)
        self.assertRedirects(reponse, f"/admin/chiffrage/devis/{self.devis.pk}/pdf/", fetch_redirect_response=False)

    def test_nouvel_onglet_reste_sur_la_fiche_et_porte_l_adresse_du_pdf(self):
        self._valider()
        donnees = {"statut": "valide", "issue": "en_attente", "lignes-TOTAL_FORMS": "0", "lignes-INITIAL_FORMS": "0",
                   "_enregistrer_valider": "1", "_nouvel_onglet": "1"}
        reponse = self.client.post(f"/admin/chiffrage/devis/{self.devis.pk}/change/", donnees)
        fiche = f"/admin/chiffrage/devis/{self.devis.pk}/change/"
        self.assertRedirects(reponse, f"{fiche}?ouvrir_pdf=%2Fadmin%2Fchiffrage%2Fdevis%2F{self.devis.pk}%2Fpdf%2F", fetch_redirect_response=False)
        self.assertContains(self.client.get(fiche), "pdf_nouvel_onglet")

    def test_sans_droit_de_valider_pas_de_bouton_ni_validation(self):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Permission

        editeur = get_user_model().objects.create_user("editeur-devis", "e@example.com", "pass-mot-de-passe-12", is_staff=True)
        editeur.user_permissions.add(*Permission.objects.filter(codename__in=["view_devis", "change_devis", "view_devisligne"]))
        self.client.force_login(editeur)
        page = self.client.get(f"/admin/chiffrage/devis/{self.devis.pk}/change/")
        self.assertNotContains(page, "Enregistrer, valider et ouvrir le PDF")
        self.client.post(f"/admin/chiffrage/devis/{self.devis.pk}/change/", self.payload(self.devis, self.ligne))
        self.devis.refresh_from_db()
        self.assertEqual(self.devis.statut, Devis.Statut.BROUILLON)


class EnregistrerEtOuvrirPdfTests(_FixtureOrdresCommande, TestCase):
    """Même bouton sur la commande (AR), la livraison (BL) et l'ordre de fabrication (fiche)."""

    def test_boutons_presents(self):
        of = creer_ordres_fabrication(self.commande, regrouper=True)[0]
        livraison = Livraison.objects.create(numero="BL-BTN", commande=self.commande, date_livraison=datetime.date(2026, 10, 5))
        for url, libelle in (
            (f"/admin/chiffrage/commande/{self.commande.pk}/change/", "Enregistrer et ouvrir l&#x27;AR (PDF)"),
            (f"/admin/chiffrage/livraison/{livraison.pk}/change/", "Enregistrer et ouvrir le BL (PDF)"),
            (f"/admin/chiffrage/ordrefabrication/{of.pk}/change/", "Enregistrer et ouvrir la fiche (PDF)"),
        ):
            self.assertContains(self.client.get(url), libelle)

    def test_of_enregistre_puis_pdf(self):
        of = next(o for o in creer_ordres_fabrication(self.commande, regrouper=True) if o.article == self.f)
        reponse = self.client.post(
            f"/admin/chiffrage/ordrefabrication/{of.pk}/change/",
            {"numero": of.pk, "commande": "CDE-OF", "article": "PIECE-F", "quantite": "5", "date_lancement": "2026-10-03",
             "date_livraison_prevue": "2026-12-10", "statut": "En cours",
             "operations-TOTAL_FORMS": "0", "operations-INITIAL_FORMS": "0",
             "composants-TOTAL_FORMS": "0", "composants-INITIAL_FORMS": "0", "_enregistrer_valider": "1"},
        )
        self.assertRedirects(reponse, f"/admin/chiffrage/ordrefabrication/{of.pk}/pdf/", fetch_redirect_response=False)
        of.refresh_from_db()
        self.assertEqual(of.statut, "En cours")


class MesColonnesTests(_FixtureOrdresCommande, TestCase):
    """« Mes colonnes » : chaque utilisateur choisit les colonnes des listes et des tableaux de lignes."""

    def setUp(self):
        super().setUp()
        self.fiche = f"/admin/chiffrage/commande/{self.commande.pk}/change/"

    def test_page_liste_les_ecrans_et_les_colonnes(self):
        page = self.client.get("/admin/mes-colonnes/")
        for attendu in ("Lignes de la fiche", "Statut d&#x27;approvisionnement", "Liste : Devis", "Liste : Commandes",
                        "Liste : Factures", "Liste : Articles", "Rétablir les colonnes par défaut"):
            self.assertContains(page, attendu)
        self.assertContains(page, "/admin/mes-colonnes/")  # lien dans le menu du compte

    def test_masquer_des_colonnes_de_la_fiche_et_de_la_liste(self):
        self.assertContains(self.client.get(self.fiche), "Statut d&#x27;approvisionnement")
        reponse = self.client.post("/admin/mes-colonnes/", {
            "chiffrage.commande:commandeligne|designation": "on",
            "chiffrage.commande:commandeligne|quantite_livree": "on",
            "chiffrage.commande:commandeligne|reliquat": "on",
            # absentes (donc masquées) : date_livraison_possible_display, statut_approvisionnement, entierement_livree…
            "chiffrage.commande|numero": "on", "chiffrage.commande|client": "on",  # la liste garde ces colonnes seulement
        })
        self.assertRedirects(reponse, "/admin/mes-colonnes/", fetch_redirect_response=False)
        fiche = self.client.get(self.fiche)
        self.assertNotContains(fiche, "Statut d&#x27;approvisionnement")
        self.assertNotContains(fiche, "Date de livraison possible")
        self.assertContains(fiche, "Reliquat")
        self.assertContains(fiche, "Prix de vente unitaire")  # colonnes indispensables toujours là
        liste = self.client.get("/admin/chiffrage/commande/")
        self.assertNotContains(liste, "Réf. commande client")
        self.assertContains(liste, "Client")

    def test_reglage_personnel(self):
        from django.contrib.auth import get_user_model

        self.client.post("/admin/mes-colonnes/", {"chiffrage.commande:commandeligne|reliquat": "on"})
        autre = get_user_model().objects.create_superuser("autre-colonnes", "ac@example.com", "pass-mot-de-passe-13")
        self.client.force_login(autre)
        self.assertContains(self.client.get(self.fiche), "Statut d&#x27;approvisionnement")  # inchangé pour un autre utilisateur

    def test_rien_n_est_masque_de_force_et_defaut_retabli(self):
        from comptes.models import PreferenceColonnes

        PreferenceColonnes.objects.create(
            utilisateur=self.admin, ecran="chiffrage.commande:commandeligne", masquees=["article", "montant_ht", "reliquat"]
        )
        fiche = self.client.get(self.fiche)
        self.assertContains(fiche, "Montant HT")  # non masquable : ignoré
        self.assertContains(fiche, "Article")
        self.assertNotContains(fiche, "Reliquat")
        reponse = self.client.post("/admin/mes-colonnes/", {"defaut": "1"})
        self.assertEqual(reponse.status_code, 302)
        self.assertFalse(PreferenceColonnes.objects.filter(utilisateur=self.admin).exists())
        self.assertContains(self.client.get(self.fiche), "Reliquat")

    def test_enregistrer_la_fiche_sans_les_colonnes_masquees(self):
        self.client.post("/admin/mes-colonnes/", {"chiffrage.commande:commandeligne|reliquat": "on"})
        fiche = self.client.get(self.fiche)
        self.assertEqual(fiche.status_code, 200)
        formset = fiche.context["inline_admin_formsets"][0].formset
        self.assertNotIn("designation", formset.empty_form.fields)


class ConstructeurDroitsTests(TestCase):
    """L'assistant « Ajouter une ligne » exige les droits de modification du devis et de création d'articles."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        self.tiers = Tiers.objects.create(code="CLI-DRT", raison_sociale="Client Droits", type_tiers=Tiers.TypeTiers.CLIENT)
        self.devis = Devis.objects.create(numero="DEV-DRT", client=self.tiers, date_creation=datetime.date(2026, 1, 1))
        self.url = f"/admin/chiffrage/devis/{self.devis.pk}/constructeur/"
        self.User = get_user_model()

    def utilisateur(self, nom, codes):
        from django.contrib.auth.models import Permission

        u = self.User.objects.create_user(nom, password="pass-mot-de-passe-20", is_staff=True)
        u.user_permissions.set(Permission.objects.filter(codename__in=codes))
        self.client.force_login(u)
        return u

    def poster(self, **extra):
        return self.client.post(self.url, data={"quantite": 1, "nouvel_article": {"reference": "ART-DRT", "composants": [], "etapes": []}, **extra}, content_type="application/json")

    def test_staff_sans_droit_refuse(self):
        self.utilisateur("sans-droit", [])
        self.assertEqual(self.poster().status_code, 403)
        self.assertFalse(Article.objects.filter(pk="ART-DRT").exists())

    def test_modifier_le_devis_ne_suffit_pas_pour_creer_un_article(self):
        self.utilisateur("devis-seul", ["change_devis", "add_devisligne"])
        reponse = self.poster()
        self.assertEqual(reponse.status_code, 403)
        self.assertIn("article fabriqué", reponse.json()["detail"])
        self.assertFalse(Article.objects.filter(pk="ART-DRT").exists())

    def test_devis_valide_refuse(self):
        self.utilisateur("tout-droit", ["change_devis", "add_devisligne", "add_article", "add_nomenclature", "add_gamme"])
        Devis.objects.filter(pk=self.devis.pk).update(statut=Devis.Statut.VALIDE)
        self.assertEqual(self.poster().status_code, 409)
