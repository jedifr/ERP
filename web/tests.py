import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from decoupe.models import FormatTole, ParametreCoupe, Plieuse, VitesseCoupe
from technique.models import Article, FamilleMatiere, Matiere, PosteTravail, TarifPoste

from .models import DemandeWeb, ReglageWeb
from .services.prix import ErreurPrixWeb, _contour_flan_boite, catalogue, chiffrer


class ConfigurateurBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        ParametreCoupe.objects.all().delete()  # base de coupe de la migration : un seul paramètre maîtrisé pour ces tests
        cls.poste = PosteTravail.objects.create(nom="Jet d'eau W", mode_calcul=PosteTravail.ModeCalcul.HORAIRE, temps_mise_en_place_min=10)
        cls.plieuse_poste = PosteTravail.objects.create(nom="Plieuse W", mode_calcul=PosteTravail.ModeCalcul.HORAIRE)
        for poste in (cls.poste, cls.plieuse_poste):
            TarifPoste.objects.create(poste=poste, cout_horaire=60, date_debut=datetime.date(2020, 1, 1))
        Plieuse.objects.filter(nom="Plieuse 2 m / 50 t").update(poste=cls.plieuse_poste)
        cls.matiere = Matiere.objects.create(nom="S235-W", densite=7.85, famille=FamilleMatiere.objects.get(nom="Acier"))
        parametre = ParametreCoupe.objects.create(
            matiere=cls.matiere, epaisseur_mm=2, poste=cls.poste, percage_stationnaire_hp_s=2, temporisation_pointage_s=0.5,
            percement_lineaire_mm=3, chevauchement_mm=3, vitesse_marquage_mm_min=5000, temporisation_marquage_s=1,
        )
        VitesseCoupe.objects.create(parametre=parametre, qualite=3, vitesse_haute_mm_min=900, vitesse_basse_mm_min=500, paliers=3,
                                    distance_acceleration_mm=3, distance_deceleration_mm=3)
        cls.tole = Article.objects.create(
            reference="S235-W-2 1500x3000", nature=Article.Nature.MATIERE_PREMIERE, matiere=cls.matiere, epaisseur=2,
            unite_cout=Article.UniteCout.SURFACE, cout_unitaire=20, format_tole=FormatTole.objects.get(largeur_mm=1500, longueur_mm=3000),
            vendu_en_ligne=True,
        )


class PrixWebTests(ConfigurateurBase):
    def test_piece_plane(self):
        r = chiffrer("S235-W", 2, 10, forme="rectangle", cotes={"largeur": 100, "hauteur": 50})
        self.assertEqual((r.statut, r.procede, r.nb_feuilles), ("ok", "jet_eau", 1))
        self.assertEqual((r.largeur_mm, r.hauteur_mm, r.poids_kg), (100, 50, 0.079))
        self.assertGreater(r.cout_matiere, 0)
        self.assertGreaterEqual(r.cout_decoupe, Decimal("10.00"))  # 10 min de mise en place à 60 €/h, plus la coupe
        self.assertEqual(r.cout_pliage, 0)
        self.assertEqual(r.prix_total_ht, (r.cout_revient * Decimal("1.2")).quantize(Decimal("0.01")))
        self.assertEqual(r.minimum_commande_ht, Decimal("60"))

    def test_profil_plie(self):
        r = chiffrer("S235-W", 2, 10, pliage={"forme": "profil", "faces": [50, 100], "angles": [90], "longueur": 60})
        self.assertEqual(r.statut, "ok")
        self.assertEqual((r.largeur_mm, r.hauteur_mm), (146.54, 60))  # flan = développé × longueur de pli
        self.assertEqual(r.cout_pliage, Decimal("7.50"))  # 5 min de réglage + 10 × 15 s (série) à 60 €/h
        self.assertEqual(r.pliage["ve"], "V12/30°")

    def test_boite_pliee(self):
        r = chiffrer("S235-W", 2, 5, pliage={"forme": "boite", "longueur": 200, "largeur": 150, "bords": [40, 40, 40, 40]})
        self.assertEqual(r.statut, "ok")
        self.assertEqual((r.largeur_mm, r.hauteur_mm), (273.08, 223.08))
        self.assertEqual(r.pliage["crans_mm"], [36.54] * 4)
        self.assertEqual(len(r.contour["exterieur"]), 12)  # quatre coins crantés
        self.assertEqual(r.cout_pliage, Decimal("20.00"))  # 2 réglages (10 min) + 5 pièces × 4 plis × 30 s (10 min) à 60 €/h

    def test_contour_sans_bord_d_un_cote(self):
        from shapely.geometry import Polygon

        self.assertAlmostEqual(Polygon(_contour_flan_boite(200, 223.08, [36.54, 0, 36.54, 0])).area, 200 * 223.08, places=3)  # comme un U : pas de cran
        self.assertAlmostEqual(Polygon(_contour_flan_boite(273.08, 223.08, [36.54] * 4)).area, 273.08 * 223.08 - 4 * 36.54 ** 2, places=3)

    def test_validation_et_refus(self):
        r = chiffrer("S235-W", 2, 1, pliage={"forme": "profil", "faces": [100, 40, 100], "angles": [90, 90], "longueur": 300})
        self.assertEqual(r.statut, "validation")
        self.assertIsNotNone(r.prix_total_ht)
        r = chiffrer("S235-W", 2, 1, pliage={"forme": "profil", "faces": [5, 100], "angles": [90], "longueur": 100})
        self.assertEqual((r.statut, r.prix_total_ht), ("refus", None))
        r = chiffrer("S235-W", 2, 501, forme="rectangle", cotes={"largeur": 100, "hauteur": 50})
        self.assertIn("demandez un devis", r.refus[0])
        r = chiffrer("S235-W", 2, 1, forme="rectangle", cotes={"largeur": 2000, "hauteur": 3500})
        self.assertIn("trop grande", r.refus[0])
        Plieuse.objects.update(poste=None)
        r = chiffrer("S235-W", 2, 1, pliage={"forme": "profil", "faces": [50, 100], "angles": [90], "longueur": 60})
        self.assertIn("demandez un devis", r.refus[0])
        self.assertIn("poste de travail", r.notes[-1])

    def test_demandes_mal_formees(self):
        for args, kw in (
            (("S235-W", 3, 1), {"forme": "rectangle", "cotes": {}}),  # épaisseur non vendue en ligne
            (("S235-W", 2, 0), {"forme": "rectangle", "cotes": {}}),
            (("S235-W", 2, 1), {}),
            (("S235-W", 2, 1), {"forme": "inconnue"}),
            (("S235-W", 2, 1), {"pliage": {"forme": "profil", "faces": [50], "angles": [], "longueur": 60}}),
            (("S235-W", 2, 1), {"pliage": {"forme": "boite", "longueur": "x"}}),
        ):
            with self.assertRaises(ErreurPrixWeb, msg=(args, kw)):
                chiffrer(*args, **kw)

    def test_messages_du_profil(self):
        for faces, angles, message in (([50], [90], "1 face saisie"), ([50, 50, 50], [90], "3 faces demandent 2 angle(s), 1 saisi(s)"), ([50, 50], [180], "entre 0 et 180°")):
            with self.assertRaisesMessage(ErreurPrixWeb, message):
                chiffrer("S235-W", 2, 1, pliage={"forme": "profil", "faces": faces, "angles": angles, "longueur": 50})

    def test_catalogue(self):
        c = catalogue()
        self.assertEqual(c["matieres"], [{"matiere": "S235-W", "famille": "Acier", "epaisseurs": [2.0]}])
        self.assertIn("rectangle", [f["cle"] for f in c["formes"]])
        self.assertEqual((c["minimum_commande_ht"], c["quantite_max"]), (60.0, 500))

    def test_marge_web_reglable(self):
        ReglageWeb.objects.filter(pk=ReglageWeb.charger().pk).update(taux_marge_pct=50)
        r = chiffrer("S235-W", 2, 10, forme="rectangle", cotes={"largeur": 100, "hauteur": 50})
        self.assertEqual(r.prix_total_ht, (r.cout_revient * Decimal("1.5")).quantize(Decimal("0.01")))


class ApiWebTests(ConfigurateurBase):
    @override_settings(WEB_CLE_API="")
    def test_fermee_sans_cle_configuree(self):
        self.assertEqual(self.client.get("/api/web/v1/catalogue/", HTTP_X_CLE_WEB="").status_code, 403)

    @override_settings(WEB_CLE_API="secret-web")
    def test_catalogue_et_prix(self):
        self.assertEqual(self.client.get("/api/web/v1/catalogue/", HTTP_X_CLE_WEB="mauvaise").status_code, 403)
        page = self.client.get("/api/web/v1/catalogue/", HTTP_X_CLE_WEB="secret-web")
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.json()["matieres"][0]["matiere"], "S235-W")
        page = self.client.post("/api/web/v1/prix/", {"matiere": "S235-W", "epaisseur": 2, "quantite": 10, "forme": "disque", "cotes": {"diametre": 100}},
                                content_type="application/json", HTTP_X_CLE_WEB="secret-web")
        self.assertEqual(page.status_code, 200, page.content)
        self.assertEqual(page.json()["statut"], "ok")
        self.assertIsInstance(page.json()["prix_total_ht"], float)
        for interne in ("cout_revient", "cout_matiere", "taux_marge_pct", "notes", "format_tole"):
            self.assertNotIn(interne, page.json())  # le client ne voit ni coûts ni marge
        page = self.client.post("/api/web/v1/prix/", {"matiere": "Or", "epaisseur": 2, "quantite": 1, "forme": "disque"},
                                content_type="application/json", HTTP_X_CLE_WEB="secret-web")
        self.assertEqual(page.status_code, 400)
        self.assertIn("pas vendu en ligne", page.json()["erreur"])


class ApercuConfigurateurTests(ConfigurateurBase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("web", "w@example.com", "pass-web-1234"))

    def test_essai_du_prix(self):
        self.assertContains(self.client.get("/admin/configurateur-web/"), "S235-W 2 mm")
        page = self.client.post("/admin/configurateur-web/", {
            "tole": "S235-W|2", "quantite": "5", "type_piece": "boite", "longueur_fond": "200", "largeur_fond": "150", "bords": "40 ; 40 ; 40 ; 40",
        })
        self.assertContains(page, "Prix ferme")
        self.assertContains(page, "273,08 × 223,08")
        page = self.client.post("/admin/configurateur-web/", {"tole": "S235-W|2", "quantite": "2", "type_piece": "forme", "forme": "disque", "cote_diametre": "80"})
        self.assertContains(page, "Disque Ø80")
        self.assertContains(self.client.get("/admin/parametrage/"), "Essai du configurateur")
        self.assertEqual(self.client.get("/admin/web/reglageweb/").status_code, 200)


class DemandeWebTests(ConfigurateurBase):
    CLIENT = {"nom": "Jeanne Martin", "societe": "Atelier Martin", "email": "jeanne@example.com", "adresse": "1 rue des Forges"}
    RECT = {"matiere": "S235-W", "epaisseur": 2, "quantite": 10, "forme": "rectangle", "cotes": {"largeur": 100, "hauteur": 50}, "prix_total_ht": 0.01}

    @override_settings(WEB_CLE_API="secret-web")
    def test_panier_recu_avec_prix_recalcules(self):
        u = {"matiere": "S235-W", "epaisseur": 2, "quantite": 1, "pliage": {"forme": "profil", "faces": [100, 40, 100], "angles": [90, 90], "longueur": 300}}
        page = self.client.post("/api/web/v1/demandes/", {"client": self.CLIENT, "lignes": [self.RECT, u]}, content_type="application/json", HTTP_X_CLE_WEB="secret-web")
        self.assertEqual(page.status_code, 201, page.content)
        demande = DemandeWeb.objects.get(numero=page.json()["numero"])
        self.assertRegex(demande.numero, r"^WEB\d{4}-0001$")
        attendu = chiffrer("S235-W", 2, 10, forme="rectangle", cotes={"largeur": 100, "hauteur": 50}).prix_total_ht
        self.assertEqual(demande.lignes[0]["prix_total_ht"], float(attendu))  # le prix du navigateur est ignoré
        self.assertNotIn("prix_total_ht", demande.lignes[0]["demande"])
        self.assertTrue(demande.a_valider)  # le U a des ailes plus longues que sa largeur
        self.assertEqual(demande.total_ht, max(demande.total_pieces_ht, Decimal("60")))
        second = self.client.post("/api/web/v1/demandes/", {"client": self.CLIENT, "lignes": [self.RECT]}, content_type="application/json", HTTP_X_CLE_WEB="secret-web")
        self.assertTrue(second.json()["numero"].endswith("-0002"))
        self.assertEqual(second.json()["total_ht"], 60.0)  # minimum de commande

    @override_settings(WEB_CLE_API="secret-web")
    def test_demandes_refusees(self):
        refus = {"matiere": "S235-W", "epaisseur": 2, "quantite": 1, "pliage": {"forme": "profil", "faces": [5, 100], "angles": [90], "longueur": 100}}
        for corps, message in (
            ({"client": {"nom": "X"}, "lignes": [self.RECT]}, "e-mail"),
            ({"client": {**self.CLIENT, "email": "pas-un-mail"}, "lignes": [self.RECT]}, "pas valide"),
            ({"client": self.CLIENT, "lignes": []}, "vide"),
            ({"client": self.CLIENT, "lignes": [self.RECT] * 51}, "50"),
            ({"client": self.CLIENT, "lignes": [refus]}, "Pièce 1"),
        ):
            page = self.client.post("/api/web/v1/demandes/", corps, content_type="application/json", HTTP_X_CLE_WEB="secret-web")
            self.assertEqual(page.status_code, 400)
            self.assertIn(message, page.json()["erreur"])
        self.assertFalse(DemandeWeb.objects.exists())

    def test_admin(self):
        self.client.force_login(get_user_model().objects.create_superuser("dw", "dw@example.com", "pass-dw-1234"))
        from .services.demandes import enregistrer_demande

        demande = enregistrer_demande({"client": self.CLIENT, "lignes": [self.RECT]})
        self.assertContains(self.client.get("/admin/web/demandeweb/"), demande.numero)
        page = self.client.get(f"/admin/web/demandeweb/{demande.pk}/change/")
        self.assertContains(page, "Rectangle")
        self.assertContains(page, "1 rue des Forges")
