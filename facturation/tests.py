import datetime

from django.test import TestCase

from chiffrage.models import Commande, CommandeLigne, Devis
from commercial.models import Adresse, ConditionPaiement, TauxTVA, Tiers
from technique.models import Article

from .models import Facture


class FactureDateEcheanceTests(TestCase):
    def setUp(self):
        self.client_tiers = Tiers.objects.create(
            code="CLI-ECHEANCE", raison_sociale="Client Échéance", type_tiers=Tiers.TypeTiers.CLIENT
        )
        adresse = Adresse.objects.create(
            tiers=self.client_tiers, est_facturation=True,
            adresse="1 rue", code_postal="75000", ville="Paris",
        )
        devis = Devis.objects.create(
            numero="DEV-ECHEANCE", client=self.client_tiers, date_creation=datetime.date(2026, 1, 1)
        )
        self.commande = Commande.objects.create(
            numero="CDE-ECHEANCE", devis=devis, date_commande=datetime.date(2026, 1, 1),
            adresse_facturation=adresse, adresse_livraison=adresse,
        )

    def _facture(self, date_facturation=datetime.date(2026, 1, 1)):
        return Facture.objects.create(
            numero=f"FAC-ECHEANCE-{date_facturation.isoformat()}", commande=self.commande,
            date_facturation=date_facturation,
        )

    def test_echeance_calculee_depuis_les_conditions_du_client(self):
        self.client_tiers.conditions_paiement = ConditionPaiement.objects.create(
            libelle="30 jours net (échéance)", nombre_jours=30
        )
        self.client_tiers.save()
        facture = self._facture()
        self.assertEqual(facture.date_echeance, datetime.date(2026, 1, 31))

    def test_echeance_none_sans_condition_de_paiement(self):
        facture = self._facture()
        self.assertIsNone(facture.date_echeance)

    def test_echeance_none_si_condition_sans_delai_chiffre(self):
        self.client_tiers.conditions_paiement = ConditionPaiement.objects.create(
            libelle="À réception (échéance)"
        )
        self.client_tiers.save()
        facture = self._facture()
        self.assertIsNone(facture.date_echeance)


class FactureMontantsCalculesTests(TestCase):
    """montant_ht_calcule/montant_ttc_calcule : total indicatif recalculé
    depuis les lignes actuelles de la commande — ne remplace jamais
    montant_ht/montant_ttc, saisis à la main depuis la facture réelle
    (Tiime fait foi)."""

    def setUp(self):
        self.client_tiers = Tiers.objects.create(
            code="CLI-MONTANTS-CALC", raison_sociale="Client Montants Calc", type_tiers=Tiers.TypeTiers.CLIENT
        )
        adresse = Adresse.objects.create(
            tiers=self.client_tiers, est_facturation=True,
            adresse="1 rue", code_postal="75000", ville="Paris",
        )
        devis = Devis.objects.create(
            numero="DEV-MONTANTS-CALC", client=self.client_tiers, date_creation=datetime.date(2026, 1, 1)
        )
        self.commande = Commande.objects.create(
            numero="CDE-MONTANTS-CALC", devis=devis, date_commande=datetime.date(2026, 1, 1),
            adresse_facturation=adresse, adresse_livraison=adresse,
        )
        self.article = Article.objects.create(reference="ART-MONTANTS-CALC", nature=Article.Nature.MATIERE_PREMIERE)
        self.taux20 = TauxTVA.objects.create(nom="Taux normal montants calc", taux=20)
        self.facture = Facture.objects.create(
            numero="FAC-MONTANTS-CALC", commande=self.commande, date_facturation=datetime.date(2026, 2, 1)
        )

    def test_none_sans_ligne_chiffree(self):
        self.assertIsNone(self.facture.montant_ht_calcule)
        self.assertIsNone(self.facture.montant_ttc_calcule)

    def test_somme_des_lignes_de_la_commande(self):
        CommandeLigne.objects.create(
            commande=self.commande, article=self.article, quantite_commandee=10,
            prix_vente_unitaire=100, taux_tva=self.taux20,
        )
        CommandeLigne.objects.create(
            commande=self.commande, article=self.article, quantite_commandee=5,
            prix_vente_unitaire=40, taux_tva=self.taux20,
        )
        # HT : 1000 + 200 = 1200 ; TTC : 1200 * 1.2 = 1440
        self.assertAlmostEqual(self.facture.montant_ht_calcule, 1200)
        self.assertAlmostEqual(self.facture.montant_ttc_calcule, 1440)

    def test_ignore_les_lignes_sans_prix(self):
        CommandeLigne.objects.create(
            commande=self.commande, article=self.article, quantite_commandee=10,
            prix_vente_unitaire=100, taux_tva=self.taux20,
        )
        CommandeLigne.objects.create(commande=self.commande, article=self.article, quantite_commandee=3)
        self.assertAlmostEqual(self.facture.montant_ht_calcule, 1000)

    def test_n_ecrase_jamais_montant_ht_saisi_a_la_main(self):
        CommandeLigne.objects.create(
            commande=self.commande, article=self.article, quantite_commandee=10,
            prix_vente_unitaire=100, taux_tva=self.taux20,
        )
        self.facture.montant_ht = 999
        self.facture.montant_ttc = 1111
        self.facture.save()
        self.facture.refresh_from_db()
        self.assertEqual(self.facture.montant_ht, 999)
        self.assertEqual(self.facture.montant_ttc, 1111)
        self.assertAlmostEqual(self.facture.montant_ht_calcule, 1000)


class MontantsCalculesCommandeViewTests(TestCase):
    """Endpoint AJAX utilisé par facturation/facture_admin.js pour
    pré-remplir montant_ht/montant_ttc dès qu'une commande est choisie sur
    le formulaire d'ajout d'une facture."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        self.user = User.objects.create_superuser("montants-admin", "m@example.com", "pass1234")
        self.client.force_login(self.user)

        client_tiers = Tiers.objects.create(
            code="CLI-MONTANTS-VIEW", raison_sociale="Client Montants Vue", type_tiers=Tiers.TypeTiers.CLIENT
        )
        adresse = Adresse.objects.create(
            tiers=client_tiers, est_facturation=True,
            adresse="1 rue", code_postal="75000", ville="Paris",
        )
        devis = Devis.objects.create(
            numero="DEV-MONTANTS-VIEW", client=client_tiers, date_creation=datetime.date(2026, 1, 1)
        )
        self.commande = Commande.objects.create(
            numero="CDE-MONTANTS-VIEW", devis=devis, date_commande=datetime.date(2026, 1, 1),
            adresse_facturation=adresse, adresse_livraison=adresse,
        )
        article = Article.objects.create(reference="ART-MONTANTS-VIEW", nature=Article.Nature.MATIERE_PREMIERE)
        taux20 = TauxTVA.objects.create(nom="Taux normal montants vue", taux=20)
        CommandeLigne.objects.create(
            commande=self.commande, article=article, quantite_commandee=10,
            prix_vente_unitaire=100, taux_tva=taux20,
        )

    def test_montants_calcules_renvoyes(self):
        response = self.client.get(f"/admin/facturation/facture/{self.commande.pk}/montants-calcules/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"montant_ht": 1000, "montant_ttc": 1200})

    def test_commande_inexistante_404(self):
        response = self.client.get("/admin/facturation/facture/INEXISTANTE/montants-calcules/")
        self.assertEqual(response.status_code, 404)

    def test_anonyme_refuse(self):
        self.client.logout()
        response = self.client.get(f"/admin/facturation/facture/{self.commande.pk}/montants-calcules/")
        self.assertNotEqual(response.status_code, 200)
