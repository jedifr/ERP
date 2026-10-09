"""Lot 1 de l'ergonomie : accueil « Aujourd'hui » par profil, page Paramétrage, menu court, puces de filtre."""

import datetime

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import RequestFactory, TestCase
from django.urls import reverse

from . import accueil
from .parametrage import cartes, ecrans_pour_recherche


def _utilisateur(nom, groupe=None, perms=(), superuser=False):
    User = get_user_model()
    u = User.objects.create_superuser(nom, f"{nom}@example.com", "pass-mot-de-passe-20") if superuser else User.objects.create_user(nom, password="pass-mot-de-passe-20", is_staff=True)
    if groupe:
        g, _ = Group.objects.get_or_create(name=groupe)
        u.groups.add(g)
    for code in perms:
        app, codename = code.split(".")
        u.user_permissions.add(Permission.objects.get(content_type__app_label=app, codename=codename))
    return u


def _requete(u):
    r = RequestFactory().get("/admin/")
    r.user = u
    return r


class ProfilsTests(TestCase):
    def test_profil_deduit_du_groupe(self):
        self.assertEqual(accueil.profils(_utilisateur("c1", "Commercial")), {"commercial"})
        self.assertEqual(accueil.profils(_utilisateur("a1", "Atelier")), {"atelier"})
        self.assertEqual(accueil.profils(_utilisateur("f1", "Facturation")), {"comptabilite"})
        self.assertEqual(accueil.profils(_utilisateur("s1", superuser=True)), {"direction"})
        self.assertEqual(accueil.profils(_utilisateur("x1")), set())

    def test_cartes_selon_profil_et_droits(self):
        atelier = _utilisateur("a2", "Atelier", ["chiffrage.view_ordrefabrication", "chiffrage.view_devis", "decoupe.view_piecedecoupe"])
        titres = {c["titre"] for c in sum(accueil.cartes_action(_requete(atelier)), [])}
        self.assertIn("Pièces en erreur d'import", titres)
        self.assertNotIn("Devis en brouillon", titres)  # profil atelier : pas de cartes commerciales
        sans_droit = _utilisateur("c2", "Commercial")
        sans_droit.groups.get().permissions.clear()  # groupe par défaut sans aucun droit
        self.assertEqual(accueil.cartes_action(_requete(sans_droit))[0], [])  # rien d'urgent sans droits

    def test_direction_voit_tout(self):
        titres = {c["titre"] for c in sum(accueil.cartes_action(_requete(_utilisateur("d1", superuser=True))), [])}
        self.assertTrue({"Devis en brouillon", "Factures en retard", "Pièces en erreur d'import"} <= titres)

    def test_pastilles_menu_masquees_sans_droit(self):
        self.assertFalse(accueil.badge_factures(_requete(_utilisateur("x2"))))
        self.assertFalse(accueil.badge_devis(_requete(_utilisateur("s2", superuser=True))))  # rien à signaler : pas de pastille


class AccueilVuesTests(TestCase):
    def setUp(self):
        self.client.force_login(_utilisateur("boss", superuser=True))

    def test_accueil_affiche_flux_et_cartes(self):
        reponse = self.client.get(reverse("admin:index"))
        self.assertContains(reponse, "Aujourd'hui")
        self.assertContains(reponse, "flux-affaire")
        self.assertContains(reponse, "Chiffres du mois")

    def test_page_parametrage(self):
        reponse = self.client.get(reverse("parametrage"))
        self.assertContains(reponse, "Découpe")
        self.assertContains(reponse, "Formats de tôle")

    def test_parametrage_filtre_par_droits(self):
        u = _utilisateur("lecteur", perms=["decoupe.view_formattole"])
        noms = [lien["libelle"] for c in cartes(_requete(u)) for lien in c["liens"]]
        self.assertEqual(noms, ["Formats de tôle"])
        self.assertNotIn("Utilisateurs", noms)

    def test_recherche_globale_inclut_les_ecrans_de_reglage(self):
        titres = [e[0] for e in ecrans_pour_recherche(_requete(_utilisateur("s3", superuser=True)))]
        self.assertIn("Paramètres de coupe", titres)

    def test_menu_court_lien_parametrage(self):
        self.assertContains(self.client.get(reverse("admin:index")), reverse("parametrage"))

    def test_puces_listes(self):
        for nom, attendu in (("admin:chiffrage_devis_changelist", "Brouillons"), ("admin:chiffrage_commande_changelist", "En cours"), ("admin:facturation_facture_changelist", "En retard")):
            self.assertContains(self.client.get(reverse(nom)), "puces-liste")
            self.assertContains(self.client.get(reverse(nom)), attendu)

    def test_puce_active(self):
        reponse = self.client.get(reverse("admin:chiffrage_devis_changelist") + "?statut__exact=brouillon")
        self.assertContains(reponse, "puce puce-active")


class FicheDevisOngletsTests(TestCase):
    def test_script_onglets_charge_et_panneau_pieces_a_ses_etapes(self):
        from chiffrage.models import Devis
        from commercial.models import Tiers

        self.client.force_login(_utilisateur("boss2", superuser=True))
        client = Tiers.objects.create(code="CLI-ONG", raison_sociale="Client onglets")
        devis = Devis.objects.create(numero="DEV-ONG-1", client=client, date_creation=datetime.date(2026, 10, 1))
        reponse = self.client.get(reverse("admin:chiffrage_devis_change", args=[devis.pk]))
        self.assertContains(reponse, "devis_onglets")
        self.assertContains(reponse, 'id="lignes-group"')
        self.assertContains(reponse, "dp-etapes")
        self.assertContains(reponse, 'class="dp-panneau"')


class ACompleterTests(TestCase):
    def setUp(self):
        self.client.force_login(_utilisateur("boss3", superuser=True))

    def test_page_et_controles(self):
        from technique.models import PosteTravail

        PosteTravail.objects.create(nom="Poste sans tarif", mode_calcul="horaire")
        reponse = self.client.get(reverse("a_completer"))
        self.assertContains(reponse, "Postes de travail sans tarif")
        self.assertContains(reponse, "Poste sans tarif")
        self.assertContains(reponse, "Informations de la société incomplètes")

    def test_controle_disparait_une_fois_corrige(self):
        from technique.models import PosteTravail, TarifPoste

        from .a_completer import resultats

        poste = PosteTravail.objects.create(nom="Poste P", mode_calcul="horaire")
        requete = _requete(_utilisateur("s4", superuser=True))
        self.assertIn("poste_tarif", [r["controle"].cle for r in resultats(requete)])
        TarifPoste.objects.create(poste=poste, cout_horaire=50, date_debut=datetime.date(2026, 1, 1))
        self.assertNotIn("poste_tarif", [r["controle"].cle for r in resultats(requete)])

    def test_masque_sans_droit(self):
        from .a_completer import resultats

        self.assertEqual(resultats(_requete(_utilisateur("x5"))), [])

    def test_carte_accueil_et_menu(self):
        self.assertContains(self.client.get(reverse("admin:index")), reverse("a_completer"))

    def test_filtre_sans_poste_et_lien_de_la_carte(self):
        reponse = self.client.get(reverse("admin:decoupe_parametrecoupe_changelist") + "?sans_poste=oui")
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, "Sans poste")


class DateDuJourTests(TestCase):
    def test_date_pre_remplie_a_la_creation(self):
        from django.utils import timezone

        self.client.force_login(_utilisateur("adm-dj", superuser=True))
        aujourdhui = timezone.localdate().strftime("%Y-%m-%d")
        for nom, champ in (("admin:chiffrage_devis_add", "date_creation"), ("admin:chiffrage_commande_add", "date_commande"), ("admin:facturation_facture_add", "date_facturation")):
            reponse = self.client.get(reverse(nom))
            self.assertEqual(reponse.context["adminform"].form.initial[champ].strftime("%Y-%m-%d"), aujourdhui, nom)
        autre = self.client.get(reverse("admin:chiffrage_devis_add") + "?date_creation=2026-01-15")
        self.assertEqual(str(autre.context["adminform"].form.initial["date_creation"]), "2026-01-15")
