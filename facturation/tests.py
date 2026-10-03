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
            numero="CDE-ECHEANCE", devis=devis, client=devis.client, date_commande=datetime.date(2026, 1, 1),
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
            numero="CDE-MONTANTS-CALC", devis=devis, client=devis.client, date_commande=datetime.date(2026, 1, 1),
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
            numero="CDE-MONTANTS-VIEW", devis=devis, client=devis.client, date_commande=datetime.date(2026, 1, 1),
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


import json

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from comptabilite.models import EcritureComptable, JournalComptable

from .models import FactureVerrouilleeError, facture_verrouillee


class _FixtureFacturation:
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("fact-admin", "f@example.com", "pass1234")
        self.client.force_login(self.user)
        self.tiers = Tiers.objects.create(code="CLI-FACT", raison_sociale="Client Facture", type_tiers=Tiers.TypeTiers.CLIENT)
        self.adresse = Adresse.objects.create(
            tiers=self.tiers, est_facturation=True, est_livraison=True, adresse="1 rue", code_postal="75000", ville="Paris"
        )
        self.tva = TauxTVA.objects.create(nom="N20 facture", taux=20)
        self.article = Article.objects.create(
            reference="ART-FACT", nature=Article.Nature.MATIERE_PREMIERE, unite_cout=Article.UniteCout.PIECE, cout_unitaire=1
        )
        # Commande créée DIRECTEMENT, sans devis (cas autorisé).
        self.commande = Commande.objects.create(
            numero="CDE-FACT", client=self.tiers, reference_client="PO", date_commande=datetime.date(2026, 1, 1),
            adresse_facturation=self.adresse, adresse_livraison=self.adresse,
        )
        self.ligne = CommandeLigne.objects.create(
            commande=self.commande, article=self.article, quantite_commandee=10, prix_vente_unitaire=10, taux_tva=self.tva
        )

    def _facture(self, numero="FAC-1", **kw):
        kw.setdefault("date_facturation", datetime.date(2026, 2, 1))
        kw.setdefault("montant_ht", 100)
        kw.setdefault("montant_ttc", 120)
        return Facture.objects.create(numero=numero, commande=self.commande, **kw)

    def _patch(self, url, donnees):
        return self.client.patch(url, data=json.dumps(donnees), content_type="application/json")


class CommandeDirecteTests(_FixtureFacturation, TestCase):
    """Une commande sans devis doit pouvoir être facturée (échéance et écriture)."""

    def test_echeance_sans_devis(self):
        facture = self._facture()
        self.assertIsNone(facture.date_echeance)  # pas de conditions de paiement, mais pas de plantage

    def test_ecriture_comptable_sans_devis(self):
        from comptabilite.generation import generer_ecriture_facture
        from comptabilite.pcg import importer_pcg

        importer_pcg()
        ecriture, creee = generer_ecriture_facture(self._facture())
        self.assertTrue(creee)
        self.assertTrue(ecriture.est_equilibree)
        self.assertAlmostEqual(ecriture.total_debit, 120)


class VerrouFactureTests(_FixtureFacturation, TestCase):
    """C-AD-01 / C-AD-02 : une facture émise ou comptabilisée ne se réécrit pas."""

    def _emettre(self, facture):
        facture.reference_tiime = "TIIME-001"
        facture.save()

    def test_brouillon_libre(self):
        facture = self._facture()
        self.assertFalse(facture_verrouillee(facture))
        facture.montant_ht = 150
        facture.montant_ttc = 180
        facture.save()
        facture.delete()
        self.assertFalse(Facture.objects.exists())

    def test_emission_et_correction_dans_la_meme_saisie_autorisee(self):
        facture = self._facture()
        facture.montant_ht, facture.montant_ttc, facture.reference_tiime = 110, 132, "TIIME-002"
        facture.save()
        self.assertTrue(facture_verrouillee(facture))

    def test_facture_emise_refuse_modification_et_suppression(self):
        facture = self._facture()
        self._emettre(facture)
        facture.montant_ht = 999
        with self.assertRaises(FactureVerrouilleeError):
            facture.save()
        with self.assertRaises(ValidationError):
            facture.full_clean()
        facture.refresh_from_db()
        with self.assertRaises(FactureVerrouilleeError):
            facture.delete()
        self.assertEqual(facture.montant_ht, 100)

    def test_le_paiement_reste_modifiable_apres_emission(self):
        facture = self._facture()
        self._emettre(facture)
        facture.statut_paiement = Facture.StatutPaiement.PAYE
        facture.save()
        facture.refresh_from_db()
        self.assertEqual(facture.statut_paiement, "paye")
        self.assertEqual(facture.date_paiement, datetime.date.today())

    def test_facture_comptabilisee_est_verrouillee_meme_sans_reference(self):
        facture = self._facture()
        journal = JournalComptable.objects.get(code="OD")
        EcritureComptable.objects.create(journal=journal, date_ecriture=facture.date_facturation, piece=facture.numero, libelle="x", facture=facture)
        self.assertTrue(facture_verrouillee(facture))
        with self.assertRaises(FactureVerrouilleeError):
            facture.delete()

    def test_admin_champs_en_lecture_seule_et_pas_de_suppression(self):
        facture = self._facture()
        self._emettre(facture)
        page = self.client.get(f"/admin/facturation/facture/{facture.pk}/change/")
        self.assertEqual(page.status_code, 200)
        for champ in ("montant_ht", "montant_ttc", "date_facturation", "reference_tiime"):
            self.assertNotContains(page, f'name="{champ}"')
        self.assertContains(page, 'name="statut_paiement"')
        self.assertEqual(self.client.post(f"/admin/facturation/facture/{facture.pk}/delete/", {"post": "yes"}).status_code, 403)
        self.assertTrue(Facture.objects.filter(pk=facture.pk).exists())

    def test_api_refuse_de_modifier_ou_supprimer_une_facture_emise(self):
        facture = self._facture()
        self._emettre(facture)
        url = f"/api/v1/factures/{facture.pk}/"
        self.assertEqual(self._patch(url, {"montant_ht": 1}).status_code, 400)
        self.assertEqual(self._patch(url, {"statut_paiement": "paye"}).status_code, 200)
        self.assertEqual(self.client.delete(url).status_code, 400)
        facture.refresh_from_db()
        self.assertEqual(facture.montant_ht, 100)

    def test_api_ne_remplace_jamais_une_facture_existante(self):
        self._facture()
        r = self.client.post(
            "/api/v1/factures/",
            data=json.dumps({"numero": "FAC-1", "commande": self.commande.pk, "date_facturation": "2026-03-01", "montant_ht": 1, "montant_ttc": 1}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 400, r.content)
        self.assertEqual(Facture.objects.get(pk="FAC-1").montant_ht, 100)

    def test_historique_de_la_facture(self):
        facture = self._facture()
        facture.statut_paiement = Facture.StatutPaiement.PARTIEL
        facture.save()
        derniere, precedente = facture.history.all()[0], facture.history.all()[1]
        changements = {c.field: (c.old, c.new) for c in derniere.diff_against(precedente).changes}
        self.assertEqual(changements["statut_paiement"], ("a_payer", "partiel"))


class ValidationFactureTests(_FixtureFacturation, TestCase):
    """C-NV-01/02 : dates et montants cohérents."""

    def _erreurs(self, **kw):
        facture = Facture(numero="FAC-V", commande=self.commande, **{"date_facturation": datetime.date(2026, 2, 1), **kw})
        with self.assertRaises(ValidationError) as cm:
            facture.full_clean()
        return cm.exception.message_dict

    def test_facture_anterieure_a_la_commande(self):
        self.assertIn("date_facturation", self._erreurs(date_facturation=datetime.date(2025, 12, 31)))

    def test_facture_dans_le_futur(self):
        futur = datetime.date.today() + datetime.timedelta(days=3)
        self.assertIn("date_facturation", self._erreurs(date_facturation=futur))

    def test_ttc_inferieur_au_ht_et_montants_negatifs(self):
        self.assertIn("montant_ttc", self._erreurs(montant_ht=100, montant_ttc=90))
        erreurs = self._erreurs(montant_ht=-5, montant_ttc=-5)
        self.assertIn("montant_ht", erreurs)

    def test_paiement_avant_la_facture(self):
        self.assertIn("date_paiement", self._erreurs(date_paiement=datetime.date(2026, 1, 15)))

    def test_montants_arrondis_au_centime_a_lenregistrement(self):
        facture = self._facture(montant_ht=10.004, montant_ttc=12.0049)
        facture.refresh_from_db()
        self.assertEqual((facture.montant_ht, facture.montant_ttc), (10.0, 12.0))


class MigrationStatutPaiementTests(TestCase):
    def test_normalisation_des_anciens_textes_libres(self):
        import importlib

        from django.apps import apps

        migration = importlib.import_module("facturation.migrations.0003_verrou_paiement_historique")
        cas = {"Payé": "paye", "payée le 12/03": "paye", "Partiellement payée": "partiel", "Impayé": "a_payer",
               "non payé": "a_payer", "": "a_payer", "en attente": "a_payer", "Réglé": "paye"}
        valeurs = {texte: migration.normaliser_vers(texte) for texte in cas}
        self.assertEqual(valeurs, cas)
