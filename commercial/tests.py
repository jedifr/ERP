from django.core.exceptions import ValidationError
from django.test import TestCase

from .models import Adresse, Contact, DelaiPropose, TauxTVA, Tiers


class AdresseTests(TestCase):
    def setUp(self):
        self.client_tiers = Tiers.objects.create(
            code="CLI-001", raison_sociale="Client Test", type_tiers=Tiers.TypeTiers.CLIENT
        )

    def test_une_seule_adresse_principale_par_type(self):
        Adresse.objects.create(
            tiers=self.client_tiers,
            type_adresse=Adresse.TypeAdresse.FACTURATION,
            adresse="1 rue A",
            code_postal="75000",
            ville="Paris",
            est_principale=True,
        )
        deuxieme = Adresse(
            tiers=self.client_tiers,
            type_adresse=Adresse.TypeAdresse.FACTURATION,
            adresse="2 rue B",
            code_postal="75000",
            ville="Paris",
            est_principale=True,
        )
        with self.assertRaises(ValidationError):
            deuxieme.full_clean()

    def test_principale_facturation_et_livraison_coexistent(self):
        Adresse.objects.create(
            tiers=self.client_tiers,
            type_adresse=Adresse.TypeAdresse.FACTURATION,
            adresse="1 rue A",
            code_postal="75000",
            ville="Paris",
            est_principale=True,
        )
        livraison = Adresse(
            tiers=self.client_tiers,
            type_adresse=Adresse.TypeAdresse.LIVRAISON,
            adresse="1 rue A",
            code_postal="75000",
            ville="Paris",
            est_principale=True,
        )
        livraison.full_clean()  # ne doit pas lever d'exception


class TauxTVATests(TestCase):
    def test_seed_taux_normal_par_defaut(self):
        # Vérifie la migration de données (0005_seed_taux_tva) : le référentiel
        # est pré-rempli avec les taux français courants, "Taux normal" par défaut.
        self.assertTrue(TauxTVA.objects.filter(nom="Taux normal", taux=20, est_defaut=True).exists())
        self.assertEqual(TauxTVA.objects.filter(est_defaut=True).count(), 1)

    def test_un_seul_taux_par_defaut(self):
        autre = TauxTVA(nom="Taux test", taux=15, est_defaut=True)
        with self.assertRaises(ValidationError):
            autre.full_clean()

    def test_remplacer_le_taux_par_defaut(self):
        TauxTVA.objects.filter(est_defaut=True).update(est_defaut=False)
        nouveau = TauxTVA(nom="Taux test 2", taux=8, est_defaut=True)
        nouveau.full_clean()  # ne doit pas lever d'exception


class TiersAdminCodificationTests(TestCase):
    """Le formulaire d'ajout de Tiers doit être pré-rempli avec le code généré
    par la règle de codification "tiers" (voir l'app codification)."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        self.user = User.objects.create_superuser("code-admin", "c@example.com", "pass1234")
        self.client.force_login(self.user)

    def test_formulaire_ajout_pre_rempli_avec_le_code_genere(self):
        response = self.client.get("/admin/commercial/tiers/add/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "TIERS-00001")

    def test_visites_successives_incrementent_le_compteur(self):
        self.client.get("/admin/commercial/tiers/add/")
        response = self.client.get("/admin/commercial/tiers/add/")
        self.assertContains(response, "TIERS-00002")


class TiersInlinesSansLigneVideTests(TestCase):
    """Régression : modifier un tiers qui a déjà une adresse/un contact ne
    doit plus afficher de ligne supplémentaire vide dans les inlines (les
    champs adresse/code postal/ville étant obligatoires, cette ligne
    "en trop" affichait des astérisques rouges "obligatoire" sur des champs
    que l'utilisateur n'avait pas l'intention de remplir)."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        self.user = User.objects.create_superuser("inlines-admin", "i@example.com", "pass1234")
        self.client.force_login(self.user)

        self.tiers = Tiers.objects.create(
            code="CLI-INLINES-VIDES", raison_sociale="Client Inlines Vides", type_tiers=Tiers.TypeTiers.CLIENT
        )
        Adresse.objects.create(
            tiers=self.tiers,
            type_adresse=Adresse.TypeAdresse.FACTURATION,
            adresse="1 rue Test",
            code_postal="75000",
            ville="Paris",
        )
        Contact.objects.create(tiers=self.tiers, nom="Existant")

    def test_une_seule_ligne_adresse_et_contact_sur_le_formulaire(self):
        response = self.client.get(f"/admin/commercial/tiers/{self.tiers.pk}/change/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="adresses-TOTAL_FORMS" value="1"', html=False)
        self.assertContains(response, 'name="contacts-TOTAL_FORMS" value="1"', html=False)


class ContactAdresseLivraisonTests(TestCase):
    """Contact.adresse_livraison : associe optionnellement un contact à une
    adresse de livraison précise du tiers (ex. le contact sur place à un
    site) — doit forcément appartenir au même tiers et être de type
    Livraison (même esprit que Devis.clean() pour adresse_facturation/
    adresse_livraison/contact vis-à-vis du client)."""

    def setUp(self):
        self.tiers = Tiers.objects.create(
            code="CLI-CONTACT-ADRESSE", raison_sociale="Client Contact Adresse", type_tiers=Tiers.TypeTiers.CLIENT
        )
        self.livraison = Adresse.objects.create(
            tiers=self.tiers,
            type_adresse=Adresse.TypeAdresse.LIVRAISON,
            adresse="1 rue de la Livraison",
            code_postal="75000",
            ville="Paris",
        )
        self.facturation = Adresse.objects.create(
            tiers=self.tiers,
            type_adresse=Adresse.TypeAdresse.FACTURATION,
            adresse="2 rue de la Facture",
            code_postal="75000",
            ville="Paris",
        )

    def test_association_valide(self):
        contact = Contact(tiers=self.tiers, nom="Site", adresse_livraison=self.livraison)
        contact.full_clean()  # ne doit pas lever

    def test_adresse_d_un_autre_tiers_refusee(self):
        autre_tiers = Tiers.objects.create(
            code="CLI-CONTACT-ADRESSE-2", raison_sociale="Autre Client", type_tiers=Tiers.TypeTiers.CLIENT
        )
        contact = Contact(tiers=autre_tiers, nom="Site", adresse_livraison=self.livraison)
        with self.assertRaises(ValidationError):
            contact.full_clean()

    def test_adresse_de_facturation_refusee(self):
        contact = Contact(tiers=self.tiers, nom="Site", adresse_livraison=self.facturation)
        with self.assertRaises(ValidationError):
            contact.full_clean()


class DelaiProposeTests(TestCase):
    def test_libelle_unique(self):
        DelaiPropose.objects.create(libelle="2 semaines")
        doublon = DelaiPropose(libelle="2 semaines")
        with self.assertRaises(ValidationError):
            doublon.full_clean()

    def test_ordre_par_defaut_zero(self):
        delai = DelaiPropose.objects.create(libelle="Sur stock")
        self.assertEqual(delai.ordre, 0)

    def test_str_renvoie_le_libelle(self):
        delai = DelaiPropose.objects.create(libelle="4 à 6 semaines")
        self.assertEqual(str(delai), "4 à 6 semaines")
