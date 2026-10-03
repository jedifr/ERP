from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from chiffrage.models import Commande, Devis, OrdreFabrication
from commercial.models import Adresse, Tiers
from decoupe.models import PieceDecoupe
from facturation.models import Facture
from stock.models import AlerteStock
from technique.models import Article

from .dashboard import dashboard_callback
from .layout import global_callback


class ToggleLargeurPleinePageTests(TestCase):
    def test_global_callback_active_la_pleine_largeur(self):
        self.assertEqual(global_callback(request=None), {"is_fullwidth": "1"})

    def test_page_admin_sans_conteneur_largeur_plafonnee(self):
        User = get_user_model()
        user = User.objects.create_superuser("largeur-admin", "l@example.com", "pass1234")
        self.client.force_login(user)
        response = self.client.get(reverse("admin:index"))
        # Le marqueur Unfold d'un contenu plafonné en largeur (classe Tailwind
        # "container" autour de #content) ne doit plus apparaître — voir
        # comptes.layout.global_callback (UNFOLD["GLOBAL_CALLBACK"]).
        self.assertNotContains(response, 'id="content" class="container')


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
            est_facturation=True,
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
            numero="CDE-DASH", devis=devis_valide, client=devis_valide.client, date_commande=aujourdhui,
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

        PieceDecoupe.objects.create(
            nom="Flasque dashboard",
            fichier_source="decoupe/sources/dashboard-test.dxf",
            statut=PieceDecoupe.Statut.OK,
        )
        PieceDecoupe.objects.create(
            nom="Import en échec",
            fichier_source="decoupe/sources/dashboard-test-erreur.dxf",
            statut=PieceDecoupe.Statut.ERREUR,
        )

    def test_kpis_reussissent_les_bons_compteurs(self):
        context = dashboard_callback(request=None, context={})
        kpis = {kpi["title"]: kpi for kpi in context["kpis"]}

        self.assertEqual(kpis["Devis en brouillon"]["value"], 1)
        self.assertEqual(kpis["Devis validés"]["value"], 1)
        self.assertEqual(kpis["CA facturé"]["value"], "1 000 €")
        self.assertEqual(kpis["Alertes de stock"]["value"], 1)
        self.assertTrue(kpis["Alertes de stock"]["attention"])
        self.assertEqual(kpis["Ordres de fabrication"]["value"], 1)
        self.assertEqual(kpis["Pièces à découper"]["value"], 1)


class GroupesMetierTests(TestCase):
    """Les rôles par défaut doivent être réellement utilisables dans l'admin : les
    listes à autocomplétion répondent 403 sans la permission « voir » du modèle lié."""

    AUTOCOMPLETIONS = {
        "Commercial": [
            ("chiffrage", "devis", "client"), ("chiffrage", "devis", "adresse_livraison"),
            ("chiffrage", "devis", "contact"), ("chiffrage", "devisligne", "article"),
            ("chiffrage", "commande", "client"), ("chiffrage", "commande", "devis"),
            ("chiffrage", "commandeligne", "article"),
        ],
        "Magasinier": [
            ("stock", "lot", "article"), ("stock", "lot", "emplacement"),
            ("stock", "mouvementstock", "lot"), ("stock", "transfert", "lot_source"),
            ("stock", "transfert", "emplacement_cible"), ("stock", "inventaireligne", "lot"),
        ],
        "Atelier": [("chiffrage", "operationof", "poste")],
        "Méthodes et bureau d'études": [
            ("technique", "article", "matiere"), ("technique", "gamme", "article"), ("technique", "gamme", "poste"),
            ("technique", "nomenclature", "article_parent"), ("technique", "tarifposte", "poste"),
        ],
        "Achats": [
            ("achats", "commandefournisseur", "fournisseur"), ("achats", "articlefournisseur", "article"),
            ("achats", "lignecommandefournisseur", "commande_ligne_client"),
            ("achats", "lignecommandefournisseur", "poste_gestion"),
            ("achats", "reception", "commande_fournisseur"), ("achats", "facturefournisseur", "commande_fournisseur"),
        ],
        "Comptabilité": [
            ("comptabilite", "articlecomptevente", "article"), ("comptabilite", "articlecomptevente", "compte_vente"),
            ("comptabilite", "tierscomptecomptable", "tiers"), ("comptabilite", "comptecomptable", "compte_parent"),
        ],
        "Facturation": [("facturation", "facture", "commande")],
    }

    def _utilisateur(self, groupe):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Group

        utilisateur = get_user_model().objects.create_user(
            f"u-{groupe.replace(' ', '-')}", "g@example.com", "pass1234", is_staff=True
        )
        utilisateur.groups.add(Group.objects.get(name=groupe))
        return utilisateur

    def test_autocompletions_accessibles_pour_chaque_role(self):
        for groupe, cas in self.AUTOCOMPLETIONS.items():
            self.client.force_login(self._utilisateur(groupe))
            for app, modele, champ in cas:
                reponse = self.client.get(
                    "/admin/autocomplete/", {"app_label": app, "model_name": modele, "field_name": champ, "term": ""}
                )
                self.assertEqual(reponse.status_code, 200, f"{groupe} : {app}.{modele}.{champ}")

    def test_responsables_heritent_des_roles_de_base(self):
        from django.contrib.auth.models import Group

        def codes(nom):
            return set(
                f"{p.content_type.app_label}.{p.codename}" for p in Group.objects.get(name=nom).permissions.select_related("content_type")
            )

        self.assertLessEqual(codes("Magasinier"), codes("Responsable stock"))
        self.assertLessEqual(codes("Commercial"), codes("Responsable commercial"))
        self.assertIn("stock.valider_inventaire", codes("Responsable stock"))
        self.assertNotIn("stock.valider_inventaire", codes("Magasinier"))
        self.assertNotIn("stock.annuler_mouvement", codes("Magasinier"))

    def test_synchroniser_ajoute_sans_jamais_retirer(self):
        from django.contrib.auth.models import Group, Permission
        from django.core.management import call_command

        groupe = Group.objects.get(name="Magasinier")
        retiree = Permission.objects.get(content_type__app_label="stock", codename="add_transfert")
        groupe.permissions.remove(retiree)
        supplementaire = Permission.objects.get(content_type__app_label="chiffrage", codename="valider_devis")
        groupe.permissions.add(supplementaire)
        call_command("synchroniser_groupes", stdout=__import__("io").StringIO())
        self.assertTrue(groupe.permissions.filter(pk=retiree.pk).exists())
        self.assertTrue(groupe.permissions.filter(pk=supplementaire.pk).exists())


class CodesDesGroupesTests(TestCase):
    def test_chaque_permission_declaree_existe(self):
        """Une faute de frappe dans un code serait ignorée en silence : le groupe
        resterait sans la permission voulue."""
        from django.contrib.auth.models import Permission

        from .groupes import GROUPES_PAR_DEFAUT

        existantes = {
            f"{app}.{code}"
            for app, code in Permission.objects.values_list("content_type__app_label", "codename")
        }
        manquantes = {
            (groupe, code)
            for groupe, codes in GROUPES_PAR_DEFAUT.items()
            for code in codes
            if code not in existantes
        }
        self.assertEqual(manquantes, set())


class ConnexionDirecteTests(TestCase):
    def test_connexion_sans_next_arrive_sur_laccueil_de_ladmin(self):
        get_user_model().objects.create_superuser("direct", "d@example.com", "pass1234")
        r = self.client.post("/admin/login/", {"username": "direct", "password": "pass1234"})
        self.assertRedirects(r, "/admin/", fetch_redirect_response=False)


class ApiSecuriseeParDefautTests(TestCase):
    """D-AD-02 : aucune route d'API ne doit répondre à un compte sans permission."""

    APPS = ["technique", "decoupe", "commercial", "chiffrage", "stock", "facturation", "comptabilite", "achats", "soustraitance"]

    def _prefixes(self):
        import importlib

        prefixes = []
        for app in self.APPS:
            module = importlib.import_module(f"{app}.urls")
            prefixes += [prefixe for prefixe, _viewset, _nom in module.router.registry]
        return prefixes

    def test_chaque_route_refuse_un_compte_sans_permission(self):
        self.client.force_login(get_user_model().objects.create_user("api-nu", "n@example.com", "pass1234", is_staff=True))
        prefixes = self._prefixes()
        self.assertGreater(len(prefixes), 40)  # garde-fou : la liste a bien été construite
        ouvertes = [p for p in prefixes if self.client.get(f"/api/v1/{p}/").status_code != 403]
        self.assertEqual(ouvertes, [])

    def test_chaque_route_refuse_lecriture_sans_permission(self):
        self.client.force_login(get_user_model().objects.create_user("api-nu2", "n@example.com", "pass1234", is_staff=True))
        ouvertes = [p for p in self._prefixes() if self.client.post(f"/api/v1/{p}/", data={}, content_type="application/json").status_code != 403]
        self.assertEqual(ouvertes, [])

    def test_visiteur_non_connecte_refuse_partout(self):
        for prefixe in self._prefixes():
            self.assertIn(self.client.get(f"/api/v1/{prefixe}/").status_code, (401, 403), prefixe)

    def test_superutilisateur_garde_lacces(self):
        self.client.force_login(get_user_model().objects.create_superuser("api-su", "s@example.com", "pass1234"))
        for prefixe in self._prefixes():
            self.assertEqual(self.client.get(f"/api/v1/{prefixe}/").status_code, 200, prefixe)

    def test_pilotage_reserve_aux_comptes_habilites(self):
        from django.contrib.auth.models import Group

        urls = ["/api/v1/pilotage/marge-reelle/OF-X/", "/api/v1/pilotage/taux-charge/P/?date_debut=2026-01-01&date_fin=2026-01-31"]
        simple = get_user_model().objects.create_user("pilot-nu", "p@example.com", "pass1234", is_staff=True)
        self.client.force_login(simple)
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 403, url)
        direction = get_user_model().objects.create_user("pilot-dir", "d@example.com", "pass1234", is_staff=True)
        direction.groups.add(Group.objects.get(name="Direction"))
        self.client.force_login(direction)
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 404, url)  # autorisé : l'objet n'existe simplement pas


class MediaProtegeTests(TestCase):
    """Les plans clients déposés ne doivent pas être lisibles sans connexion."""

    def setUp(self):
        import tempfile

        from django.test import override_settings

        self._dossier = tempfile.TemporaryDirectory()
        (__import__("pathlib").Path(self._dossier.name) / "decoupe").mkdir()
        (__import__("pathlib").Path(self._dossier.name) / "decoupe" / "plan.dxf").write_text("PLAN CONFIDENTIEL")
        (__import__("pathlib").Path(self._dossier.name) / "autre.txt").write_text("divers")
        self._override = override_settings(MEDIA_ROOT=self._dossier.name)
        self._override.enable()
        self.addCleanup(self._override.disable)
        self.addCleanup(self._dossier.cleanup)

    def test_visiteur_non_connecte_redirige_vers_la_connexion(self):
        r = self.client.get("/media/decoupe/plan.dxf")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/admin/login/", r.url)

    def test_compte_sans_droit_sur_les_pieces_refuse(self):
        self.client.force_login(get_user_model().objects.create_user("media-nu", "m@example.com", "pass1234", is_staff=True))
        self.assertEqual(self.client.get("/media/decoupe/plan.dxf").status_code, 403)

    def test_role_methodes_peut_lire_les_plans(self):
        from django.contrib.auth.models import Group

        utilisateur = get_user_model().objects.create_user("media-bet", "m@example.com", "pass1234", is_staff=True)
        utilisateur.groups.add(Group.objects.get(name="Méthodes et bureau d'études"))
        self.client.force_login(utilisateur)
        r = self.client.get("/media/decoupe/plan.dxf")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(b"".join(r.streaming_content), b"PLAN CONFIDENTIEL")

    def test_autres_dossiers_reserves_au_personnel(self):
        self.client.force_login(get_user_model().objects.create_user("media-ext", "m@example.com", "pass1234"))
        self.assertEqual(self.client.get("/media/autre.txt").status_code, 403)
        self.client.force_login(get_user_model().objects.create_user("media-staff", "m2@example.com", "pass1234", is_staff=True))
        self.assertEqual(self.client.get("/media/autre.txt").status_code, 200)

    def test_pas_de_sortie_du_dossier(self):
        self.client.force_login(get_user_model().objects.create_superuser("media-su", "s@example.com", "pass1234"))
        self.assertIn(self.client.get("/media/../settings.py").status_code, (400, 404))


class DiagnosticSecuriteTests(TestCase):
    CLE_FORTE = "k" * 10 + "Zx9" * 10 + "q8"

    def _titres(self, **reglages):
        from django.test import override_settings

        from .securite import diagnostics

        with override_settings(**reglages):
            return {c.titre for c in diagnostics()}

    def _reglages_sains(self, **surcharge):
        base = dict(
            DEBUG=False, SECRET_KEY=self.CLE_FORTE, ALLOWED_HOSTS=["erp.local"],
            SESSION_COOKIE_SECURE=True, CSRF_COOKIE_SECURE=True,
            DATABASES={"default": {**__import__("django.conf", fromlist=["settings"]).settings.DATABASES["default"], "PASSWORD": "Un-vrai-mot-de-passe-9"}},
        )
        base.update(surcharge)
        return base

    def test_configuration_saine_sans_constat(self):
        self.assertEqual(self._titres(**self._reglages_sains()), set())

    def test_debug_est_critique(self):
        self.assertIn("Mode DEBUG activé", self._titres(**self._reglages_sains(DEBUG=True)))

    def test_cle_par_defaut_ou_courte_est_critique(self):
        for cle in ("django-insecure-change-me-in-production", "change-me-in-production", "trop-courte"):
            self.assertIn("Clé secrète par défaut ou trop courte", self._titres(**self._reglages_sains(SECRET_KEY=cle)), cle)

    def test_hotes_et_mot_de_passe_et_cookies(self):
        titres = self._titres(**self._reglages_sains(ALLOWED_HOSTS=["*"], SESSION_COOKIE_SECURE=False))
        self.assertIn("ALLOWED_HOSTS accepte tous les noms", titres)
        self.assertIn("Cookies de session non marqués « sécurisés »", titres)

    def test_mot_de_passe_de_base_par_defaut(self):
        from django.conf import settings

        reglages = self._reglages_sains()
        reglages["DATABASES"] = {"default": {**settings.DATABASES["default"], "PASSWORD": "erp_dev_password"}}
        self.assertIn("Mot de passe de base de données par défaut", self._titres(**reglages))

    def test_debug_est_faux_par_defaut_sans_variable(self):
        import os
        import subprocess
        import sys

        env = {k: v for k, v in os.environ.items() if k != "DJANGO_DEBUG"}
        sortie = subprocess.run(
            [sys.executable, "-c", "from django.conf import settings; import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings'); print(settings.DEBUG)"],
            capture_output=True, text=True, env=env, cwd=str(__import__('django.conf', fromlist=['settings']).settings.BASE_DIR),
        )
        self.assertEqual(sortie.stdout.strip(), "False", sortie.stderr)

    def test_bandeau_visible_des_seuls_superutilisateurs(self):
        from django.test import override_settings

        with override_settings(DEBUG=False, SECRET_KEY="trop-courte"):
            self.client.force_login(get_user_model().objects.create_superuser("sec-su", "s@example.com", "pass1234"))
            self.assertContains(self.client.get("/admin/"), "Clé secrète par défaut ou trop courte")
            self.client.force_login(get_user_model().objects.create_user("sec-staff", "t@example.com", "pass1234", is_staff=True))
            self.assertNotContains(self.client.get("/admin/"), "Clé secrète par défaut")

    def test_commande_verifier_securite_code_de_sortie(self):
        import io

        from django.core.management import call_command
        from django.test import override_settings

        with override_settings(**self._reglages_sains()):
            sortie = io.StringIO()
            call_command("verifier_securite", stdout=sortie)
            self.assertIn("aucun constat", sortie.getvalue())
        with override_settings(**self._reglages_sains(DEBUG=True)):
            with self.assertRaises(SystemExit) as cm:
                call_command("verifier_securite", stdout=io.StringIO())
            self.assertEqual(cm.exception.code, 1)

    def test_mot_de_passe_trop_court_refuse(self):
        from django.contrib.auth.password_validation import validate_password
        from django.core.exceptions import ValidationError

        with self.assertRaises(ValidationError):
            validate_password("Abc9xyz!1")  # 9 caractères
        validate_password("Rx7!pLm29qZw")


class LimitationConnexionTests(TestCase):
    """D-AD-04 : 5 échecs en 15 minutes verrouillent l'identifiant (même avec le bon mot de passe)."""

    def setUp(self):
        self.utilisateur = get_user_model().objects.create_user("marc", "m@example.com", "Bon-mot-de-passe-1", is_staff=True)

    def _tenter(self, identifiant="marc", mot_de_passe="mauvais", **extra):
        return self.client.post("/admin/login/", {"username": identifiant, "password": mot_de_passe}, **extra)

    def _echouer(self, n, identifiant="marc"):
        for _ in range(n):
            self._tenter(identifiant)

    def test_quatre_echecs_ne_bloquent_pas(self):
        self._echouer(4)
        self.assertEqual(self._tenter(mot_de_passe="Bon-mot-de-passe-1").status_code, 302)

    def test_cinq_echecs_bloquent_meme_avec_le_bon_mot_de_passe(self):
        self._echouer(5)
        r = self._tenter(mot_de_passe="Bon-mot-de-passe-1")
        self.assertEqual(r.status_code, 200)  # reste sur la page de connexion
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_message_explicite_de_verrouillage(self):
        self._echouer(5)
        r = self._tenter(mot_de_passe="Bon-mot-de-passe-1")
        self.assertContains(r, "compte verrouillé")

    def test_les_refus_pendant_le_verrou_ne_prolongent_pas_le_blocage(self):
        from .models import EvenementConnexion

        self._echouer(5)
        self._echouer(10)  # tentatives pendant le verrou
        self.assertEqual(EvenementConnexion.objects.filter(identifiant="marc", type_evenement="echec").count(), 5)
        self.assertEqual(EvenementConnexion.objects.filter(identifiant="marc", type_evenement="bloque").count(), 10)

    def test_le_verrou_tombe_apres_la_fenetre(self):
        import datetime

        from django.utils import timezone

        from .models import EvenementConnexion

        self._echouer(5)
        EvenementConnexion.objects.filter(type_evenement="echec").update(date=timezone.now() - datetime.timedelta(minutes=16))
        self.assertEqual(self._tenter(mot_de_passe="Bon-mot-de-passe-1").status_code, 302)

    def test_un_succes_remet_le_compteur_a_zero(self):
        self._echouer(4)
        self._tenter(mot_de_passe="Bon-mot-de-passe-1")
        self.client.logout()
        self._echouer(4)  # 4 nouveaux échecs seulement : pas de blocage
        self.assertEqual(self._tenter(mot_de_passe="Bon-mot-de-passe-1").status_code, 302)

    def test_autres_comptes_non_affectes_et_casse_ignoree(self):
        get_user_model().objects.create_user("lea", "l@example.com", "Autre-mot-de-passe-2", is_staff=True)
        self._echouer(3)
        self._echouer(2, identifiant="MARC")  # même compte, autre casse
        self.assertEqual(self._tenter(mot_de_passe="Bon-mot-de-passe-1").status_code, 200)
        self.assertEqual(self._tenter("lea", "Autre-mot-de-passe-2").status_code, 302)

    def test_identifiant_inexistant_traite_pareil(self):
        from .connexions import compte_verrouille

        self._echouer(5, identifiant="fantome")
        self.assertTrue(compte_verrouille("fantome"))

    def test_api_en_authentification_de_base_est_protegee_aussi(self):
        import base64

        get_user_model().objects.filter(username="marc").update(is_superuser=True)
        entete = lambda mdp: {"HTTP_AUTHORIZATION": "Basic " + base64.b64encode(f"marc:{mdp}".encode()).decode()}
        for _ in range(5):
            self.client.get("/api/v1/articles/", **entete("faux"))
        self.assertEqual(self.client.get("/api/v1/articles/", **entete("Bon-mot-de-passe-1")).status_code, 403)

    def test_journal_enregistre_succes_et_adresse_ip(self):
        from .models import EvenementConnexion

        self._tenter(mot_de_passe="Bon-mot-de-passe-1", HTTP_X_FORWARDED_FOR="203.0.113.7, 10.0.0.1")
        evenement = EvenementConnexion.objects.get(type_evenement="succes")
        self.assertEqual((evenement.identifiant, evenement.adresse_ip), ("marc", "203.0.113.7"))

    def test_adresse_ip_invalide_ignoree(self):
        from .models import EvenementConnexion

        self._tenter(HTTP_X_FORWARDED_FOR="pas-une-ip")
        self.assertIsNone(EvenementConnexion.objects.get(type_evenement="echec").adresse_ip)

    def test_minutes_restantes_et_purge(self):
        import datetime

        from django.utils import timezone

        from .connexions import enregistrer, minutes_restantes
        from .models import EvenementConnexion

        self.assertEqual(minutes_restantes("marc"), 0)
        self._echouer(5)
        self.assertTrue(1 <= minutes_restantes("marc") <= 15)
        EvenementConnexion.objects.update(date=timezone.now() - datetime.timedelta(days=200))
        enregistrer("quelquun", "succes")
        self.assertEqual(EvenementConnexion.objects.count(), 1)  # l'ancien historique est purgé

    def test_deblocage_manuel_par_un_administrateur(self):
        from .models import EvenementConnexion

        self._echouer(5)
        admin_user = get_user_model().objects.create_superuser("root", "r@example.com", "Admin-mot-de-passe-3")
        self.client.force_login(admin_user)
        ids = list(EvenementConnexion.objects.filter(identifiant="marc").values_list("pk", flat=True)[:1])
        self.client.post("/admin/comptes/evenementconnexion/", {"action": "action_debloquer", "_selected_action": ids}, follow=True)
        self.client.logout()
        self.assertEqual(self._tenter(mot_de_passe="Bon-mot-de-passe-1").status_code, 302)

    def test_journal_en_consultation_seule_et_reserve_aux_habilites(self):
        from .models import EvenementConnexion

        self._echouer(1)
        evenement = EvenementConnexion.objects.first()
        self.client.force_login(get_user_model().objects.create_superuser("root2", "r@example.com", "Admin-mot-de-passe-3"))
        self.assertEqual(self.client.get("/admin/comptes/evenementconnexion/").status_code, 200)
        self.assertEqual(self.client.get("/admin/comptes/evenementconnexion/add/").status_code, 403)
        self.assertEqual(self.client.post(f"/admin/comptes/evenementconnexion/{evenement.pk}/delete/", {"post": "yes"}).status_code, 403)
        simple = get_user_model().objects.create_user("simple-j", "s@example.com", "pass-mot-de-passe-4", is_staff=True)
        self.client.force_login(simple)
        self.assertEqual(self.client.get("/admin/comptes/evenementconnexion/").status_code, 403)


class JournalAffichageTests(TestCase):
    def test_colonne_verrouille_en_clair(self):
        for _ in range(5):
            self.client.post("/admin/login/", {"username": "cible", "password": "x"})
        self.client.force_login(get_user_model().objects.create_superuser("root3", "r@example.com", "Admin-mot-de-passe-3"))
        page = self.client.get("/admin/comptes/evenementconnexion/?q=cible")
        self.assertContains(page, "VERROUILLÉ")
