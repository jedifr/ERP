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


from chiffrage.models import Livraison, LivraisonLigne

from .models import FactureLigne
from .services import FacturationError, creer_avoir, lignes_a_facturer, preparer_facture, reste_a_facturer


class _FixtureLignes(_FixtureFacturation):
    """Commande de 10 pièces à 10 € HT (TVA 20 %), dont 6 livrées."""

    def setUp(self):
        super().setUp()
        self._livrer(6)

    def _livrer(self, quantite, numero=None):
        numero = numero or f"BL-F-{Livraison.objects.count() + 1}"
        livraison = Livraison.objects.create(numero=numero, commande=self.commande, date_livraison=datetime.date(2026, 1, 20))
        LivraisonLigne.objects.create(livraison=livraison, commande_ligne=self.ligne, quantite_livree=quantite)
        self.ligne.refresh_from_db()
        return livraison

    def _facturer(self, quantite, numero="FAC-L1", **kw):
        facture = Facture.objects.create(
            numero=numero, commande=self.commande, date_facturation=datetime.date(2026, 2, 1), **kw
        )
        ligne = FactureLigne(facture=facture, commande_ligne=self.ligne, quantite=quantite)
        ligne.full_clean()
        ligne.save()
        return facture


class LignesDeFactureTests(_FixtureLignes, TestCase):
    """C-OP-05 / C-OP-06 : on ne facture ni plus que livré, ni deux fois la même chose."""

    def test_facturation_du_livre_accepte_et_montants_deduits_des_lignes(self):
        facture = self._facturer(6)
        facture.remplir_montants_depuis_les_lignes()
        facture.refresh_from_db()
        self.assertEqual((facture.montant_ht, facture.montant_ttc), (60.0, 72.0))

    def test_facturer_plus_que_le_livre_est_refuse(self):
        ligne = FactureLigne(
            facture=Facture.objects.create(numero="FAC-X", commande=self.commande, date_facturation=datetime.date(2026, 2, 1)),
            commande_ligne=self.ligne, quantite=7,
        )
        with self.assertRaises(ValidationError) as cm:
            ligne.full_clean()
        self.assertIn("quantite", cm.exception.message_dict)
        self.assertIn("il reste 6 à facturer", cm.exception.message_dict["quantite"][0])

    def test_deux_factures_cumulees_ne_depassent_pas_le_livre(self):
        self._facturer(4, "FAC-A")
        self.assertEqual(self.ligne.quantite_facturee, 4)
        self.assertEqual(self.ligne.reste_a_facturer, 2)
        facture_b = Facture.objects.create(numero="FAC-B", commande=self.commande, date_facturation=datetime.date(2026, 2, 2))
        with self.assertRaises(ValidationError):
            FactureLigne(facture=facture_b, commande_ligne=self.ligne, quantite=3).full_clean()
        FactureLigne(facture=facture_b, commande_ligne=self.ligne, quantite=2).full_clean()

    def test_une_livraison_supplementaire_libere_de_la_capacite(self):
        self._facturer(6, "FAC-A")
        self.assertEqual(reste_a_facturer(self.ligne), 0)
        self._livrer(3)
        self.assertEqual(reste_a_facturer(self.ligne), 3)

    def test_facturation_anticipee_limitee_au_commande(self):
        facture = Facture.objects.create(
            numero="FAC-ANT", commande=self.commande, date_facturation=datetime.date(2026, 2, 1), anticipee=True
        )
        FactureLigne(facture=facture, commande_ligne=self.ligne, quantite=10).full_clean()
        with self.assertRaises(ValidationError):
            FactureLigne(facture=facture, commande_ligne=self.ligne, quantite=11).full_clean()

    def test_ligne_dune_autre_commande_refusee(self):
        autre = Commande.objects.create(
            numero="CDE-AUTRE", client=self.tiers, reference_client="X", date_commande=datetime.date(2026, 1, 1),
            adresse_facturation=self.adresse, adresse_livraison=self.adresse,
        )
        ligne_autre = CommandeLigne.objects.create(commande=autre, article=self.article, quantite_commandee=5, prix_vente_unitaire=1)
        facture = Facture.objects.create(numero="FAC-Y", commande=self.commande, date_facturation=datetime.date(2026, 2, 1))
        with self.assertRaises(ValidationError) as cm:
            FactureLigne(facture=facture, commande_ligne=ligne_autre, quantite=1).full_clean()
        self.assertIn("commande_ligne", cm.exception.message_dict)

    def test_prix_et_tva_figes_sur_la_ligne(self):
        facture = self._facturer(2)
        self.ligne.prix_vente_unitaire = 99
        self.ligne.save()
        ligne = facture.lignes.get()
        self.assertEqual((ligne.prix_unitaire_ht, ligne.taux_tva, ligne.montant_ht), (10.0, 20.0, 20.0))

    def test_lignes_figees_une_fois_la_facture_emise(self):
        facture = self._facturer(2)
        facture.reference_tiime = "TIIME-L"
        facture.save()
        ligne = facture.lignes.get()
        ligne.quantite = 3
        with self.assertRaises(FactureVerrouilleeError):
            ligne.save()
        with self.assertRaises(FactureVerrouilleeError):
            ligne.delete()
        with self.assertRaises(ValidationError):
            FactureLigne(facture=facture, commande_ligne=self.ligne, quantite=1).full_clean()

    def test_montants_saisis_jamais_ecrases(self):
        facture = self._facturer(6, montant_ht=55, montant_ttc=66)
        self.assertFalse(facture.remplir_montants_depuis_les_lignes())
        facture.refresh_from_db()
        self.assertEqual(facture.montant_ht, 55)

    def test_api_lignes_refuse_le_depassement(self):
        facture = Facture.objects.create(numero="FAC-API", commande=self.commande, date_facturation=datetime.date(2026, 2, 1))
        r = self.client.post(
            "/api/v1/facture-lignes/",
            data=json.dumps({"facture": facture.pk, "commande_ligne": self.ligne.pk, "quantite": 9}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 400, r.content)
        r = self.client.post(
            "/api/v1/facture-lignes/",
            data=json.dumps({"facture": facture.pk, "commande_ligne": self.ligne.pk, "quantite": 6}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["montant_ht"], 60.0)


class AvoirsTests(_FixtureLignes, TestCase):
    """C-OP-04 : correction d'une facture émise par un avoir lié, jamais par modification."""

    def _facture_emise(self, quantite=6, numero="FAC-E"):
        facture = self._facturer(quantite, numero)
        facture.remplir_montants_depuis_les_lignes()
        facture.reference_tiime = f"TIIME-{numero}"
        facture.save()
        return facture

    def test_avoir_total_negatif_lie_a_la_facture(self):
        facture = self._facture_emise()
        avoir = creer_avoir(facture, "Marchandise non conforme", utilisateur=self.user)
        self.assertEqual(avoir.type_document, "avoir")
        self.assertEqual(avoir.facture_origine, facture)
        self.assertEqual((avoir.montant_ht, avoir.montant_ttc), (-60.0, -72.0))
        self.assertEqual(avoir.motif, "Marchandise non conforme")
        self.assertEqual(avoir.numero, "AV-FAC-E")
        avoir.full_clean()

    def test_avoir_rouvre_le_reste_a_facturer(self):
        facture = self._facture_emise()
        self.assertEqual(reste_a_facturer(self.ligne), 0)
        creer_avoir(facture, "Erreur de prix")
        self.assertEqual(self.ligne.quantite_facturee, 0)
        self.assertEqual(reste_a_facturer(self.ligne), 6)

    def test_avoir_partiel_puis_solde_puis_plus_rien(self):
        facture = self._facture_emise()
        partiel = Facture.objects.create(
            numero="AV-PARTIEL", commande=self.commande, type_document="avoir", facture_origine=facture,
            motif="Remise", date_facturation=datetime.date(2026, 2, 3),
        )
        ligne = FactureLigne(facture=partiel, commande_ligne=self.ligne, quantite=2)
        ligne.full_clean()
        ligne.save()
        solde = creer_avoir(facture, "Solde")
        self.assertEqual(solde.lignes.get().quantite, 4)
        with self.assertRaises(FacturationError):
            creer_avoir(facture, "Encore")

    def test_avoir_ne_depasse_pas_la_facture(self):
        facture = self._facture_emise()
        avoir = Facture.objects.create(
            numero="AV-TROP", commande=self.commande, type_document="avoir", facture_origine=facture,
            motif="x", date_facturation=datetime.date(2026, 2, 3),
        )
        with self.assertRaises(ValidationError):
            FactureLigne(facture=avoir, commande_ligne=self.ligne, quantite=7).full_clean()

    def test_avoir_refuse_sur_facture_non_emise_ou_sur_un_avoir(self):
        brouillon = self._facturer(2, "FAC-BR")
        with self.assertRaises(FacturationError):
            creer_avoir(brouillon, "x")
        avoir = creer_avoir(self._facture_emise(quantite=4), "x")
        avoir.reference_tiime = "TIIME-AV"
        avoir.save()
        with self.assertRaises(FacturationError):
            creer_avoir(avoir, "x")

    def test_motif_obligatoire(self):
        with self.assertRaises(FacturationError):
            creer_avoir(self._facture_emise(), "  ")

    def test_regles_de_coherence_dun_avoir(self):
        facture = self._facture_emise()

        def erreurs(**kw):
            donnees = dict(numero="AV-T", commande=self.commande, type_document="avoir", facture_origine=facture,
                           motif="m", date_facturation=datetime.date(2026, 2, 3), montant_ht=-10, montant_ttc=-12)
            donnees.update(kw)
            with self.assertRaises(ValidationError) as cm:
                Facture(**donnees).full_clean()
            return cm.exception.message_dict

        self.assertIn("montant_ht", erreurs(montant_ht=10, montant_ttc=12))
        self.assertIn("facture_origine", erreurs(facture_origine=None))
        self.assertIn("motif", erreurs(motif=""))
        self.assertIn("date_facturation", erreurs(date_facturation=datetime.date(2026, 1, 15)))
        self.assertIn("montant_ht", erreurs(montant_ht=-100, montant_ttc=-120))
        with self.assertRaises(ValidationError) as cm:
            Facture(numero="F-X", commande=self.commande, facture_origine=facture, date_facturation=datetime.date(2026, 2, 3)).full_clean()
        self.assertIn("facture_origine", cm.exception.message_dict)

    def test_la_facture_deja_creditee_reste_intacte_et_non_supprimable(self):
        facture = self._facture_emise()
        creer_avoir(facture, "x")
        facture.refresh_from_db()
        self.assertEqual(facture.montant_ht, 60.0)
        with self.assertRaises(FactureVerrouilleeError):
            facture.delete()

    def test_annulation_de_livraison_possible_apres_avoir_integral(self):
        livraison = Livraison.objects.first()
        facture = self._facture_emise()
        with self.assertRaises(Exception):
            livraison.annuler()
        creer_avoir(facture, "Livraison annulée")
        livraison.annuler()
        self.ligne.refresh_from_db()
        self.assertEqual(self.ligne.quantite_livree, 0)

    def test_api_avoir_et_permission(self):
        facture = self._facture_emise()
        simple = get_user_model().objects.create_user("api-simple-f", "s@example.com", "pass1234", is_staff=True)
        self.client.force_login(simple)
        self.assertEqual(self.client.post(f"/api/v1/factures/{facture.pk}/avoir/", data={"motif": "x"}).status_code, 403)
        self.client.force_login(self.user)
        r = self.client.post(f"/api/v1/factures/{facture.pk}/avoir/", data=json.dumps({"motif": "Retour"}), content_type="application/json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["montant_ht"], -60.0)
        again = self.client.post(f"/api/v1/factures/{facture.pk}/avoir/", data=json.dumps({"motif": "Encore"}), content_type="application/json")
        self.assertEqual(again.status_code, 400)

    def test_admin_page_et_creation_davoir(self):
        facture = self._facture_emise()
        page = self.client.get(f"/admin/facturation/facture/{facture.pk}/avoir/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Motif")
        self.client.post(f"/admin/facturation/facture/{facture.pk}/avoir/", {"motif": "Erreur"}, follow=True)
        self.assertTrue(Facture.objects.filter(type_document="avoir", facture_origine=facture).exists())


class PreparerFactureTests(_FixtureLignes, TestCase):
    """Facture brouillon du livré non facturé : numéro, lignes, montants."""

    def test_prepare_le_livre_non_facture(self):
        facture = preparer_facture(self.commande)
        self.assertEqual(facture.numero, "FAC-00001")  # règle de codification « Facture » préconfigurée
        self.assertEqual(facture.mode_creation, "automatique")
        self.assertEqual(facture.lignes.get().quantite, 6)
        self.assertEqual((facture.montant_ht, facture.montant_ttc), (60.0, 72.0))
        self.assertFalse(facture_verrouillee(facture))

    def test_seconde_preparation_ne_prend_que_le_nouveau_livre(self):
        preparer_facture(self.commande)
        with self.assertRaises(FacturationError):
            preparer_facture(self.commande)
        self._livrer(3)
        facture = preparer_facture(self.commande)
        self.assertEqual(facture.numero, "FAC-00002")  # le compteur de codification a avancé
        self.assertEqual(facture.lignes.get().quantite, 3)

    def test_numero_de_repli_sans_regle_de_codification(self):
        from codification.models import RegleCodification

        RegleCodification.objects.filter(pk="facture").delete()
        self.assertEqual(preparer_facture(self.commande).numero, "FAC-CDE-FACT")
        self._livrer(2)
        self.assertEqual(preparer_facture(self.commande).numero, "FAC-CDE-FACT-2")

    def test_rien_a_facturer_sans_livraison(self):
        autre = Commande.objects.create(
            numero="CDE-RIEN", client=self.tiers, reference_client="X", date_commande=datetime.date(2026, 1, 1),
            adresse_facturation=self.adresse, adresse_livraison=self.adresse,
        )
        CommandeLigne.objects.create(commande=autre, article=self.article, quantite_commandee=5, prix_vente_unitaire=1)
        with self.assertRaises(FacturationError):
            preparer_facture(autre)

    def test_commande_annulee_refusee(self):
        self.commande.statut = Commande.Statut.ANNULEE
        self.commande.save()
        with self.assertRaises(FacturationError):
            preparer_facture(self.commande)

    def test_anticipee_prend_tout_le_commande(self):
        facture = preparer_facture(self.commande, anticipee=True)
        self.assertEqual(facture.lignes.get().quantite, 10)
        self.assertTrue(facture.anticipee)

    def test_vue_admin_liste_et_prepare(self):
        page = self.client.get("/admin/facturation/facture/preparer/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "CDE-FACT")
        self.client.post("/admin/facturation/facture/preparer/", {"commande": self.commande.pk}, follow=True)
        self.assertTrue(Facture.objects.filter(commande=self.commande, mode_creation="automatique").exists())
        page = self.client.get("/admin/facturation/facture/preparer/")
        self.assertContains(page, "Rien à facturer")

    def test_api_preparer(self):
        r = self.client.post("/api/v1/factures/preparer/", data=json.dumps({"commande": self.commande.pk}), content_type="application/json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(self.client.post("/api/v1/factures/preparer/", data=json.dumps({"commande": self.commande.pk}), content_type="application/json").status_code, 400)

    def test_anticipee_exige_la_permission(self):
        simple = get_user_model().objects.create_user("antic", "a@example.com", "pass1234", is_staff=True)
        from django.contrib.auth.models import Permission

        simple.user_permissions.set(Permission.objects.filter(codename__in=["add_facture", "view_facture"]))
        self.client.force_login(simple)
        r = self.client.post("/api/v1/factures/preparer/", data=json.dumps({"commande": self.commande.pk, "anticipee": True}), content_type="application/json")
        self.assertEqual(r.status_code, 403)

    def test_indicateurs_sur_la_ligne_de_commande_dans_ladmin(self):
        preparer_facture(self.commande)
        page = self.client.get(f"/admin/chiffrage/commande/{self.commande.pk}/change/")
        self.assertContains(page, "Livré non facturé")


class EcrituresFactureAvoirTests(_FixtureLignes, TestCase):
    """C-TD-01 : l'écriture suit ce que la facture facture réellement."""

    def setUp(self):
        super().setUp()
        from comptabilite.pcg import importer_pcg

        importer_pcg()

    def test_facture_partielle_ne_comptabilise_que_le_facture(self):
        from comptabilite.generation import generer_ecriture_facture

        facture = preparer_facture(self.commande)  # 6 sur 10
        ecriture, _ = generer_ecriture_facture(facture)
        self.assertTrue(ecriture.est_equilibree)
        self.assertAlmostEqual(ecriture.total_debit, 72.0)  # et non 120 (toute la commande)

    def test_deux_factures_du_meme_ordre_ont_chacune_leur_ecriture(self):
        from comptabilite.generation import generer_ecriture_facture

        premiere = preparer_facture(self.commande)
        self._livrer(4)
        seconde = preparer_facture(self.commande)
        e1, _ = generer_ecriture_facture(premiere)
        e2, _ = generer_ecriture_facture(seconde)
        self.assertAlmostEqual(e1.total_debit + e2.total_debit, 120.0)

    def test_avoir_ecriture_en_sens_inverse(self):
        from comptabilite.generation import generer_ecriture_facture

        facture = preparer_facture(self.commande)
        facture.reference_tiime = "TIIME-C"
        facture.save()
        avoir = creer_avoir(facture, "Retour")
        ecriture, _ = generer_ecriture_facture(avoir)
        self.assertTrue(ecriture.est_equilibree)
        client = ecriture.lignes.get(compte__code="411")
        self.assertEqual((client.debit, client.credit), (0, 72.0))
        vente = ecriture.lignes.get(compte__code="706")
        self.assertEqual((vente.debit, vente.credit), (60.0, 0))
        tva = ecriture.lignes.get(compte__code="44571")
        self.assertEqual((tva.debit, tva.credit), (12.0, 0))
        self.assertTrue(ecriture.libelle.startswith("Avoir"))

    def test_facture_sans_lignes_garde_le_comportement_historique(self):
        from comptabilite.generation import generer_ecriture_facture

        ancienne = self._facture(numero="FAC-ANCIENNE", montant_ht=100, montant_ttc=120)
        ecriture, _ = generer_ecriture_facture(ancienne)
        self.assertTrue(ecriture.est_equilibree)
        self.assertAlmostEqual(ecriture.total_debit, 120.0)  # lignes de la commande : 10 x 10 HT, 20 %
