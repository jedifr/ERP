import datetime

from django.core.exceptions import ValidationError
from django.test import TestCase

from .models import Adresse, Contact, ContactTelephone, ConditionPaiement, DelaiPropose, Devise, Pays, TauxTVA, Tiers


class TiersRegimeFiscalTests(TestCase):
    def test_regime_fiscal_par_defaut_france(self):
        tiers = Tiers.objects.create(
            code="CLI-REGIME", raison_sociale="Client Régime", type_tiers=Tiers.TypeTiers.CLIENT
        )
        self.assertEqual(tiers.regime_fiscal, Tiers.RegimeFiscal.FRANCE)


class RegimeFiscalAutoDepuisPaysTests(TestCase):
    def setUp(self):
        self.tiers = Tiers.objects.create(
            code="CLI-PAYS", raison_sociale="Client Pays", type_tiers=Tiers.TypeTiers.CLIENT
        )
        # FR/DE/US sont déjà en base via la migration de seed (0011_seed_pays).
        self.france = Pays.objects.get(code="FR")
        self.allemagne = Pays.objects.get(code="DE")
        self.etats_unis = Pays.objects.get(code="US")

    def _adresse_livraison_principale(self, pays):
        return Adresse.objects.create(
            tiers=self.tiers, type_adresse=Adresse.TypeAdresse.LIVRAISON,
            adresse="1 rue", code_postal="00000", ville="Ville", pays=pays, est_principale=True,
        )

    def test_pays_france_donne_regime_france(self):
        self._adresse_livraison_principale(self.france)
        self.tiers.refresh_from_db()
        self.assertEqual(self.tiers.regime_fiscal, Tiers.RegimeFiscal.FRANCE)

    def test_pays_ue_donne_regime_intra_ue(self):
        self._adresse_livraison_principale(self.allemagne)
        self.tiers.refresh_from_db()
        self.assertEqual(self.tiers.regime_fiscal, Tiers.RegimeFiscal.INTRA_UE)

    def test_pays_hors_ue_donne_regime_hors_ue(self):
        self._adresse_livraison_principale(self.etats_unis)
        self.tiers.refresh_from_db()
        self.assertEqual(self.tiers.regime_fiscal, Tiers.RegimeFiscal.HORS_UE)

    def test_france_exoneree_jamais_ecrasee_automatiquement(self):
        self.tiers.regime_fiscal = Tiers.RegimeFiscal.FRANCE_EXONERE
        self.tiers.save()
        self._adresse_livraison_principale(self.allemagne)
        self.tiers.refresh_from_db()
        self.assertEqual(self.tiers.regime_fiscal, Tiers.RegimeFiscal.FRANCE_EXONERE)

    def test_repli_sur_adresse_facturation_si_pas_de_livraison(self):
        Adresse.objects.create(
            tiers=self.tiers, type_adresse=Adresse.TypeAdresse.FACTURATION,
            adresse="1 rue", code_postal="00000", ville="Ville", pays=self.allemagne, est_principale=True,
        )
        self.tiers.refresh_from_db()
        self.assertEqual(self.tiers.regime_fiscal, Tiers.RegimeFiscal.INTRA_UE)


class ConditionPaiementTests(TestCase):
    def test_creation_et_str(self):
        condition = ConditionPaiement.objects.create(libelle="30 jours fin de mois", nombre_jours=30, fin_de_mois=True)
        self.assertEqual(str(condition), "30 jours fin de mois")

    def test_tiers_utilise_une_condition_de_la_bibliotheque(self):
        condition = ConditionPaiement.objects.create(libelle="Comptant", nombre_jours=0)
        tiers = Tiers.objects.create(
            code="CLI-CDT", raison_sociale="Client Condition", type_tiers=Tiers.TypeTiers.CLIENT,
            conditions_paiement=condition,
        )
        self.assertEqual(tiers.conditions_paiement.nombre_jours, 0)


class ContactTelephoneTests(TestCase):
    def test_plusieurs_numeros_types_par_contact(self):
        tiers = Tiers.objects.create(
            code="CLI-TEL", raison_sociale="Client Téléphone", type_tiers=Tiers.TypeTiers.CLIENT
        )
        contact = Contact.objects.create(tiers=tiers, nom="Dupont")
        ContactTelephone.objects.create(contact=contact, type_telephone=ContactTelephone.TypeTelephone.PORTABLE, numero="0601020304")
        ContactTelephone.objects.create(contact=contact, type_telephone=ContactTelephone.TypeTelephone.BUREAU, numero="0102030405")

        self.assertEqual(contact.telephones.count(), 2)
        types = set(contact.telephones.values_list("type_telephone", flat=True))
        self.assertEqual(types, {"portable", "bureau"})


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

    def test_visites_successives_sans_enregistrer_ne_sautent_aucun_numero(self):
        # Un formulaire d'ajout consulté puis abandonné sans être enregistré
        # ne doit pas faire avancer le compteur — sinon le prochain tiers
        # réellement créé « saute » un numéro (voir
        # stock.tests.EmplacementAdminCodificationTests pour la vérification
        # de bout en bout, y compris après un enregistrement réel, sur une
        # entité sans inline)."""
        self.client.get("/admin/commercial/tiers/add/")
        response = self.client.get("/admin/commercial/tiers/add/")
        self.assertContains(response, "TIERS-00001")


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


class ContactInlineAdresseLivraisonChoicesTests(TestCase):
    """Le champ « Adresse de livraison associée » du tableau Contacts, sur
    la fiche Tiers, ne doit proposer que les adresses de livraison du tiers
    en cours d'édition — jamais celles d'un autre tiers (l'autocomplete
    précédent interrogeait tout le référentiel Adresse sans filtre), et
    aucune adresse tant que le tiers n'a pas encore été enregistré une
    première fois (ses adresses n'existent pas encore en base à ce
    moment-là — incohérent de prétendre en proposer une)."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        self.user = User.objects.create_superuser("adresse-livraison-admin", "al@example.com", "pass1234")
        self.client.force_login(self.user)

        self.tiers = Tiers.objects.create(
            code="CLI-ADR-LIV", raison_sociale="Client Adresse Livraison", type_tiers=Tiers.TypeTiers.CLIENT
        )
        self.contact = Contact.objects.create(tiers=self.tiers, nom="Site")
        self.livraison = Adresse.objects.create(
            tiers=self.tiers, type_adresse=Adresse.TypeAdresse.LIVRAISON,
            libelle="Entrepôt propre", adresse="1 rue", code_postal="75000", ville="Paris",
        )
        self.facturation = Adresse.objects.create(
            tiers=self.tiers, type_adresse=Adresse.TypeAdresse.FACTURATION,
            libelle="Siège propre", adresse="1 rue", code_postal="75000", ville="Paris",
        )
        autre_tiers = Tiers.objects.create(
            code="CLI-ADR-LIV-AUTRE", raison_sociale="Autre Client", type_tiers=Tiers.TypeTiers.CLIENT
        )
        self.livraison_autre_tiers = Adresse.objects.create(
            tiers=autre_tiers, type_adresse=Adresse.TypeAdresse.LIVRAISON,
            libelle="Entrepôt autre tiers", adresse="2 rue", code_postal="75000", ville="Paris",
        )

    def test_formulaire_ajout_ne_propose_aucune_adresse(self):
        response = self.client.get("/admin/commercial/tiers/add/")
        self.assertEqual(response.status_code, 200)
        formset = response.context["inline_admin_formsets"][2].formset
        self.assertEqual(formset.empty_form.fields["adresse_livraison"].queryset.count(), 0)

    def test_formulaire_modification_ne_propose_que_les_adresses_de_livraison_du_tiers(self):
        response = self.client.get(f"/admin/commercial/tiers/{self.tiers.pk}/change/")
        self.assertEqual(response.status_code, 200)
        formset = response.context["inline_admin_formsets"][2].formset
        queryset = formset.forms[0].fields["adresse_livraison"].queryset
        self.assertIn(self.livraison, queryset)
        self.assertNotIn(self.facturation, queryset)
        self.assertNotIn(self.livraison_autre_tiers, queryset)


class ContactAdminAdresseLivraisonChoicesTests(TestCase):
    """Même correctif que ContactInlineAdresseLivraisonChoicesTests, mais
    sur la fiche Contact autonome (/admin/commercial/contact/)."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        self.user = User.objects.create_superuser("contact-admin-adr", "ca@example.com", "pass1234")
        self.client.force_login(self.user)

        self.tiers = Tiers.objects.create(
            code="CLI-CONTACT-ADMIN-ADR", raison_sociale="Client Contact Admin Adr", type_tiers=Tiers.TypeTiers.CLIENT
        )
        self.contact = Contact.objects.create(tiers=self.tiers, nom="Site")
        self.livraison = Adresse.objects.create(
            tiers=self.tiers, type_adresse=Adresse.TypeAdresse.LIVRAISON,
            libelle="Entrepôt propre", adresse="1 rue", code_postal="75000", ville="Paris",
        )
        self.facturation = Adresse.objects.create(
            tiers=self.tiers, type_adresse=Adresse.TypeAdresse.FACTURATION,
            libelle="Siège propre", adresse="1 rue", code_postal="75000", ville="Paris",
        )
        autre_tiers = Tiers.objects.create(
            code="CLI-CONTACT-ADMIN-ADR-AUTRE", raison_sociale="Autre Client", type_tiers=Tiers.TypeTiers.CLIENT
        )
        self.livraison_autre_tiers = Adresse.objects.create(
            tiers=autre_tiers, type_adresse=Adresse.TypeAdresse.LIVRAISON,
            libelle="Entrepôt autre tiers", adresse="2 rue", code_postal="75000", ville="Paris",
        )

    def test_formulaire_ajout_ne_propose_aucune_adresse(self):
        response = self.client.get("/admin/commercial/contact/add/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["adminform"].form.fields["adresse_livraison"].queryset.count(), 0)

    def test_formulaire_modification_ne_propose_que_les_adresses_de_livraison_du_tiers(self):
        response = self.client.get(f"/admin/commercial/contact/{self.contact.pk}/change/")
        self.assertEqual(response.status_code, 200)
        queryset = response.context["adminform"].form.fields["adresse_livraison"].queryset
        self.assertIn(self.livraison, queryset)
        self.assertNotIn(self.facturation, queryset)
        self.assertNotIn(self.livraison_autre_tiers, queryset)


class TiersIbanTests(TestCase):
    def test_iban_valide_accepte(self):
        # IBAN français d'exemple, valide (clé de contrôle correcte).
        tiers = Tiers(
            code="FOUR-IBAN-OK", raison_sociale="Fournisseur IBAN OK", type_tiers=Tiers.TypeTiers.FOURNISSEUR,
            iban="FR7630006000011234567890189",
        )
        tiers.full_clean()  # ne doit pas lever

    def test_iban_avec_espaces_accepte(self):
        tiers = Tiers(
            code="FOUR-IBAN-ESP", raison_sociale="Fournisseur IBAN Espaces", type_tiers=Tiers.TypeTiers.FOURNISSEUR,
            iban="FR76 3000 6000 0112 3456 7890 189",
        )
        tiers.full_clean()  # ne doit pas lever

    def test_iban_cle_de_controle_invalide_refuse(self):
        tiers = Tiers(
            code="FOUR-IBAN-KO", raison_sociale="Fournisseur IBAN KO", type_tiers=Tiers.TypeTiers.FOURNISSEUR,
            iban="FR7630006000011234567890180",
        )
        with self.assertRaises(ValidationError):
            tiers.full_clean()

    def test_iban_format_invalide_refuse(self):
        tiers = Tiers(
            code="FOUR-IBAN-FMT", raison_sociale="Fournisseur IBAN Format", type_tiers=Tiers.TypeTiers.FOURNISSEUR,
            iban="PAS-UN-IBAN",
        )
        with self.assertRaises(ValidationError):
            tiers.full_clean()

    def test_iban_vide_autorise(self):
        tiers = Tiers(
            code="FOUR-IBAN-VIDE", raison_sociale="Fournisseur Sans IBAN", type_tiers=Tiers.TypeTiers.FOURNISSEUR,
        )
        tiers.full_clean()  # ne doit pas lever


class DeviseTests(TestCase):
    def test_seed_devises_par_defaut(self):
        # Migration de données 0013_seed_devise.
        self.assertTrue(Devise.objects.filter(code="EUR").exists())
        self.assertTrue(Devise.objects.filter(code="USD").exists())

    def test_tiers_devise(self):
        eur = Devise.objects.get(code="EUR")
        tiers = Tiers.objects.create(
            code="CLI-DEVISE", raison_sociale="Client Devise", type_tiers=Tiers.TypeTiers.CLIENT, devise=eur,
        )
        self.assertEqual(tiers.devise.symbole, "€")

    def test_str_avec_et_sans_symbole(self):
        avec = Devise.objects.get(code="USD")
        self.assertEqual(str(avec), "USD ($)")
        sans = Devise.objects.create(code="XXX", nom="Devise sans symbole")
        self.assertEqual(str(sans), "XXX")


class ConditionPaiementEcheanceTests(TestCase):
    def test_echeance_simple_sans_fin_de_mois(self):
        condition = ConditionPaiement.objects.create(libelle="30 jours net", nombre_jours=30)
        echeance = condition.calculer_echeance(datetime.date(2026, 1, 1))
        self.assertEqual(echeance, datetime.date(2026, 1, 31))

    def test_echeance_reportee_en_fin_de_mois(self):
        condition = ConditionPaiement.objects.create(libelle="30 jours fin de mois", nombre_jours=30, fin_de_mois=True)
        echeance = condition.calculer_echeance(datetime.date(2026, 1, 1))
        # 1er janvier + 30 jours = 31 janvier -> déjà fin de mois de janvier.
        self.assertEqual(echeance, datetime.date(2026, 1, 31))

    def test_echeance_fin_de_mois_change_de_mois(self):
        condition = ConditionPaiement.objects.create(libelle="15 jours fin de mois", nombre_jours=15, fin_de_mois=True)
        echeance = condition.calculer_echeance(datetime.date(2026, 1, 20))
        # 20 janvier + 15 jours = 4 février -> reporté au dernier jour de février.
        self.assertEqual(echeance, datetime.date(2026, 2, 28))

    def test_echeance_none_sans_nombre_de_jours(self):
        condition = ConditionPaiement.objects.create(libelle="À réception, sans délai chiffré")
        self.assertIsNone(condition.calculer_echeance(datetime.date(2026, 1, 1)))


class ApercuCompteComptableViewTests(TestCase):
    """Endpoint AJAX utilisé par tiers_admin.js pour afficher, dès la frappe
    du code à 5 caractères, le compte comptable que TiersCompteComptable.save()
    résoudrait (voir comptabilite.models) — sans avoir à enregistrer le
    formulaire pour le voir."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        from comptabilite.models import CompteComptable

        User = get_user_model()
        self.user = User.objects.create_superuser("apercu-admin", "a@example.com", "pass1234")
        self.client.force_login(self.user)
        self.compte_existant = CompteComptable.objects.create(code="411DUPON", libelle="Client Dupont")

    def test_code_correspondant_a_un_compte_existant(self):
        response = self.client.get("/admin/commercial/tiers/apercu-compte-comptable/?prefixe=411&code=dupon")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data, {"valide": True, "code": "411DUPON", "existe": True, "libelle": "Client Dupont"})

    def test_code_ne_correspondant_a_aucun_compte(self):
        response = self.client.get("/admin/commercial/tiers/apercu-compte-comptable/?prefixe=401&code=martl")
        data = response.json()
        self.assertEqual(data, {"valide": True, "code": "401MARTL", "existe": False, "libelle": None})

    def test_code_avec_chiffres_accepte(self):
        response = self.client.get("/admin/commercial/tiers/apercu-compte-comptable/?prefixe=401&code=dup01")
        data = response.json()
        self.assertEqual(data, {"valide": True, "code": "401DUP01", "existe": False, "libelle": None})

    def test_code_incomplet_invalide(self):
        response = self.client.get("/admin/commercial/tiers/apercu-compte-comptable/?prefixe=411&code=dup")
        self.assertEqual(response.json(), {"valide": False})

    def test_prefixe_inconnu_invalide(self):
        response = self.client.get("/admin/commercial/tiers/apercu-compte-comptable/?prefixe=706&code=dupon")
        self.assertEqual(response.json(), {"valide": False})

    def test_anonyme_refuse(self):
        self.client.logout()
        response = self.client.get("/admin/commercial/tiers/apercu-compte-comptable/?prefixe=411&code=dupon")
        self.assertNotEqual(response.status_code, 200)


class TiersContactTelephoneNestedInlineTests(TestCase):
    """Les numéros de téléphone d'un contact (ContactTelephone) doivent être
    saisissables directement depuis la fiche Tiers, sans passer par la fiche
    Contact dédiée (inline imbriqué, voir commercial/admin.py)."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        self.user = User.objects.create_superuser("nested-admin", "n@example.com", "pass1234")
        self.client.force_login(self.user)

        self.tiers = Tiers.objects.create(
            code="CLI-NESTED-TEL", raison_sociale="Client Nested Tel", type_tiers=Tiers.TypeTiers.CLIENT
        )
        self.contact = Contact.objects.create(tiers=self.tiers, nom="Dupont")
        ContactTelephone.objects.create(
            contact=self.contact, type_telephone=ContactTelephone.TypeTelephone.PORTABLE, numero="0601020304"
        )

    def test_numero_de_telephone_visible_sur_la_fiche_tiers(self):
        response = self.client.get(f"/admin/commercial/tiers/{self.tiers.pk}/change/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "0601020304")


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
