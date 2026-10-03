import datetime

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings

from chiffrage.models import Commande, CommandeLigne, Devis
from commercial.models import Adresse, TauxTVA, Tiers
from comptabilite.models import (
    ArticleCompteAchat,
    CompteComptable,
    EcritureComptable,
    ParametresComptables,
    PosteGestion,
    TiersCompteComptable,
)
from comptabilite.pcg import importer_pcg
from stock.models import AlerteStock, Emplacement, Lot, MouvementStock
from technique.models import Article

from .generation import GenerationEcritureAchatError, generer_ecriture_achat
from .models import (
    AchatsError,
    ArticleFournisseur,
    CommandeFournisseur,
    FactureFournisseur,
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
            tiers=self.client_tiers, est_facturation=True,
            adresse="1 rue A", code_postal="75000", ville="Paris",
        )
        self.article = Article.objects.create(reference="TOLE-APPRO", nature=Article.Nature.MATIERE_PREMIERE)
        devis = Devis.objects.create(
            numero="DEV-APPRO", client=self.client_tiers, date_creation=datetime.date(2026, 1, 1),
        )
        self.commande_client = Commande.objects.create(
            numero="CDE-APPRO", devis=devis, client=devis.client, date_commande=datetime.date(2026, 1, 1),
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


class LigneCommandeFournisseurMontantsTests(TestCase):
    def setUp(self):
        self.fournisseur = Tiers.objects.create(
            code="FOUR-MONTANTS", raison_sociale="Fournisseur Montants", type_tiers=Tiers.TypeTiers.FOURNISSEUR
        )
        self.commande = CommandeFournisseur.objects.create(
            numero="CF-MONTANTS", fournisseur=self.fournisseur, date_commande=datetime.date(2026, 1, 1)
        )
        self.article = Article.objects.create(reference="ART-MONTANTS", nature=Article.Nature.MATIERE_PREMIERE)
        self.taux20 = TauxTVA.objects.create(nom="Taux normal achats test", taux=20)

    def test_montant_ht_sans_tva(self):
        ligne = LigneCommandeFournisseur.objects.create(
            commande_fournisseur=self.commande, article=self.article, quantite_commandee=10,
            prix_unitaire_achat=5, taux_tva=None,
        )
        self.assertEqual(ligne.montant_ht, 50)
        self.assertEqual(ligne.montant_ttc, 50)

    def test_montant_ttc_avec_tva(self):
        ligne = LigneCommandeFournisseur.objects.create(
            commande_fournisseur=self.commande, article=self.article, quantite_commandee=10,
            prix_unitaire_achat=5, taux_tva=self.taux20,
        )
        self.assertEqual(ligne.montant_ht, 50)
        self.assertAlmostEqual(ligne.montant_ttc, 60)


class FactureFournisseurTests(TestCase):
    def test_creation_et_str(self):
        fournisseur = Tiers.objects.create(
            code="FOUR-FACT-TEST", raison_sociale="Fournisseur Facture Test", type_tiers=Tiers.TypeTiers.FOURNISSEUR
        )
        commande = CommandeFournisseur.objects.create(
            numero="CF-FACT-TEST", fournisseur=fournisseur, date_commande=datetime.date(2026, 1, 1)
        )
        facture = FactureFournisseur.objects.create(
            numero="FACF-TEST-0001", commande_fournisseur=commande, date_facture=datetime.date(2026, 1, 15),
            reference_fournisseur="INV-2026-042",
        )
        self.assertEqual(str(facture), "FACF-TEST-0001")
        self.assertEqual(facture.reference_fournisseur, "INV-2026-042")


class GenererEcritureAchatTests(TestCase):
    def setUp(self):
        importer_pcg()
        self.fournisseur = Tiers.objects.create(
            code="FOUR-COMPTA-TEST", raison_sociale="Fournisseur Compta Test", type_tiers=Tiers.TypeTiers.FOURNISSEUR
        )
        self.article = Article.objects.create(reference="ART-ACHAT-COMPTA-TEST", nature=Article.Nature.MATIERE_PREMIERE)
        self.taux20 = TauxTVA.objects.create(nom="Taux normal achats compta test", taux=20)
        self.taux10 = TauxTVA.objects.create(nom="Taux intermédiaire achats compta test", taux=10)
        self.commande = CommandeFournisseur.objects.create(
            numero="CF-COMPTA-TEST", fournisseur=self.fournisseur, date_commande=datetime.date(2026, 1, 1)
        )
        self.facture = FactureFournisseur.objects.create(
            numero="FACF-COMPTA-TEST", commande_fournisseur=self.commande, date_facture=datetime.date(2026, 2, 1),
        )

    def test_generation_repartit_par_taux_de_tva_et_equilibre(self):
        LigneCommandeFournisseur.objects.create(
            commande_fournisseur=self.commande, article=self.article, quantite_commandee=10,
            prix_unitaire_achat=100, taux_tva=self.taux20,
        )
        LigneCommandeFournisseur.objects.create(
            commande_fournisseur=self.commande, article=self.article, quantite_commandee=5,
            prix_unitaire_achat=40, taux_tva=self.taux10,
        )
        # HT : 1000 (20%) + 200 (10%) = 1200 ; TVA : 200 + 20 = 220 ; TTC : 1420
        ecriture, creee = generer_ecriture_achat(self.facture)
        self.assertTrue(creee)
        self.assertTrue(ecriture.est_equilibree)
        self.assertAlmostEqual(ecriture.total_credit, 1420)

        lignes_fournisseur = ecriture.lignes.filter(compte__code="401")
        self.assertEqual(lignes_fournisseur.count(), 1)
        self.assertAlmostEqual(lignes_fournisseur.first().credit, 1420)

        self.assertAlmostEqual(
            sum(l.debit for l in ecriture.lignes.filter(compte__code="601")), 1200
        )
        self.assertAlmostEqual(
            sum(l.debit for l in ecriture.lignes.filter(compte__code="44566")), 220
        )

    def test_generation_utilise_le_compte_fournisseur_specifique_du_tiers(self):
        compte_fournisseur_special = CompteComptable.objects.get(code="4011")
        TiersCompteComptable.objects.create(tiers=self.fournisseur, compte_fournisseur=compte_fournisseur_special)
        LigneCommandeFournisseur.objects.create(
            commande_fournisseur=self.commande, article=self.article, quantite_commandee=1,
            prix_unitaire_achat=100, taux_tva=self.taux20,
        )
        ecriture, _ = generer_ecriture_achat(self.facture)
        self.assertTrue(ecriture.lignes.filter(compte=compte_fournisseur_special).exists())

    def test_generation_utilise_le_compte_achat_specifique_de_larticle_par_regime_fiscal(self):
        compte_achat_intra = CompteComptable.objects.get(code="601")
        compte_achat_hors_ue = CompteComptable.objects.get(code="602")
        poste = PosteGestion.objects.create(
            code="PG-ACHAT-COMPTA-TEST", libelle="Poste achat compta test",
            compte_achat_intra_ue=compte_achat_intra, compte_achat_hors_ue=compte_achat_hors_ue,
        )
        ArticleCompteAchat.objects.create(article=self.article, poste_gestion=poste)
        self.fournisseur.regime_fiscal = Tiers.RegimeFiscal.INTRA_UE
        self.fournisseur.save()
        LigneCommandeFournisseur.objects.create(
            commande_fournisseur=self.commande, article=self.article, quantite_commandee=1, prix_unitaire_achat=100,
        )
        ecriture, _ = generer_ecriture_achat(self.facture)
        self.assertTrue(ecriture.lignes.filter(compte=compte_achat_intra).exists())

    def test_generation_leve_erreur_si_poste_non_configure_pour_le_regime(self):
        poste = PosteGestion.objects.create(
            code="PG-ACHAT-INCOMPLET", libelle="Poste incomplet",
            compte_achat_france=CompteComptable.objects.get(code="601"),
        )
        ArticleCompteAchat.objects.create(article=self.article, poste_gestion=poste)
        self.fournisseur.regime_fiscal = Tiers.RegimeFiscal.HORS_UE
        self.fournisseur.save()
        LigneCommandeFournisseur.objects.create(
            commande_fournisseur=self.commande, article=self.article, quantite_commandee=1, prix_unitaire_achat=100,
        )
        with self.assertRaises(GenerationEcritureAchatError):
            generer_ecriture_achat(self.facture)

    def test_generation_ligne_poste_de_gestion_sans_article(self):
        poste = PosteGestion.objects.create(
            code="PG-CHARGE-CPTA", libelle="Charge générale compta test",
            compte_achat_france=CompteComptable.objects.get(code="616"),
        )
        LigneCommandeFournisseur.objects.create(
            commande_fournisseur=self.commande, poste_gestion=poste, designation="Assurance",
            quantite_commandee=1, prix_unitaire_achat=300,
        )
        ecriture, _ = generer_ecriture_achat(self.facture)
        self.assertTrue(ecriture.lignes.filter(compte__code="616").exists())

    def test_generation_idempotente(self):
        LigneCommandeFournisseur.objects.create(
            commande_fournisseur=self.commande, article=self.article, quantite_commandee=1, prix_unitaire_achat=100,
        )
        ecriture1, creee1 = generer_ecriture_achat(self.facture)
        ecriture2, creee2 = generer_ecriture_achat(self.facture)
        self.assertTrue(creee1)
        self.assertFalse(creee2)
        self.assertEqual(ecriture1.pk, ecriture2.pk)
        self.assertEqual(EcritureComptable.objects.filter(facture_fournisseur=self.facture).count(), 1)

    def test_generation_sans_lignes_utilise_les_montants_globaux(self):
        self.facture.montant_ht = 500
        self.facture.montant_ttc = 600
        self.facture.save()
        ecriture, creee = generer_ecriture_achat(self.facture)
        self.assertTrue(creee)
        self.assertAlmostEqual(ecriture.total_debit, 600)
        self.assertAlmostEqual(ecriture.total_credit, 600)

    def test_generation_sans_lignes_ni_montants_leve_erreur(self):
        with self.assertRaises(GenerationEcritureAchatError):
            generer_ecriture_achat(self.facture)

    def test_generation_sans_parametres_configures_leve_erreur(self):
        ParametresComptables.objects.filter(pk=1).update(compte_achat_defaut=None)
        CompteComptable.objects.filter(code="601").delete()
        LigneCommandeFournisseur.objects.create(
            commande_fournisseur=self.commande, article=self.article, quantite_commandee=1, prix_unitaire_achat=100,
        )
        with self.assertRaises(GenerationEcritureAchatError):
            generer_ecriture_achat(self.facture)


class ReceptionTracabiliteTests(TestCase):
    """Gap analysis : réception sur plusieurs lots, n° de coulée et certificat matière."""

    def setUp(self):
        ReceptionTests.setUp(self)  # même préparation, sans rejouer les tests de ReceptionTests

    def _reception(self, quantite=10, **kw):
        return ReceptionLigne.objects.create(
            reception=self.reception, ligne_commande_fournisseur=self.ligne, quantite_recue=quantite, **kw
        )

    def test_premiere_reception_cree_le_lot_de_la_coulee(self):
        emplacement = Emplacement.objects.create(code="TRC-A1")
        self._reception(40, numero_coulee="C-7781", emplacement=emplacement)
        lot = Lot.objects.get(article=self.article)
        self.assertEqual((lot.numero_coulee, lot.emplacement, lot.quantite), ("C-7781", emplacement, 40))
        mouvement = lot.mouvements.get()
        self.assertEqual((mouvement.reference_origine, mouvement.cout_unitaire), ("RECEPTION-REC-001", 2.0))

    def test_meme_coulee_complete_son_lot_et_nouvelle_coulee_cree_un_autre(self):
        emplacement = Emplacement.objects.create(code="TRC-A2")
        self._reception(10, numero_coulee="C-1", emplacement=emplacement)
        self._reception(15, numero_coulee="C-1")  # sans emplacement : retrouvé par la coulée
        self._reception(20, numero_coulee="C-2", emplacement=emplacement)
        quantites = {l.numero_coulee: l.quantite for l in Lot.objects.filter(article=self.article)}
        self.assertEqual(quantites, {"C-1": 25, "C-2": 20})

    def test_lot_choisi_explicitement(self):
        emplacement = Emplacement.objects.create(code="TRC-A3")
        lot1 = Lot.objects.create(article=self.article, emplacement=emplacement, numero_coulee="L1")
        Lot.objects.create(article=self.article, emplacement=Emplacement.objects.create(code="TRC-A4"), numero_coulee="L2")
        self._reception(30, lot=lot1)  # deux lots existent : le choix lève l'ambiguïté
        lot1.refresh_from_db()
        self.assertEqual(lot1.quantite, 30)

    def test_lot_dun_autre_article_refuse_sans_rien_enregistrer(self):
        autre = Article.objects.create(reference="AUTRE-ART", nature=Article.Nature.MATIERE_PREMIERE)
        lot_autre = Lot.objects.create(article=autre, emplacement=Emplacement.objects.create(code="TRC-A5"))
        with self.assertRaises(AchatsError):
            self._reception(10, lot=lot_autre)
        self.assertEqual(ReceptionLigne.objects.count(), 0)

    def test_echec_de_resolution_du_lot_nenregistre_rien(self):
        """Avant : la ligne et le cumul reçu restaient enregistrés alors que le lot était introuvable."""
        with self.assertRaises(AchatsError):
            self._reception(10, numero_coulee="C-9")  # nouvelle coulée sans emplacement
        self.assertEqual(ReceptionLigne.objects.count(), 0)
        self.ligne.refresh_from_db()
        self.assertEqual(self.ligne.quantite_recue, 0)
        with self.assertRaises(AchatsError):
            self._reception(10)  # aucun lot du tout
        self.ligne.refresh_from_db()
        self.assertEqual((self.ligne.quantite_recue, ReceptionLigne.objects.count()), (0, 0))

    def test_ambiguite_entre_plusieurs_lots_sans_choix(self):
        for i in range(2):
            Lot.objects.create(article=self.article, emplacement=Emplacement.objects.create(code=f"TRC-B{i}"))
        with self.assertRaises(AchatsError) as cm:
            self._reception(10)
        self.assertIn("choisissez le lot de destination", str(cm.exception))
        self.assertEqual(ReceptionLigne.objects.count(), 0)

    def test_article_non_gere_en_stock_refuse(self):
        Article.objects.filter(pk=self.article.pk).update(gere_en_stock=False)
        self.ligne.refresh_from_db()
        self.ligne.article.refresh_from_db()
        with self.assertRaises(AchatsError):
            self._reception(10, emplacement=Emplacement.objects.create(code="TRC-C1"))

    def test_certificat_rattache_au_lot_cree(self):
        import tempfile

        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import override_settings

        with tempfile.TemporaryDirectory() as dossier, override_settings(MEDIA_ROOT=dossier):
            emplacement = Emplacement.objects.create(code="TRC-D1")
            self._reception(
                10, numero_coulee="C-CERT", emplacement=emplacement,
                certificat=SimpleUploadedFile("cert.pdf", b"%PDF-1.4 certificat 3.1"),
            )
            lot = Lot.objects.get(numero_coulee="C-CERT")
            self.assertTrue(lot.certificat.name.startswith("stock/certificats/"))
            self.assertEqual(lot.certificat.read(), b"%PDF-1.4 certificat 3.1")


class TracabiliteLivraisonTests(TestCase):
    """Livraison d'une coulée précise, bon de livraison et page de traçabilité."""

    def setUp(self):
        ReceptionTests.setUp(self)
        import datetime as dt

        from django.contrib.auth import get_user_model

        from chiffrage.models import Commande, CommandeLigne, Livraison, LivraisonLigne

        self.user = get_user_model().objects.create_superuser("trace-admin", "t@example.com", "pass-mot-de-passe-4")
        self.client.force_login(self.user)
        self.emp = Emplacement.objects.create(code="TRL-A1")
        for coulee, qte in (("C-OLD", 10), ("C-NEW", 10)):
            ReceptionLigne.objects.create(
                reception=self.reception, ligne_commande_fournisseur=self.ligne, quantite_recue=qte,
                numero_coulee=coulee, emplacement=self.emp,
            )
        self.lot_old = Lot.objects.get(numero_coulee="C-OLD")
        self.lot_new = Lot.objects.get(numero_coulee="C-NEW")
        client = Tiers.objects.create(code="CLI-TRL", raison_sociale="Client Traçabilité", type_tiers=Tiers.TypeTiers.CLIENT)
        adresse = Adresse.objects.create(tiers=client, est_facturation=True, est_livraison=True, adresse="1 rue", code_postal="75000", ville="Paris")
        self.commande_client = Commande.objects.create(
            numero="CDE-TRL", client=client, reference_client="PO", date_commande=dt.date(2026, 1, 1),
            adresse_facturation=adresse, adresse_livraison=adresse,
        )
        self.cl = CommandeLigne.objects.create(commande=self.commande_client, article=self.article, quantite_commandee=12, prix_vente_unitaire=5)
        self.livraison = Livraison.objects.create(numero="BL-TRL", commande=self.commande_client, date_livraison=dt.date(2026, 2, 1))
        self.LivraisonLigne = LivraisonLigne

    def test_livraison_dune_coulee_precise(self):
        self.LivraisonLigne.objects.create(livraison=self.livraison, commande_ligne=self.cl, quantite_livree=4, lot=self.lot_new)
        self.lot_old.refresh_from_db()
        self.lot_new.refresh_from_db()
        self.assertEqual((self.lot_old.quantite, self.lot_new.quantite), (10, 6))  # pas de FIFO : la coulée voulue

    def test_sans_choix_le_fifo_sapplique(self):
        self.LivraisonLigne.objects.create(livraison=self.livraison, commande_ligne=self.cl, quantite_livree=4)
        self.lot_old.refresh_from_db()
        self.assertEqual(self.lot_old.quantite, 6)

    def test_coulee_insuffisante_refuse_toute_la_ligne(self):
        from chiffrage.models import LivraisonError

        with self.assertRaises(LivraisonError):
            self.LivraisonLigne.objects.create(livraison=self.livraison, commande_ligne=self.cl, quantite_livree=11, lot=self.lot_new)
        self.assertEqual(self.LivraisonLigne.objects.count(), 0)
        self.lot_new.refresh_from_db()
        self.assertEqual(self.lot_new.quantite, 10)

    def test_lot_dun_autre_article_invalide(self):
        from django.core.exceptions import ValidationError

        autre = Article.objects.create(reference="AUTRE-TRL", nature=Article.Nature.MATIERE_PREMIERE)
        lot = Lot.objects.create(article=autre, emplacement=self.emp)
        ligne = self.LivraisonLigne(livraison=self.livraison, commande_ligne=self.cl, quantite_livree=1, lot=lot)
        with self.assertRaises(ValidationError) as cm:
            ligne.full_clean()
        self.assertIn("lot", cm.exception.message_dict)

    def test_le_bon_de_livraison_imprime_la_coulee(self):
        from chiffrage.documents import generer_pdf_bon_livraison

        self.LivraisonLigne.objects.create(livraison=self.livraison, commande_ligne=self.cl, quantite_livree=4, lot=self.lot_new)
        texte = generer_pdf_bon_livraison(self.livraison).decode("latin-1")
        self.assertIn("C-NEW", texte)
        self.assertNotIn("C-OLD", texte)

    def test_page_de_tracabilite_du_lot(self):
        self.LivraisonLigne.objects.create(livraison=self.livraison, commande_ligne=self.cl, quantite_livree=4, lot=self.lot_new)
        page = self.client.get(f"/admin/stock/lot/{self.lot_new.pk}/tracabilite/")
        self.assertEqual(page.status_code, 200)
        for attendu in ("C-NEW", "REC-001", "Fournisseur Test", "BL-TRL", "Client Traçabilité", "CDE-TRL"):
            self.assertContains(page, attendu)
        autre = self.client.get(f"/admin/stock/lot/{self.lot_old.pk}/tracabilite/")
        self.assertNotContains(autre, "BL-TRL")  # l'autre coulée n'est jamais partie

    def test_tracabilite_signale_une_livraison_annulee(self):
        self.LivraisonLigne.objects.create(livraison=self.livraison, commande_ligne=self.cl, quantite_livree=4, lot=self.lot_new)
        self.livraison.annuler(utilisateur=self.user, motif="Erreur")
        page = self.client.get(f"/admin/stock/lot/{self.lot_new.pk}/tracabilite/")
        self.assertContains(page, "(annulée)")

    def test_recherche_de_lot_par_numero_de_coulee(self):
        page = self.client.get("/admin/stock/lot/?q=C-NEW")
        self.assertContains(page, "C-NEW")
        self.assertNotContains(page, "C-OLD")

    def test_certificat_servi_aux_habilites_seulement(self):
        import tempfile

        from django.contrib.auth import get_user_model
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import override_settings

        with tempfile.TemporaryDirectory() as dossier, override_settings(MEDIA_ROOT=dossier):
            self.lot_new.certificat.save("cert.pdf", SimpleUploadedFile("cert.pdf", b"CERT"), save=True)
            url = self.lot_new.certificat.url
            self.assertEqual(self.client.get(url).status_code, 200)
            self.client.force_login(get_user_model().objects.create_user("sans-droit-cert", "c@example.com", "pass-mot-de-passe-4", is_staff=True))
            self.assertEqual(self.client.get(url).status_code, 403)
            self.client.logout()
            self.assertEqual(self.client.get(url).status_code, 302)


class StockOptionnelTests(TestCase):
    """La gestion de stock est facultative : par article, et globalement (DJANGO_STOCK_ACTIF)."""

    def setUp(self):
        ReceptionTests.setUp(self)
        from django.contrib.auth import get_user_model

        self.admin = get_user_model().objects.create_superuser("sans-stock", "s@example.com", "pass-mot-de-passe-5")

    def _recevoir(self, quantite=50, **kw):
        return ReceptionLigne.objects.create(
            reception=self.reception, ligne_commande_fournisseur=self.ligne, quantite_recue=quantite, **kw
        )

    def test_article_non_gere_en_stock_se_recoit_sans_lot(self):
        self.article.gere_en_stock = False
        self.article.save()
        self._recevoir()
        self.ligne.refresh_from_db()
        self.assertEqual(self.ligne.quantite_recue, 50)
        self.assertEqual(MouvementStock.objects.count(), 0)

    def test_lot_designe_pour_un_article_non_gere_est_refuse(self):
        self.article.gere_en_stock = False
        self.article.save()
        emp = Emplacement.objects.create(code="SANS-A1")
        with self.assertRaises(AchatsError):
            self._recevoir(numero_coulee="C-1", emplacement=emp)

    @override_settings(STOCK_ACTIF=False)
    def test_sans_gestion_de_stock_la_reception_ne_touche_pas_au_stock(self):
        # Même un article « géré en stock » (donnée héritée) et sans lot : on ne note que la quantité reçue.
        self.assertTrue(self.article.gere_en_stock)
        self._recevoir()
        self.ligne.refresh_from_db()
        self.assertEqual(self.ligne.quantite_recue, 50)
        self.assertEqual(MouvementStock.objects.count(), 0)

    @override_settings(STOCK_ACTIF=False)
    def test_sans_gestion_de_stock_un_nouvel_article_n_est_pas_gere_en_stock(self):
        neuf = Article.objects.create(reference="MP-SANS-STOCK", nature=Article.Nature.MATIERE_PREMIERE)
        self.assertFalse(neuf.gere_en_stock)

    @override_settings(STOCK_ACTIF=False)
    def test_sans_gestion_de_stock_la_livraison_ne_consomme_aucun_lot(self):
        import datetime as dt

        from chiffrage.models import Commande, CommandeLigne, Livraison, LivraisonLigne

        emp = Emplacement.objects.create(code="SANS-B1")
        lot = Lot.objects.create(article=self.article, emplacement=emp, quantite=10)
        client = Tiers.objects.create(code="CLI-SS", raison_sociale="Client", type_tiers=Tiers.TypeTiers.CLIENT)
        adr = Adresse.objects.create(tiers=client, est_facturation=True, est_livraison=True, adresse="1 rue", code_postal="75000", ville="Paris")
        cde = Commande.objects.create(
            numero="CDE-SS", client=client, reference_client="PO", date_commande=dt.date(2026, 1, 1),
            adresse_facturation=adr, adresse_livraison=adr,
        )
        cl = CommandeLigne.objects.create(commande=cde, article=self.article, quantite_commandee=5, prix_vente_unitaire=5)
        bl = Livraison.objects.create(numero="BL-SS", commande=cde, date_livraison=dt.date(2026, 2, 1))
        LivraisonLigne.objects.create(livraison=bl, commande_ligne=cl, quantite_livree=3)
        lot.refresh_from_db()
        cl.refresh_from_db()
        self.assertEqual(lot.quantite, 10)  # stock intact
        self.assertEqual(cl.quantite_livree, 3)  # mais la livraison est bien enregistrée

    @override_settings(STOCK_ACTIF=False)
    def test_ecrans_de_stock_masques(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get("/admin/stock/lot/").status_code, 403)
        # Le menu latéral est construit au démarrage selon DJANGO_STOCK_ACTIF (config/settings.py).

    @override_settings(STOCK_ACTIF=False)
    def test_champs_de_stock_masques_sur_l_article_et_la_reception(self):
        self.client.force_login(self.admin)
        fiche = self.client.get(f"/admin/technique/article/{self.article.pk}/change/").content.decode()
        self.assertNotIn('name="gere_en_stock"', fiche)
        self.assertNotIn('name="stock_mini"', fiche)
        reception = self.client.get(f"/admin/achats/reception/{self.reception.pk}/change/").content.decode()
        self.assertNotIn("numero_coulee", reception)

    def test_avec_gestion_de_stock_tout_reste_visible(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get("/admin/stock/lot/").status_code, 200)
        fiche = self.client.get(f"/admin/technique/article/{self.article.pk}/change/").content.decode()
        self.assertIn('name="gere_en_stock"', fiche)

    @override_settings(STOCK_ACTIF=False)
    def test_synthese_et_tuile_sans_alertes_de_stock(self):
        from comptes.dashboard import dashboard_callback
        from comptes.synthese import construire

        titres = [k["title"] for k in dashboard_callback(request=None, context={})["kpis"]]
        self.assertNotIn("Alertes de stock", titres)
        self.assertNotIn("Alertes de stock actives", construire())
