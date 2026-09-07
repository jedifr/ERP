from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


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
