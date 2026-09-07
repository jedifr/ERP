from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from chiffrage.models import Commande, Devis, OrdreFabrication
from commercial.models import Adresse, Tiers
from facturation.models import Facture
from stock.models import AlerteStock
from technique.models import Article

from .dashboard import dashboard_callback


class AjoutUtilisateurAdminTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser("super-test", "super@example.com", "pass1234")
        self.client.force_login(self.admin)

    def test_formulaire_ajout_ne_propose_pas_le_choix_sso_ldap(self):
        response = self.client.get(reverse("admin:auth_user_add"))
        self.assertNotContains(response, "usable_password")

    def test_formulaire_ajout_expose_bien_les_champs_simplifies(self):
        response = self.client.get(reverse("admin:auth_user_add"))
        self.assertContains(response, 'name="first_name"')
        self.assertContains(response, 'name="last_name"')
        self.assertContains(response, 'name="is_staff"')
        self.assertNotContains(response, 'name="is_superuser"')
        self.assertNotContains(response, 'name="groups"')

    def test_creation_via_le_formulaire_simplifie(self):
        response = self.client.post(
            reverse("admin:auth_user_add"),
            {
                "username": "atelier1",
                "first_name": "Jean",
                "last_name": "Dupont",
                "password1": "MotDePasse#2026",
                "password2": "MotDePasse#2026",
                "is_staff": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        User = get_user_model()
        user = User.objects.get(username="atelier1")
        self.assertEqual(user.first_name, "Jean")
        self.assertEqual(user.last_name, "Dupont")
        self.assertTrue(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertTrue(user.check_password("MotDePasse#2026"))

    def test_creation_sans_prenom_ni_nom_reste_possible(self):
        response = self.client.post(
            reverse("admin:auth_user_add"),
            {
                "username": "atelier2",
                "password1": "MotDePasse#2026",
                "password2": "MotDePasse#2026",
            },
        )
        self.assertEqual(response.status_code, 302)
        User = get_user_model()
        user = User.objects.get(username="atelier2")
        self.assertFalse(user.is_staff)


class DashboardCallbackTests(TestCase):
    def setUp(self):
        aujourdhui = timezone.localdate()
        self.client_tiers = Tiers.objects.create(code="CLI-DASH", raison_sociale="Client Dashboard")
        self.adresse = Adresse.objects.create(
            tiers=self.client_tiers,
            type_adresse=Adresse.TypeAdresse.FACTURATION,
            adresse="1 rue Test",
            code_postal="75000",
            ville="Paris",
        )

        Devis.objects.create(
            numero="DEV-DASH-BROUILLON", client=self.client_tiers, date_creation=aujourdhui,
            statut=Devis.Statut.BROUILLON,
        )
        devis_valide = Devis.objects.create(
            numero="DEV-DASH-VALIDE", client=self.client_tiers, date_creation=aujourdhui,
            statut=Devis.Statut.VALIDE,
        )

        commande = Commande.objects.create(
            numero="CDE-DASH", devis=devis_valide, date_commande=aujourdhui,
            adresse_facturation=self.adresse, adresse_livraison=self.adresse,
        )
        Facture.objects.create(
            numero="FAC-DASH", commande=commande, montant_ht=1000, montant_ttc=1200,
            date_facturation=aujourdhui,
        )

        article = Article.objects.create(reference="PIECE-DASH", nature=Article.Nature.FABRIQUE)
        OrdreFabrication.objects.create(
            numero="OF-DASH", commande=commande, article=article, quantite=1, date_lancement=aujourdhui,
        )

        AlerteStock.objects.create(article=article, date_declenchement=aujourdhui)

    def test_kpis_reussissent_les_bons_compteurs(self):
        context = dashboard_callback(request=None, context={})
        kpis = {kpi["title"]: kpi for kpi in context["kpis"]}

        self.assertEqual(kpis["Devis en brouillon"]["value"], 1)
        self.assertEqual(kpis["Devis validés"]["value"], 1)
        self.assertEqual(kpis["CA facturé"]["value"], "1 000 €")
        self.assertEqual(kpis["Alertes de stock"]["value"], 1)
        self.assertTrue(kpis["Alertes de stock"]["attention"])
        self.assertEqual(kpis["Ordres de fabrication"]["value"], 1)
