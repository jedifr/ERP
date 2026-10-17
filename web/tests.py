import datetime
import io
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
        self.assertEqual((r.taux_tva, r.prix_total_ttc), (Decimal("20"), (r.prix_total_ht * Decimal("1.2")).quantize(Decimal("0.01"))))
        self.assertEqual(r.prix_unitaire_ttc, (r.prix_unitaire_ht * Decimal("1.2")).quantize(Decimal("0.01")))

    def test_tva_du_referentiel(self):
        from commercial.models import TauxTVA

        TauxTVA.objects.update(est_defaut=False)
        TauxTVA.objects.create(nom="DOM", taux=Decimal("8.5"), est_defaut=True)
        r = chiffrer("S235-W", 2, 10, forme="rectangle", cotes={"largeur": 100, "hauteur": 50})
        self.assertEqual(r.prix_total_ttc, (r.prix_total_ht * Decimal("1.085")).quantize(Decimal("0.01")))
        self.assertEqual(catalogue()["taux_tva"], 8.5)

    def test_rayon_ou_chanfrein(self):
        with self.assertRaisesMessage(ErreurPrixWeb, "pas les deux"):  # l'ERP refuse même si le site laissait passer
            chiffrer("S235-W", 2, 10, forme="rectangle", cotes={"largeur": 100, "hauteur": 50, "rayon_angle": 5, "chanfrein": 5})

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
        lignes = {l["nom"]: l for l in r.lignes_pli}
        self.assertEqual(sorted(lignes), ["Bord A", "Bord B", "Bord C", "Bord D"])
        a = lignes["Bord A"]  # au milieu de la bande pliée, de cran à cran
        self.assertTrue(36.54 < a["p1"][1] == a["p2"][1] < 40)  # bord de 40 : reculé de (ri + e), plus la moitié de l'arc
        self.assertEqual((a["p1"][0], a["p2"][0], a["angle"], a["sens"]), (36.54, 273.08 - 36.54, 90, "haut"))
        self.assertAlmostEqual(lignes["Bord C"]["p1"][1], 223.08 - a["p1"][1], places=2)
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
        self.assertGreater(page.json()["prix_total_ttc"], page.json()["prix_total_ht"])
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


class ValiderDemandeTests(ConfigurateurBase):
    """« Valider » : client retrouvé ou créé, devis au prix de la demande, demande traitée."""

    CLIENT = {"nom": "Jeanne Martin", "societe": "Atelier Martin", "email": "Jeanne@Example.com", "telephone": "0601020304",
              "adresse": "1 rue des Forges", "code_postal": "67000", "ville": "Strasbourg"}
    RECT = DemandeWebTests.RECT
    U = {"matiere": "S235-W", "epaisseur": 2, "quantite": 3, "pliage": {"forme": "profil", "faces": [40, 60, 40], "angles": [90, 90], "longueur": 200}}

    def setUp(self):
        from .services.demandes import enregistrer_demande

        self.enregistrer = enregistrer_demande
        self.demande = enregistrer_demande({"client": self.CLIENT, "lignes": [self.RECT, self.U]})

    def test_client_et_devis_crees(self):
        from commercial.models import Tiers

        from .services.conversion import ErreurConversion, creer_devis

        devis, cree, remarques = creer_devis(self.demande)
        self.assertTrue(cree)
        client = devis.client
        self.assertEqual((client.raison_sociale, client.type_tiers), ("Atelier Martin", Tiers.TypeTiers.CLIENT))
        contact = client.contacts.get()
        self.assertEqual((contact.prenom, contact.nom, contact.telephones.get().numero), ("Jeanne", "Martin", "0601020304"))
        adresse = client.adresses.get()
        self.assertEqual((adresse.adresse, adresse.code_postal, adresse.ville, adresse.pays_id), ("1 rue des Forges", "67000", "Strasbourg", "FR"))
        self.assertTrue(adresse.est_livraison and adresse.est_facturation)
        self.assertEqual((devis.adresse_livraison, devis.contact, devis.reference), (adresse, contact, f"Demande web {self.demande.numero}"))
        lignes = list(devis.lignes.order_by("ordre", "id"))
        self.assertEqual([l.quantite for l in lignes], [10, 3, 1])  # + le complément au minimum de commande
        for ligne, web in zip(lignes, self.demande.lignes):
            self.assertEqual(ligne.prix_vente_total, Decimal(str(web["prix_total_ht"])))  # prix annoncé au client
        piece, flan = devis.pieces_decoupe.order_by("id")  # le rectangle et le flan du U : DXF de fabrication joints au devis
        self.assertEqual((piece.matiere_id, piece.epaisseur, piece.quantite, piece.tole), ("S235-W", 2, 10, self.tole))
        self.assertTrue(piece.fichier_source.name.endswith(".dxf"))
        self.assertEqual(piece.article, lignes[0].article)
        self.assertTrue(lignes[0].article.gamme_etapes.exists())  # gamme de découpe alimentée (le paramètre a un poste)
        self.assertIn("pliage sur", remarques[1])  # le U : flan découpé, puis plié sur la plieuse retenue
        self.assertEqual((flan.article, lignes[1].article.pliage.faces_mm), (lignes[1].article, [40, 60, 40]))
        self.assertEqual(len(flan.pliage_json["traits"]), 2)  # les deux lignes de pli du U, sur leur calque
        self.assertEqual([e.origine for e in lignes[1].article.gamme_etapes.order_by("ordre")], ["decoupe", "pliage"])
        self.demande.refresh_from_db()
        self.assertEqual((self.demande.devis, self.demande.statut), (devis, DemandeWeb.Statut.TRAITEE))
        with self.assertRaisesMessage(ErreurConversion, "déjà son devis"):
            creer_devis(self.demande)

    def test_minimum_de_commande_en_ligne(self):
        from .services.conversion import REFERENCE_MINIMUM, creer_devis

        petite = self.enregistrer({"client": self.CLIENT, "lignes": [{**self.RECT, "quantite": 1}]})
        self.assertGreater(petite.total_ht, petite.total_pieces_ht)
        devis, _, remarques = creer_devis(petite)
        ligne = devis.lignes.get(article__reference=REFERENCE_MINIMUM)
        self.assertEqual(ligne.prix_vente_total, petite.total_ht - petite.total_pieces_ht)
        self.assertEqual(sum(l.prix_vente_total for l in devis.lignes.all()), petite.total_ht)
        self.assertIn("Minimum de commande", remarques[-1])

    def test_ordres_de_fabrication_a_la_commande(self):
        from chiffrage.models import Devis
        from chiffrage.production import lancer_en_production

        from .services.conversion import creer_devis

        devis, _, _ = creer_devis(self.demande)
        Devis.objects.filter(pk=devis.pk).update(statut=Devis.Statut.VALIDE)
        with self.captureOnCommitCallbacks(execute=True):
            commande = lancer_en_production(Devis.objects.get(pk=devis.pk))
        self.assertEqual(commande.ordres_fabrication.count(), 2)  # le rectangle et le U, pas le minimum de commande

    def test_client_retrouve_par_email(self):
        from .services.conversion import creer_devis

        premier, _, _ = creer_devis(self.demande)
        autre = self.enregistrer({"client": {**self.CLIENT, "email": "jeanne@example.com", "societe": ""}, "lignes": [self.RECT]})
        second, cree, _ = creer_devis(autre)
        self.assertFalse(cree)
        self.assertEqual(second.client, premier.client)
        self.assertEqual(second.client.adresses.count(), 1)  # même adresse : pas de doublon
        self.assertNotEqual(second.pk, premier.pk)

    def test_bouton_valider(self):
        self.client.force_login(get_user_model().objects.create_superuser("vw", "vw@example.com", "pass-vw-1234"))
        page = self.client.get(f"/admin/web/demandeweb/{self.demande.pk}/change/")
        self.assertContains(page, "Valider : créer le client et le devis")
        page = self.client.get(f"/admin/web/demandeweb/{self.demande.pk}/valider/")
        self.demande.refresh_from_db()
        self.assertRedirects(page, f"/admin/chiffrage/devis/{self.demande.devis_id}/change/", fetch_redirect_response=False)
        page = self.client.get(f"/admin/web/demandeweb/{self.demande.pk}/change/")
        self.assertNotContains(page, "Valider : créer le client et le devis")
        self.assertContains(page, self.demande.devis_id)


PLAQUE = {"forme": "rectangle", "largeur": 300, "hauteur": 200, "rayon_angle": 10}


class PieceLibreTests(ConfigurateurBase):
    def test_plaque_et_decoupes(self):
        recette = {"base": PLAQUE, "elements": [
            {"type": "rond", "x": 50, "y": 50, "diametre": 20},
            {"type": "oblong", "x": 150, "y": 100, "longueur": 80, "largeur": 20, "angle": 30},
            {"type": "rectangle", "x": 300, "y": 100, "largeur": 40, "hauteur": 60},  # encoche dans le bord droit
            {"type": "polygone", "x": 250, "y": 50, "nb_cotes": 6, "diametre": 30},
            {"type": "rectangle", "x": 150, "y": 210, "largeur": 40, "hauteur": 30, "mode": "ajouter"},  # patte
        ]}
        r = chiffrer("S235-W", 2, 10, libre=recette)
        self.assertEqual(r.statut, "ok", r.refus + r.validations)
        self.assertEqual(r.designation, "Pièce libre 300 × 200, 5 éléments")
        self.assertEqual(len(r.contour["trous"]), 3)
        self.assertEqual((r.largeur_mm, r.hauteur_mm), (300, 225))  # la patte dépasse de 25 mm
        plein = chiffrer("S235-W", 2, 10, forme="rectangle", cotes={"largeur": 300, "hauteur": 200, "rayon_angle": 10})
        self.assertGreater(r.cout_decoupe, plein.cout_decoupe)  # plus de contours à découper

    def test_disque_perce(self):
        r = chiffrer("S235-W", 2, 1, libre={"base": {"forme": "disque", "diametre": 150}, "elements": [{"type": "rond", "x": 75, "y": 75, "diametre": 40}]})
        self.assertEqual((r.statut, r.designation, len(r.contour["trous"])), ("ok", "Pièce libre Ø150, 1 élément", 1))

    def test_points_a_verifier(self):
        petit = chiffrer("S235-W", 2, 1, libre={"base": PLAQUE, "elements": [{"type": "rond", "x": 50, "y": 50, "diametre": 1.5}]})
        self.assertEqual(petit.statut, "validation")
        self.assertIn("plus petite(s) que l'épaisseur", petit.validations[0])
        pont = chiffrer("S235-W", 2, 1, libre={"base": PLAQUE, "elements": [
            {"type": "rond", "x": 50, "y": 50, "diametre": 20}, {"type": "rond", "x": 71, "y": 50, "diametre": 20}]})
        self.assertEqual(pont.statut, "validation")  # 1 mm de matière entre les deux trous, pour 2 mm d'épaisseur
        self.assertIn("plus fine que l'épaisseur", " ".join(pont.validations))

    def test_recettes_refusees(self):
        for recette, message in (
            ({"base": {"forme": "etoile"}}, "rectangle ou un disque"),
            ({"base": PLAQUE, "elements": [{"type": "rond", "x": 900, "y": 50, "diametre": 20}]}, "en dehors"),
            ({"base": PLAQUE, "elements": [{"type": "rectangle", "x": 150, "y": 100, "largeur": 10, "hauteur": 400}]}, "plusieurs morceaux"),
            ({"base": PLAQUE, "elements": [{"type": "rond", "x": "a", "y": 1, "diametre": 2}]}, "nombre"),
            ({"base": PLAQUE, "elements": [{"type": "rond", "x": 1, "y": 1, "diametre": 2}] * 201}, "200"),
            ({"base": PLAQUE, "elements": [{"type": "rectangle", "x": 500, "y": 500, "largeur": 10, "hauteur": 10, "mode": "ajouter"}]}, "toucher"),
        ):
            with self.subTest(message=message), self.assertRaisesMessage(ErreurPrixWeb, message):
                chiffrer("S235-W", 2, 1, libre=recette)


@override_settings(WEB_CLE_API="secret-web")
class DxfWebTests(ConfigurateurBase):
    def setUp(self):
        import tempfile

        self.media = tempfile.TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=self.media.name))

    def envoyer(self, contenu, nom="platine.dxf"):
        import base64

        return self.client.post("/api/web/v1/dxf/", {"nom": nom, "contenu": base64.b64encode(contenu).decode()},
                                content_type="application/json", HTTP_X_CLE_WEB="secret-web")

    def test_depot_prix_et_demande(self):
        from decoupe.services import formes

        contenu = formes.dxf_bytes(formes.construire("platine_grille", {"largeur": 200, "hauteur": 150}))
        page = self.envoyer(contenu)
        self.assertEqual(page.status_code, 200, page.content)
        fiche = page.json()
        self.assertEqual((fiche["largeur_mm"], fiche["hauteur_mm"], len(fiche["contour"]["trous"])), (200, 150, 4))
        self.assertEqual(self.envoyer(contenu).json()["jeton"], fiche["jeton"])  # même fichier, même jeton
        prix = self.client.post("/api/web/v1/prix/", {"matiere": "S235-W", "epaisseur": 2, "quantite": 4, "dxf": fiche["jeton"]},
                                content_type="application/json", HTTP_X_CLE_WEB="secret-web")
        self.assertEqual(prix.status_code, 200, prix.content)
        self.assertEqual((prix.json()["statut"], prix.json()["designation"]), ("ok", "Pièce du fichier platine.dxf"))
        page = self.client.post("/api/web/v1/demandes/", {"client": DemandeWebTests.CLIENT, "lignes": [
            {"matiere": "S235-W", "epaisseur": 2, "quantite": 4, "dxf": fiche["jeton"]},
            {"matiere": "S235-W", "epaisseur": 2, "quantite": 1, "libre": {"base": PLAQUE, "elements": [{"type": "rond", "x": 50, "y": 50, "diametre": 20}]}},
        ]}, content_type="application/json", HTTP_X_CLE_WEB="secret-web")
        self.assertEqual(page.status_code, 201, page.content)
        demande = DemandeWeb.objects.get()
        self.client.force_login(get_user_model().objects.create_superuser("dx", "dx@example.com", "pass-dx-1234"))
        self.assertContains(self.client.get(f"/admin/web/demandeweb/{demande.pk}/change/"), "/dxf/1/")
        self.assertContains(self.client.get(f"/admin/web/demandeweb/{demande.pk}/change/"), "Télécharger le DXF")
        original = self.client.get(f"/admin/web/demandeweb/{demande.pk}/dxf/0/")
        self.assertEqual(b"".join(original.streaming_content), contenu)  # le fichier du client, tel quel
        genere = self.client.get(f"/admin/web/demandeweb/{demande.pk}/dxf/1/")
        self.assertIn(b"LWPOLYLINE", genere.content)  # pièce libre : DXF recalculé depuis le contour
        self.assertEqual(self.client.get(f"/admin/web/demandeweb/{demande.pk}/dxf/9/").status_code, 404)

    def test_envoi_groupe_fichiers_et_prix(self):
        import base64

        carre = b'<svg xmlns="http://www.w3.org/2000/svg" width="50mm" height="50mm" viewBox="0 0 50 50"><path d="M0 0H50V50H0Z"/></svg>'
        page = self.client.post("/api/web/v1/dxf/", {"fichiers": [
            {"nom": "logo-1.svg", "contenu": base64.b64encode(self.SVG_MM).decode()},
            {"nom": "logo-2.svg", "contenu": base64.b64encode(carre).decode()},
            {"nom": "logo-3.svg", "contenu": "pas du base64 !"},
        ]}, content_type="application/json", HTTP_X_CLE_WEB="secret-web")
        self.assertEqual(page.status_code, 200, page.content)
        fiches = page.json()["fichiers"]
        self.assertEqual([f.get("largeur_mm") for f in fiches], [200, 50, None])
        self.assertIn("illisible", fiches[2]["erreur"])
        base = {"matiere": "S235-W", "epaisseur": 2, "quantite": 3}
        prix = self.client.post("/api/web/v1/prix/", {"lot": [{**base, "dxf": fiches[0]["jeton"]}, {**base, "dxf": fiches[1]["jeton"]},
                                                              {**base, "matiere": "Or", "dxf": fiches[1]["jeton"]}]},
                                content_type="application/json", HTTP_X_CLE_WEB="secret-web")
        self.assertEqual(prix.status_code, 200, prix.content)
        lot = prix.json()["lot"]
        self.assertTrue(all(r.get("statut") in ("ok", "validation") and r["prix_total_ht"] for r in lot[:2]), lot)
        self.assertGreater(lot[0]["prix_total_ht"], lot[1]["prix_total_ht"])
        self.assertIn("pas vendu en ligne", lot[2]["erreur"])
        for corps in ({"lot": []}, {"lot": [base] * 31}, {"fichiers": "x"}):
            chemin = "/api/web/v1/dxf/" if "fichiers" in corps else "/api/web/v1/prix/"
            self.assertEqual(self.client.post(chemin, corps, content_type="application/json", HTTP_X_CLE_WEB="secret-web").status_code, 400)

    def test_jeu_de_pieces_chiffre_comme_une_seule_decoupe(self):
        from web.services.prix import chiffrer_jeu

        D = Decimal
        petit = b'<svg xmlns="http://www.w3.org/2000/svg" width="30mm" height="20mm" viewBox="0 0 30 20"><path d="M0 0H30V20H0Z"/></svg>'
        jetons = [self.envoyer(c, f"logo-{i}.svg").json()["jeton"] for i, c in enumerate([self.SVG_MM] + [petit] * 5)]
        base = {"matiere": "S235-W", "epaisseur": 2, "quantite": 3}
        seules = [chiffrer("S235-W", 2, 3, dxf=j) for j in jetons]
        jeu = chiffrer_jeu([{**base, "dxf": j} for j in jetons])
        self.assertIn(jeu.statut, ("ok", "validation"), jeu.refus)
        self.assertEqual(jeu.nb_feuilles, 1)
        # une seule mise en place et une seule imbrication : bien moins cher que six pièces chiffrées chacune seule
        self.assertLess(jeu.prix_total_ht, sum(r.prix_total_ht for r in seules) * D("0.8"))
        self.assertEqual(sum(r.prix_total_ht for r in jeu.pieces), jeu.prix_total_ht)
        self.assertGreater(jeu.prix_total_ttc, jeu.prix_total_ht)
        self.assertGreater(jeu.pieces[0].prix_total_ht, jeu.pieces[1].prix_total_ht)
        # un jeu d'une pièce coûte exactement la pièce seule
        self.assertEqual(chiffrer_jeu([{**base, "dxf": jetons[0]}]).prix_total_ht, seules[0].prix_total_ht)
        with self.assertRaises(ErreurPrixWeb):
            chiffrer_jeu([{**base, "dxf": jetons[0]}, {**base, "quantite": 4, "dxf": jetons[1]}])
        refus = chiffrer_jeu([{**base, "dxf": jetons[0]}, {**base, "dxf": "inconnu"}])
        self.assertEqual((refus.statut, refus.prix_total_ht), ("refus", None))

        page = self.client.post("/api/web/v1/prix/", {"jeu": True, "lot": [{**base, "dxf": j} for j in jetons]},
                                content_type="application/json", HTTP_X_CLE_WEB="secret-web")
        self.assertEqual(page.status_code, 200, page.content)
        corps = page.json()
        self.assertEqual(corps["prix_total_ht"], float(jeu.prix_total_ht))
        self.assertEqual(len(corps["pieces"]), 6)
        self.assertNotIn("cout_revient", corps)

        # la demande recalcule le jeu ensemble : même total que le prix affiché
        lignes = [{**base, "dxf": j, "jeu": "logo"} for j in jetons]
        reponse = self.client.post("/api/web/v1/demandes/", {"client": DemandeWebTests.CLIENT, "lignes": lignes + [{**base, "dxf": jetons[1]}]},
                                   content_type="application/json", HTTP_X_CLE_WEB="secret-web")
        self.assertEqual(reponse.status_code, 201, reponse.content)
        self.assertAlmostEqual(reponse.json()["total_pieces_ht"], float(jeu.prix_total_ht + seules[1].prix_total_ht), places=2)

    SVG_MM = (b'<svg xmlns="http://www.w3.org/2000/svg" width="200mm" height="100mm" viewBox="0 0 200 100">'
              b'<path d="M0,0 H200 V70 A30 30 0 0 1 170 100 H0 Z"/><circle cx="40" cy="50" r="15"/>'
              b'<rect x="90" y="30" width="40" height="20" rx="5"/><text x="5" y="5">Logo</text></svg>')

    def test_svg_converti_en_dxf(self):
        page = self.envoyer(self.SVG_MM, "platine.svg")
        self.assertEqual(page.status_code, 200, page.content)
        fiche = page.json()
        self.assertEqual((fiche["format"], fiche["largeur_mm"], fiche["hauteur_mm"], len(fiche["contour"]["trous"])), ("svg", 200, 100, 2))
        self.assertTrue(any("texte" in a for a in fiche["avertissements"]))
        # Sans unité : 96 px par pouce, et le client est prévenu.
        px = self.envoyer(b'<svg xmlns="http://www.w3.org/2000/svg" width="378" height="189"><rect width="378" height="189"/></svg>', "px.svg").json()
        self.assertAlmostEqual(px["largeur_mm"], 100, places=1)
        self.assertTrue(any("96 px" in a for a in px["avertissements"]))
        page = self.client.post("/api/web/v1/demandes/", {"client": DemandeWebTests.CLIENT, "lignes": [
            {"matiere": "S235-W", "epaisseur": 2, "quantite": 2, "dxf": fiche["jeton"]}]}, content_type="application/json", HTTP_X_CLE_WEB="secret-web")
        self.assertEqual(page.status_code, 201, page.content)
        demande = DemandeWeb.objects.get()
        self.client.force_login(get_user_model().objects.create_superuser("sv", "sv@example.com", "pass-sv-1234"))
        self.assertContains(self.client.get(f"/admin/web/demandeweb/{demande.pk}/change/"), "?original=1")
        dxf = self.client.get(f"/admin/web/demandeweb/{demande.pk}/dxf/0/")
        self.assertIn(b"LWPOLYLINE", b"".join(dxf.streaming_content))
        original = self.client.get(f"/admin/web/demandeweb/{demande.pk}/dxf/0/?original=1")
        self.assertEqual(b"".join(original.streaming_content), self.SVG_MM)

    def test_mise_a_l_echelle(self):
        from decoupe.services import formes

        fiche = self.envoyer(formes.dxf_bytes(formes.construire("platine_grille", {"largeur": 200, "hauteur": 150}))).json()
        prix = lambda **e: self.client.post("/api/web/v1/prix/", {"matiere": "S235-W", "epaisseur": 2, "quantite": 4, "dxf": fiche["jeton"], **e},
                                            content_type="application/json", HTTP_X_CLE_WEB="secret-web")
        normal, double = prix().json(), prix(echelle=1.5).json()
        self.assertEqual((double["largeur_mm"], double["hauteur_mm"]), (300, 225))
        self.assertIn("à l'échelle 150 %", double["designation"])
        self.assertGreater(double["prix_total_ht"], normal["prix_total_ht"])
        self.assertEqual(prix(echelle=0).status_code, 400)
        page = self.client.post("/api/web/v1/demandes/", {"client": DemandeWebTests.CLIENT, "lignes": [
            {"matiere": "S235-W", "epaisseur": 2, "quantite": 4, "dxf": fiche["jeton"], "echelle": 1.5}]},
            content_type="application/json", HTTP_X_CLE_WEB="secret-web")
        self.assertEqual(page.status_code, 201, page.content)
        demande = DemandeWeb.objects.get()
        self.assertEqual(demande.lignes[0]["demande"]["echelle"], 1.5)
        self.client.force_login(get_user_model().objects.create_superuser("ec", "ec@example.com", "pass-ec-1234"))
        import ezdxf

        dxf = self.client.get(f"/admin/web/demandeweb/{demande.pk}/dxf/0/")
        doc = ezdxf.read(io.StringIO(dxf.content.decode()))
        xs = [x for e in doc.modelspace().query("LWPOLYLINE") for x, *_ in e.get_points()]
        self.assertAlmostEqual(max(xs) - min(xs), 300, places=1)  # DXF de fabrication à la taille commandée

    def test_dwg_sans_convertisseur(self):
        from unittest import mock

        with mock.patch("web.services.dxf.dwg_lisible", return_value=False), mock.patch("shutil.which", return_value=None), \
                mock.patch("ezdxf.addons.odafc.is_installed", return_value=False), mock.patch("web.services.step.lisible", return_value=False):
            page = self.envoyer(b"AC1032 pas un vrai dwg", "piece.dwg")
            self.assertEqual(page.status_code, 400)
            self.assertIn("exportez votre pièce en DXF", page.json()["erreur"])
            formats = self.client.get("/api/web/v1/catalogue/", HTTP_X_CLE_WEB="secret-web").json()["formats_fichier"]
            self.assertEqual(formats, ["dxf", "svg"])

    def test_fichiers_refuses(self):
        from decoupe.services import formes

        deux = formes.dxf_bytes(formes.Contour([(0, 0), (10, 0), (10, 10), (0, 10)], [[(20, 0), (30, 0), (30, 10), (20, 10)]]))
        for contenu, nom, message in (
            (deux, "deux.dxf", "pièces séparées"),
            (b"pas un dxf", "piece.dxf", "illisible"),
            (b"0\nSECTION", "piece.iges", "DXF, DWG, SVG ou STEP"),
            (b"<svg xmlns='http://www.w3.org/2000/svg'><text>a</text></svg>", "texte.svg", "aucun tracé"),
            (b"", "piece.dxf", "vide"),
            (b"x" * (1536 * 1024 + 1), "piece.dxf", "trop gros"),
        ):
            with self.subTest(message=message):
                page = self.envoyer(contenu, nom)
                self.assertEqual(page.status_code, 400)
                self.assertIn(message, page.json()["erreur"])
                self.assertNotIn(self.media.name, page.json()["erreur"])  # jamais de chemin du serveur
        prix = self.client.post("/api/web/v1/prix/", {"matiere": "S235-W", "epaisseur": 2, "quantite": 1, "dxf": "0" * 64},
                                content_type="application/json", HTTP_X_CLE_WEB="secret-web")
        self.assertIn("renvoyez-le", prix.json()["erreur"])


class BibliothequeWebTests(ConfigurateurBase):
    """Formes décoratives game-icons : un seul tenant, largeur mini selon l'épaisseur, prix et demande."""

    def test_formes_d_un_seul_tenant_et_credit(self):
        from shapely.geometry import Polygon

        from .services import bibliotheque

        liste = bibliotheque.liste()
        self.assertIn("CC BY 3.0", liste["credit"])
        cles = {f["cle"]: f for f in liste["formes"]}
        self.assertGreater(len(cles), 500)
        self.assertEqual(cles["anchor"]["nom"], "Ancre")
        self.assertNotIn("bat", cles)  # ailes accrochées par un point : deux morceaux à la découpe
        self.assertNotIn("suicide", cles)
        contour, designation = bibliotheque.construire("anchor", 250)
        poly = Polygon(contour["exterieur"], contour["trous"])
        self.assertTrue(poly.is_valid)
        minx, _, maxx, _ = poly.bounds
        self.assertAlmostEqual(maxx - minx, 250, places=1)
        self.assertTrue(designation.startswith("Forme « Ancre » 250 × "))

    def test_aucun_trait_ni_jour_plus_fin_que_l_epaisseur(self):
        from shapely.geometry import Polygon

        from .services import bibliotheque

        for cle in ("anchor", "turtle", "spider-web", "card-king-clubs"):
            largeur = bibliotheque.largeur_mini(cle, 3)
            contour, _ = bibliotheque.construire(cle, largeur)
            piece = Polygon(contour["exterieur"], contour["trous"])
            ouverte = piece.buffer(-1.5).buffer(1.5)  # efface les traits de moins de 3 mm
            fermee = piece.buffer(1.5).buffer(-1.5)  # bouche les jours de moins de 3 mm
            self.assertEqual(ouverte.geom_type, "Polygon", cle)
            self.assertEqual((len(ouverte.interiors), len(fermee.interiors)), (len(piece.interiors),) * 2, cle)
            self.assertGreater(ouverte.area / piece.area, 0.97, cle)
        self.assertGreaterEqual(bibliotheque.largeur_mini("anchor", 6), 2 * bibliotheque.largeur_mini("anchor", 3) - 1)  # proportionnelle

    def test_prix_largeur_mini_et_demande(self):
        from .services import bibliotheque

        mini = bibliotheque.largeur_mini("anchor", 2)
        r = chiffrer("S235-W", 2, 3, bibliotheque={"cle": "anchor", "largeur": 200})
        self.assertEqual((r.statut, r.largeur_mm), ("ok", 200))
        self.assertIn("Ancre", r.designation)
        trop_petit = chiffrer("S235-W", 2, 3, bibliotheque={"cle": "anchor", "largeur": mini - 1})
        self.assertEqual(trop_petit.statut, "refus")
        self.assertIn(f"à partir de {mini} mm", trop_petit.refus[0])
        self.assertEqual(chiffrer("S235-W", 2, 3, bibliotheque={"cle": "anchor", "largeur": mini}).statut, "ok")
        for mauvaise in ({"cle": "inconnue", "largeur": 100}, {"cle": "anchor", "largeur": "large"}, {"cle": "anchor", "largeur": 0}, "anchor"):
            with self.assertRaises(ErreurPrixWeb):
                chiffrer("S235-W", 2, 1, bibliotheque=mauvaise)

    @override_settings(WEB_CLE_API="secret-web")
    def test_api(self):
        page = self.client.get("/api/web/v1/bibliotheque/", HTTP_X_CLE_WEB="secret-web")
        self.assertEqual(page.status_code, 200)
        ancre = next(f for f in page.json()["formes"] if f["cle"] == "anchor")
        self.assertEqual(set(ancre), {"cle", "nom", "proportion", "rapport", "apercu"})
        self.assertTrue(ancre["apercu"].startswith("M"))
        self.assertEqual(self.client.get("/api/web/v1/bibliotheque/", HTTP_X_CLE_WEB="mauvaise").status_code, 403)
        self.assertGreater(self.client.get("/api/web/v1/catalogue/", HTTP_X_CLE_WEB="secret-web").json()["bibliotheque"]["nombre"], 500)
        ligne = {"matiere": "S235-W", "epaisseur": 2, "quantite": 2, "bibliotheque": {"cle": "anchor", "largeur": 300}}
        prix = self.client.post("/api/web/v1/prix/", ligne, content_type="application/json", HTTP_X_CLE_WEB="secret-web")
        self.assertEqual((prix.status_code, prix.json()["statut"], prix.json()["largeur_mm"]), (200, "ok", 300))
        page = self.client.post("/api/web/v1/demandes/", {"client": DemandeWebTests.CLIENT, "lignes": [ligne]},
                                content_type="application/json", HTTP_X_CLE_WEB="secret-web")
        self.assertEqual(page.status_code, 201, page.content)
        demande = DemandeWeb.objects.get()
        self.assertEqual(demande.lignes[0]["demande"]["bibliotheque"], {"cle": "anchor", "largeur": 300})
        self.client.force_login(get_user_model().objects.create_superuser("bi", "bi@example.com", "pass-bi-1234"))
        dxf = self.client.get(f"/admin/web/demandeweb/{demande.pk}/dxf/0/")
        self.assertIn(b"LWPOLYLINE", dxf.content)  # DXF de fabrication à la largeur commandée


@override_settings(WEB_CLE_API="secret-web")
class StepWebTests(ConfigurateurBase):
    """Pièces 3D (STEP) : épaisseur et plis lus dans le fichier, flan déplié avec l'outillage de l'atelier."""

    envoyer = DxfWebTests.envoyer

    def setUp(self):
        DxfWebTests.setUp(self)
        from .pieces_step_essai import bac, ecrire_step, plaque, profil

        self.ecrire, self.profil, self.plaque, self.bac = ecrire_step, profil, plaque, bac

    def prix(self, jeton, **autres):
        return self.client.post("/api/web/v1/prix/", {"matiere": "S235-W", "epaisseur": 2, "quantite": 10, "dxf": jeton, **autres},
                                content_type="application/json", HTTP_X_CLE_WEB="secret-web")

    def test_corniere_comme_le_profil_saisi(self):
        # Cornière 50 × 100 cotes extérieures, rayon intérieur 2 dans le modèle, 60 de long, percée de deux trous.
        solide = self.profil([46, 96], [90], 2, 2, 60, trous=[(20, 0, 30, (0, 1, 0), 8), (48, 50, 30, (1, 0, 0), 10)])
        page = self.envoyer(self.ecrire(solide), "corniere.stp")
        self.assertEqual(page.status_code, 200, page.content)
        fiche = page.json()
        self.assertEqual((fiche["format"], fiche["epaisseur_mm"], len(fiche["plis"]), len(fiche["contour"]["trous"])), ("step", 2, 1, 2))
        self.assertEqual((fiche["plis"][0]["angle"], fiche["plis"][0]["longueur"], fiche["plis"][0]["faces"]), (90, 60, [50, 100]))
        r = self.prix(fiche["jeton"]).json()
        self.assertEqual(r["statut"], "ok", r)
        saisi = chiffrer("S235-W", 2, 10, pliage={"forme": "profil", "faces": [50, 100], "angles": [90], "longueur": 60})
        # même flan que le profil saisi à la main (développé au rayon du vé de l'atelier, pas à celui du modèle)
        self.assertAlmostEqual(r["largeur_mm"], saisi.largeur_mm, places=1)
        self.assertEqual(r["hauteur_mm"], 60)
        self.assertEqual((r["pliage"]["nb_plis"], len(r["contour"]["trous"])), (1, 2))
        self.assertIn("(1 pli)", r["designation"])
        Article.objects.create(reference="S235-W-3", nature=Article.Nature.MATIERE_PREMIERE, matiere=self.matiere, epaisseur=3,
                               unite_cout=Article.UniteCout.SURFACE, cout_unitaire=30, format_tole=self.tole.format_tole, vendu_en_ligne=True)
        self.assertIn("choisissez 2 mm", self.prix(fiche["jeton"], epaisseur=3).json()["erreur"])  # épaisseur imposée par le fichier
        self.assertEqual(self.prix(fiche["jeton"], echelle=2).status_code, 400)

    def test_lignes_de_pli_et_oblong_de_decharge(self):
        # Cornière dont un trou de 6 mm tombe à 3 mm du pli (bord mini 8,5 mm en V12) : oblong 6 × 2 sur la ligne de pli.
        solide = self.profil([46, 96], [90], 2, 2, 60, trous=[(40, 0, 30, (0, 1, 0), 6)])
        fiche = self.envoyer(self.ecrire(solide), "corniere.stp").json()
        r = chiffrer("S235-W", 2, 10, dxf=fiche["jeton"])
        saisi = chiffrer("S235-W", 2, 10, pliage={"forme": "profil", "faces": [50, 100], "angles": [90], "longueur": 60})
        (pli,), (pli_saisi,) = r.lignes_pli, saisi.lignes_pli
        self.assertEqual((pli["angle"], pli["sens"]), (90, "haut"))
        self.assertAlmostEqual(pli["p1"][0], pli_saisi["p1"][0], places=1)  # même ligne que le profil saisi à la main
        self.assertEqual(len(r.modifications), 1)
        self.assertIn("oblong de décharge 6.0 × 2 mm", r.modifications[0])
        self.assertEqual(len(r.contour["trous"]), 2)  # le trou et l'oblong
        self.assertIn("oblong de décharge", self.prix(fiche["jeton"]).json()["modifications"][0])
        page = self.client.post("/api/web/v1/demandes/", {"client": DemandeWebTests.CLIENT, "lignes": [
            {"matiere": "S235-W", "epaisseur": 2, "quantite": 2, "dxf": fiche["jeton"]}]}, content_type="application/json", HTTP_X_CLE_WEB="secret-web")
        self.assertEqual(page.status_code, 201, page.content)
        demande = DemandeWeb.objects.get()
        self.client.force_login(get_user_model().objects.create_superuser("lp", "lp@example.com", "pass-lp-1234"))
        dxf = self.client.get(f"/admin/web/demandeweb/{demande.pk}/dxf/0/").content
        self.assertIn(b"PLIAGE", dxf)
        self.assertIn("Pli 1 : 90° HAUT".encode(), dxf)
        from .services.conversion import creer_devis

        devis, _, _ = creer_devis(demande)
        piece = devis.pieces_decoupe.get()  # le flan importé : lignes de pli à part, pas découpées
        self.assertEqual((piece.statut, piece.nb_contours_interieurs, len(piece.pliage_json["traits"])), ("ok", 2, 1))

    def test_plaque_plane_compressee_et_demande(self):
        import gzip

        contenu = self.ecrire(self.plaque(200, 100, 2, [(50, 50, 20), (150, 50, 10)]))
        fiche = self.envoyer(gzip.compress(contenu), "platine.step").json()
        self.assertEqual((fiche["largeur_mm"], fiche["hauteur_mm"], fiche["plis"], len(fiche["contour"]["trous"])), (200, 100, [], 2))
        self.assertEqual(self.prix(fiche["jeton"]).json()["statut"], "ok")
        page = self.client.post("/api/web/v1/demandes/", {"client": DemandeWebTests.CLIENT, "lignes": [
            {"matiere": "S235-W", "epaisseur": 2, "quantite": 2, "dxf": fiche["jeton"]}]}, content_type="application/json", HTTP_X_CLE_WEB="secret-web")
        self.assertEqual(page.status_code, 201, page.content)
        demande = DemandeWeb.objects.get()
        self.client.force_login(get_user_model().objects.create_superuser("st", "st@example.com", "pass-st-1234"))
        self.assertContains(self.client.get(f"/admin/web/demandeweb/{demande.pk}/change/"), "Fichier d'origine (STEP)")
        original = self.client.get(f"/admin/web/demandeweb/{demande.pk}/dxf/0/?original=1")
        self.assertEqual(b"".join(original.streaming_content), contenu)  # le STEP décompressé, tel qu'exporté par le client
        dxf = self.client.get(f"/admin/web/demandeweb/{demande.pk}/dxf/0/")
        self.assertIn(b"LWPOLYLINE", dxf.content)  # flan chiffré, en DXF

    def test_bac_plie_dans_deux_directions(self):
        fiche = self.envoyer(self.ecrire(self.bac(100, 60, 20, 2, 2)), "bac.step").json()
        self.assertEqual(len(fiche["plis"]), 4)
        self.assertAlmostEqual(fiche["largeur_mm"], 148.8, places=1)  # 20 + 100 + 20 + 2 arcs neutres (K 0,4 en aperçu)
        self.assertTrue(any("plusieurs directions" in a for a in fiche["avertissements"]))
        r = self.prix(fiche["jeton"]).json()
        self.assertEqual(r["statut"], "validation", r)
        self.assertEqual(len(r["contour"]["exterieur"]), 12)  # flan en croix, coins crantés

    def test_fichiers_step_refuses(self):
        from .pieces_step_essai import assembler, bossage

        for solide, nom, message in (
            (assembler(self.plaque(50, 50, 2), self.profil([10, 10], [90], 2, 2, 50)), "deux.step", "pièces séparées"),
            (bossage(), "bossage.step", "épaisseur constante"),
        ):
            with self.subTest(message=message):
                page = self.envoyer(self.ecrire(solide), nom)
                self.assertEqual(page.status_code, 400, page.content)
                self.assertIn(message, page.json()["erreur"])
        page = self.envoyer(b"ISO-10303-21; pas vraiment", "faux.step")
        self.assertIn("STEP illisible", page.json()["erreur"])

    def test_catalogue_propose_le_step(self):
        formats = self.client.get("/api/web/v1/catalogue/", HTTP_X_CLE_WEB="secret-web").json()["formats_fichier"]
        self.assertIn("step", formats)
