import datetime

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from chiffrage.models import Commande, CommandeLigne, Devis
from commercial.models import Adresse, Tiers
from comptabilite.models import PosteGestion
from stock.models import AlerteStock, Emplacement, Lot, MouvementStock
from technique.models import Article

from .models import (
    AchatsError,
    ArticleFournisseur,
    CommandeFournisseur,
    LigneCommandeFournisseur,
    Reception,
    ReceptionLigne,
    TarifAchatArticle,
)


class ReceptionTests(TestCase):
    def setUp(self):
        self.fournisseur = Tiers.objects.create(
            code="FOUR-001", raison_sociale="Fournisseur Test", type_tiers=Tiers.TypeTiers.FOURNISSEUR
        )
        self.article = Article.objects.create(
            reference="TOLE-ACH-01", nature=Article.Nature.MATIERE_PREMIERE, cout_unitaire=2.0
        )
        self.commande = CommandeFournisseur.objects.create(
            numero="CF-001", fournisseur=self.fournisseur, date_commande=datetime.date(2026, 1, 1)
        )
        self.ligne = LigneCommandeFournisseur.objects.create(
            commande_fournisseur=self.commande,
            article=self.article,
            quantite_commandee=100,
            prix_unitaire_achat=2.0,
        )
        self.reception = Reception.objects.create(
            numero="REC-001", commande_fournisseur=self.commande, date_reception=datetime.date(2026, 1, 10)
        )

    def test_sans_lot_leve_erreur(self):
        rl = ReceptionLigne(reception=self.reception, ligne_commande_fournisseur=self.ligne, quantite_recue=50)
        with self.assertRaises(AchatsError):
            rl.save()

    def test_reception_cree_mouvement_et_maj_quantite_recue(self):
        emplacement = Emplacement.objects.create(code="ACH-A1")
        Lot.objects.create(article=self.article, emplacement=emplacement, quantite=0)

        ReceptionLigne.objects.create(
            reception=self.reception, ligne_commande_fournisseur=self.ligne, quantite_recue=50
        )

        self.ligne.refresh_from_db()
        self.assertEqual(self.ligne.quantite_recue, 50)

        lot = Lot.objects.get(article=self.article)
        self.assertEqual(lot.quantite, 50)
        mouvement = MouvementStock.objects.get(lot=lot)
        self.assertEqual(mouvement.type_mouvement, MouvementStock.TypeMouvement.ENTREE)
        self.assertEqual(mouvement.reference_origine, "RECEPTION-REC-001")

    def test_plusieurs_lots_leve_erreur(self):
        e1 = Emplacement.objects.create(code="ACH-B1")
        e2 = Emplacement.objects.create(code="ACH-B2")
        Lot.objects.create(article=self.article, emplacement=e1, quantite=0)
        Lot.objects.create(article=self.article, emplacement=e2, quantite=0)

        rl = ReceptionLigne(reception=self.reception, ligne_commande_fournisseur=self.ligne, quantite_recue=10)
        with self.assertRaises(AchatsError):
            rl.save()

    def test_depassement_quantite_commandee_refuse(self):
        rl = ReceptionLigne(
            reception=self.reception, ligne_commande_fournisseur=self.ligne, quantite_recue=150
        )
        with self.assertRaises(ValidationError):
            rl.full_clean()


class AlerteStockClotureTests(TestCase):
    def test_creation_ligne_cloture_alerte_active(self):
        fournisseur = Tiers.objects.create(
            code="FOUR-002", raison_sociale="Fournisseur B", type_tiers=Tiers.TypeTiers.FOURNISSEUR
        )
        article = Article.objects.create(
            reference="TOLE-ACH-02", nature=Article.Nature.MATIERE_PREMIERE, stock_mini=10
        )
        alerte = AlerteStock.objects.create(article=article, date_declenchement=datetime.date(2026, 1, 1))
        commande = CommandeFournisseur.objects.create(
            numero="CF-002", fournisseur=fournisseur, date_commande=datetime.date(2026, 1, 5)
        )

        LigneCommandeFournisseur.objects.create(
            commande_fournisseur=commande,
            article=article,
            alerte_stock_origine=alerte,
            quantite_commandee=50,
            prix_unitaire_achat=1.5,
        )

        alerte.refresh_from_db()
        self.assertEqual(alerte.statut, AlerteStock.Statut.TRAITEE)
        self.assertIsNotNone(alerte.date_traitement)


class CommandeLigneClientTests(TestCase):
    """Rattachement d'une ligne de commande fournisseur à une ligne de
    commande client (traçabilité de l'approvisionnement) : clôture
    automatique d'une alerte de stock active pour le même article, sans
    sélection manuelle de alerte_stock_origine."""

    def setUp(self):
        self.fournisseur = Tiers.objects.create(
            code="FOUR-CLI", raison_sociale="Fournisseur Client", type_tiers=Tiers.TypeTiers.FOURNISSEUR
        )
        self.client_tiers = Tiers.objects.create(code="CLI-APPRO", raison_sociale="Client Appro")
        self.adresse = Adresse.objects.create(
            tiers=self.client_tiers, type_adresse=Adresse.TypeAdresse.FACTURATION,
            adresse="1 rue A", code_postal="75000", ville="Paris",
        )
        self.article = Article.objects.create(reference="TOLE-APPRO", nature=Article.Nature.MATIERE_PREMIERE)
        devis = Devis.objects.create(
            numero="DEV-APPRO", client=self.client_tiers, date_creation=datetime.date(2026, 1, 1),
        )
        self.commande_client = Commande.objects.create(
            numero="CDE-APPRO", devis=devis, date_commande=datetime.date(2026, 1, 1),
            adresse_facturation=self.adresse, adresse_livraison=self.adresse,
        )
        self.ligne_client = CommandeLigne.objects.create(
            commande=self.commande_client, article=self.article, quantite_commandee=20
        )
        self.commande_fournisseur = CommandeFournisseur.objects.create(
            numero="CF-APPRO", fournisseur=self.fournisseur, date_commande=datetime.date(2026, 1, 1),
            date_livraison_prevue=datetime.date(2026, 2, 1),
        )

    def test_rattachement_cloture_alerte_active_sans_selection_manuelle(self):
        alerte = AlerteStock.objects.create(article=self.article, date_declenchement=datetime.date(2026, 1, 1))
        LigneCommandeFournisseur.objects.create(
            commande_fournisseur=self.commande_fournisseur,
            article=self.article,
            commande_ligne_client=self.ligne_client,
            quantite_commandee=20,
            prix_unitaire_achat=3.0,
        )
        alerte.refresh_from_db()
        self.assertEqual(alerte.statut, AlerteStock.Statut.TRAITEE)

    def test_alerte_deja_traitee_non_reprise(self):
        alerte_traitee = AlerteStock.objects.create(
            article=self.article, date_declenchement=datetime.date(2026, 1, 1),
            statut=AlerteStock.Statut.TRAITEE, date_traitement=datetime.date(2026, 1, 2),
        )
        ligne = LigneCommandeFournisseur.objects.create(
            commande_fournisseur=self.commande_fournisseur,
            article=self.article,
            commande_ligne_client=self.ligne_client,
            quantite_commandee=20,
            prix_unitaire_achat=3.0,
        )
        # Une alerte déjà traitée n'est jamais reprise comme alerte_stock_origine.
        self.assertIsNone(ligne.alerte_stock_origine)
        self.assertNotEqual(ligne.alerte_stock_origine_id, alerte_traitee.pk)

    def test_selection_manuelle_respectee_sans_ecrasement(self):
        autre_alerte = AlerteStock.objects.create(
            article=self.article, date_declenchement=datetime.date(2026, 1, 1)
        )
        ligne = LigneCommandeFournisseur.objects.create(
            commande_fournisseur=self.commande_fournisseur,
            article=self.article,
            commande_ligne_client=self.ligne_client,
            alerte_stock_origine=autre_alerte,
            quantite_commandee=20,
            prix_unitaire_achat=3.0,
        )
        self.assertEqual(ligne.alerte_stock_origine, autre_alerte)

    def test_date_livraison_possible_reprend_la_date_fournisseur(self):
        self.assertIsNone(self.ligne_client.date_livraison_possible)
        LigneCommandeFournisseur.objects.create(
            commande_fournisseur=self.commande_fournisseur,
            article=self.article,
            commande_ligne_client=self.ligne_client,
            quantite_commandee=20,
            prix_unitaire_achat=3.0,
        )
        self.assertEqual(self.ligne_client.date_livraison_possible, datetime.date(2026, 2, 1))

    def test_date_livraison_possible_prend_la_plus_tardive(self):
        LigneCommandeFournisseur.objects.create(
            commande_fournisseur=self.commande_fournisseur,
            article=self.article,
            commande_ligne_client=self.ligne_client,
            quantite_commandee=10,
            prix_unitaire_achat=3.0,
        )
        commande_fournisseur_2 = CommandeFournisseur.objects.create(
            numero="CF-APPRO-2", fournisseur=self.fournisseur, date_commande=datetime.date(2026, 1, 1),
            date_livraison_prevue=datetime.date(2026, 3, 15),
        )
        LigneCommandeFournisseur.objects.create(
            commande_fournisseur=commande_fournisseur_2,
            article=self.article,
            commande_ligne_client=self.ligne_client,
            quantite_commandee=10,
            prix_unitaire_achat=3.0,
        )
        self.assertEqual(self.ligne_client.date_livraison_possible, datetime.date(2026, 3, 15))

    def test_date_livraison_prevue_client_jamais_ecrasee(self):
        self.ligne_client.date_livraison_prevue = datetime.date(2026, 1, 20)
        self.ligne_client.save()
        LigneCommandeFournisseur.objects.create(
            commande_fournisseur=self.commande_fournisseur,
            article=self.article,
            commande_ligne_client=self.ligne_client,
            quantite_commandee=20,
            prix_unitaire_achat=3.0,
        )
        self.ligne_client.refresh_from_db()
        self.assertEqual(self.ligne_client.date_livraison_prevue, datetime.date(2026, 1, 20))

    def test_statut_approvisionnement(self):
        self.assertIsNone(self.ligne_client.statut_approvisionnement)
        ligne_achat = LigneCommandeFournisseur.objects.create(
            commande_fournisseur=self.commande_fournisseur,
            article=self.article,
            commande_ligne_client=self.ligne_client,
            quantite_commandee=20,
            prix_unitaire_achat=3.0,
        )
        LigneCommandeFournisseur.objects.filter(pk=ligne_achat.pk).update(quantite_recue=8)
        statut = self.ligne_client.statut_approvisionnement
        self.assertIn("Fournisseur Client", statut)
        self.assertIn("8/20", statut)

    def test_date_livraison_possible_affichee_au_format_francais_dans_admin(self):
        # Une propriété (pas un vrai champ de modèle) affichée en readonly
        # dans l'admin Unfold passe par str(date) et non par le format
        # localisé — sans le wrapper *_display, elle apparaît en ISO
        # (aaaa-mm-jj) au lieu du format utilisé partout ailleurs (jj/mm/aaaa).
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user = User.objects.create_superuser("appro-admin", "a@example.com", "pass1234")
        self.client.force_login(user)

        LigneCommandeFournisseur.objects.create(
            commande_fournisseur=self.commande_fournisseur,
            article=self.article,
            commande_ligne_client=self.ligne_client,
            quantite_commandee=20,
            prix_unitaire_achat=3.0,
        )
        response = self.client.get(f"/admin/chiffrage/commande/{self.commande_client.pk}/change/")
        self.assertContains(response, "01/02/2026")
        self.assertNotContains(response, "2026-02-01")


class ArticleFournisseurTests(TestCase):
    def setUp(self):
        self.fournisseur = Tiers.objects.create(
            code="FOUR-ARF-01", raison_sociale="Fournisseur ARF", type_tiers=Tiers.TypeTiers.FOURNISSEUR
        )
        self.autre_fournisseur = Tiers.objects.create(
            code="FOUR-ARF-02", raison_sociale="Autre Fournisseur ARF", type_tiers=Tiers.TypeTiers.FOURNISSEUR
        )
        self.consommable = Article.objects.create(
            reference="CONSO-ARF-01", nature=Article.Nature.CONSOMMABLE, cout_unitaire=5.0
        )
        self.fabrique = Article.objects.create(reference="PIECE-ARF-01", nature=Article.Nature.FABRIQUE)

    def test_article_fabrique_refuse_un_fournisseur(self):
        lien = ArticleFournisseur(article=self.fabrique, fournisseur=self.fournisseur)
        with self.assertRaises(ValidationError):
            lien.full_clean()

    def test_plusieurs_fournisseurs_pour_le_meme_article(self):
        ArticleFournisseur.objects.create(
            article=self.consommable, fournisseur=self.fournisseur, reference_fournisseur="REF-A"
        )
        ArticleFournisseur.objects.create(
            article=self.consommable, fournisseur=self.autre_fournisseur, reference_fournisseur="REF-B"
        )
        self.assertEqual(self.consommable.fournisseurs.count(), 2)

    def test_meme_couple_article_fournisseur_refuse_en_double(self):
        ArticleFournisseur.objects.create(article=self.consommable, fournisseur=self.fournisseur)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ArticleFournisseur.objects.create(article=self.consommable, fournisseur=self.fournisseur)

    def test_tarif_actuel(self):
        lien = ArticleFournisseur.objects.create(article=self.consommable, fournisseur=self.fournisseur)
        self.assertIsNone(lien.tarif_actuel)

        TarifAchatArticle.objects.create(
            article_fournisseur=lien,
            prix_unitaire=4.5,
            date_debut=datetime.date(2025, 1, 1),
            date_fin=datetime.date(2025, 12, 31),
        )
        actuel = TarifAchatArticle.objects.create(
            article_fournisseur=lien, prix_unitaire=4.8, date_debut=datetime.date(2026, 1, 1)
        )
        self.assertEqual(lien.tarif_actuel, actuel)

    def test_frais_port_facultatif(self):
        lien = ArticleFournisseur.objects.create(article=self.consommable, fournisseur=self.fournisseur)
        tarif = TarifAchatArticle.objects.create(
            article_fournisseur=lien, prix_unitaire=4.5, frais_port=12.0, date_debut=datetime.date(2026, 1, 1)
        )
        self.assertEqual(tarif.frais_port, 12.0)

    def test_chevauchement_tarifs_refuse(self):
        lien = ArticleFournisseur.objects.create(article=self.consommable, fournisseur=self.fournisseur)
        TarifAchatArticle.objects.create(
            article_fournisseur=lien,
            prix_unitaire=4.5,
            date_debut=datetime.date(2025, 1, 1),
            date_fin=datetime.date(2025, 12, 31),
        )
        chevauchant = TarifAchatArticle(
            article_fournisseur=lien, prix_unitaire=4.8, date_debut=datetime.date(2025, 6, 1)
        )
        with self.assertRaises(ValidationError):
            chevauchant.full_clean()


class LigneCommandeFournisseurSansArticleTests(TestCase):
    """Une ligne peut porter un poste de gestion seul (charge générale
    sans article — assurance, abonnement...) plutôt qu'un article."""

    def setUp(self):
        self.fournisseur = Tiers.objects.create(
            code="FOUR-POSTE-TEST", raison_sociale="Assureur Test", type_tiers=Tiers.TypeTiers.FOURNISSEUR
        )
        self.commande = CommandeFournisseur.objects.create(
            numero="CF-POSTE-TEST", fournisseur=self.fournisseur, date_commande=datetime.date(2026, 1, 1)
        )
        self.poste = PosteGestion.objects.create(code="ASSUR-TEST", libelle="Assurance test")
        self.article = Article.objects.create(reference="ART-POSTE-TEST", nature=Article.Nature.MATIERE_PREMIERE)

    def test_ni_article_ni_poste_refuse(self):
        ligne = LigneCommandeFournisseur(
            commande_fournisseur=self.commande, quantite_commandee=1, prix_unitaire_achat=100
        )
        with self.assertRaises(ValidationError):
            ligne.full_clean()

    def test_article_et_poste_a_la_fois_refuse(self):
        ligne = LigneCommandeFournisseur(
            commande_fournisseur=self.commande, article=self.article, poste_gestion=self.poste,
            quantite_commandee=1, prix_unitaire_achat=100,
        )
        with self.assertRaises(ValidationError):
            ligne.full_clean()

    def test_ligne_poste_seul_valide(self):
        ligne = LigneCommandeFournisseur.objects.create(
            commande_fournisseur=self.commande, poste_gestion=self.poste,
            designation="Assurance RC Pro — T1 2026", quantite_commandee=1, prix_unitaire_achat=450,
        )
        self.assertIsNone(ligne.article_id)
        self.assertIn("ASSUR-TEST", str(ligne))

    def test_reception_dune_ligne_poste_ne_touche_pas_le_stock(self):
        ligne = LigneCommandeFournisseur.objects.create(
            commande_fournisseur=self.commande, poste_gestion=self.poste,
            designation="Assurance", quantite_commandee=1, prix_unitaire_achat=450,
        )
        reception = Reception.objects.create(
            numero="REC-POSTE-TEST", commande_fournisseur=self.commande, date_reception=datetime.date(2026, 1, 15)
        )
        ReceptionLigne.objects.create(reception=reception, ligne_commande_fournisseur=ligne, quantite_recue=1)

        ligne.refresh_from_db()
        self.assertEqual(ligne.quantite_recue, 1)
        self.assertEqual(MouvementStock.objects.count(), 0)
