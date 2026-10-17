import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from chiffrage.moteur import cout_etape_gamme
from technique.models import Article, FamilleMatiere, Gamme, Matiere, PosteTravail, TarifPoste

from .models import PliageArticle, Plieuse, ReglagePliage, ReglagePliageEpaisseur
from .services.gamme import alimenter_gamme_pliage
from .services.pliage import ErreurPliage, calculer_boite, calculer_pliage, developpe, force_abaque, table_pour


class PliageCalculTests(TestCase):
    """Exemples de la spécification de pliage (plieuses et outils créés par la migration decoupe 0038)."""

    @classmethod
    def setUpTestData(cls):
        cls.acier = Matiere.objects.create(nom="S235-T", densite=7.85, famille=FamilleMatiere.objects.get(nom="Acier"))
        cls.inox = Matiere.objects.create(nom="304L-T", densite=7.9, famille=FamilleMatiere.objects.get(nom="Inox"))

    def test_corniere_2mm_developpe_valide_par_l_atelier(self):
        r = calculer_pliage(self.acier, 2, [50, 100], [90], 60)
        self.assertEqual(r.statut, "ok")
        self.assertEqual((r.ve, r.plieuse), ("V12/30°", "Plieuse 2 m / 50 t"))
        self.assertEqual(r.bord_mini, 8.5)
        self.assertEqual(r.developpe_mm, 146.54)
        self.assertEqual((r.nb_reglages, r.temps_reglage_min, r.temps_pli_min), (1, 5.0, 0.5))

    def test_marge_de_force_70_pourcent_choisit_la_plieuse(self):
        self.assertEqual(calculer_pliage(self.acier, 3, [50, 50], [90], 1300).plieuse, "Plieuse 2 m / 50 t")  # 33 t ≤ 35
        self.assertEqual(calculer_pliage(self.acier, 3, [50, 50], [90], 1500).plieuse, "Plieuse 3 m / 200 t")  # 38 t
        self.assertEqual(calculer_pliage(self.inox, 3, [50, 50], [90], 1000).plieuse, "Plieuse 3 m / 200 t")
        r = calculer_pliage(self.acier, 3, [50, 50], [90], 3000)
        self.assertEqual(r.ve, "V24/30°")
        self.assertAlmostEqual(r.force_t, 76.45, places=1)
        self.assertIn("Aucune plieuse", " ".join(calculer_pliage(self.inox, 10, [100, 100], [90], 1200).refus))

    def test_v24_extrapole_du_v25(self):
        force, exacte = force_abaque(table_pour(FamilleMatiere.objects.get(nom="Acier")), 3, 24)
        self.assertFalse(exacte)
        self.assertAlmostEqual(force, 250, delta=1)

    def test_angle_mini_selon_le_ve_et_la_force(self):
        r = calculer_pliage(self.acier, 2, [50, 100], [30], 100)
        self.assertEqual((r.statut, r.angle_mini), ("refus", 35))
        self.assertEqual(calculer_pliage(self.acier, 2, [50, 100], [35], 100).statut, "ok")
        self.assertEqual(calculer_pliage(self.acier, 5, [50, 50], [45], 500).angle_mini, 55)

    def test_angle_aigu_au_dela_de_la_force_nominale_va_au_bureau_d_etudes(self):
        r = calculer_pliage(self.inox, 2, [50, 100], [60], 100)  # 330 kN/m > 250 du V12 à 30°
        self.assertEqual(r.statut, "validation")
        self.assertIn("bureau d'études", r.validations[0])

    def test_face_trop_courte_refusee(self):
        r = calculer_pliage(self.acier, 2, [5, 100], [90], 100)
        self.assertEqual(r.statut, "refus")
        self.assertIn("Face A trop courte", r.refus[0])

    def test_validations_technicien(self):
        self.assertEqual(calculer_pliage(self.acier, 2, [20, 60, 20], [90, -90], 500).statut, "ok")
        r = calculer_pliage(self.acier, 2, [100, 40, 100], [90, 90], 500)  # U : ailes > largeur
        self.assertEqual(len(r.validations), 2)
        r = calculer_pliage(self.acier, 2, [30, 12, 30], [90, -90], 500)  # face entre deux plis < 2 × 8,5
        self.assertIn("2 × bord mini", r.validations[0])
        r = calculer_pliage(self.acier, 8, [100, 100], [90], 1000)
        self.assertEqual((r.statut, r.ve, r.plieuse), ("validation", "V80/80°", "Plieuse 3 m / 200 t"))

    def test_temps_de_reglage_par_epaisseur(self):
        reglage = ReglagePliage.charger()
        ReglagePliageEpaisseur.objects.create(reglage=reglage, epaisseur_max_mm=3, reglage_court_min=4, reglage_long_min=6)
        ReglagePliageEpaisseur.objects.create(reglage=reglage, epaisseur_max_mm=6, reglage_court_min=10, reglage_long_min=15)
        r = calculer_pliage(self.acier, 2, [50, 100], [90], 60)  # tranche jusqu'à 3 mm, pli court
        self.assertEqual(r.temps_reglage_min, 4)
        r = calculer_pliage(self.acier, 3, [50, 100], [90], 1000)  # 3 mm inclus dans la première tranche, pli long
        self.assertEqual(r.temps_reglage_min, 6)
        r = calculer_pliage(self.acier, 5, [50, 100], [90], 60)
        self.assertEqual(r.temps_reglage_min, 10)
        r = calculer_pliage(self.acier, 8, [100, 100], [90], 1000)  # au-delà des tranches : temps généraux
        self.assertEqual(r.temps_reglage_min, 7.5)

    def test_reglages_distincts(self):
        r = calculer_pliage(self.acier, 2, [40, 200, 40], [90, 90], 1000)  # U symétrique : un seul réglage, pli long
        self.assertEqual((r.nb_reglages, r.temps_reglage_min), (1, 7.5))
        r = calculer_pliage(self.acier, 2, [40, 200, 60], [90, 90], 500)
        self.assertEqual(r.nb_reglages, 2)

    def test_developpe_angle_ouvert_et_aigu(self):
        self.assertAlmostEqual(developpe([50, 50], [180 - 1e-9], 2, 2, 0.445), 100, places=3)
        # angle aigu coté à la tangence : recul = ri + e ; arc sur 120°
        self.assertAlmostEqual(developpe([50, 50], [60], 2, 2, 0.445), 100 - 8 + 2.0944 * 2.89, places=2)
        # angle ouvert (135°) coté à l'intersection virtuelle : recul = (ri + e) × tan(22,5°)
        self.assertAlmostEqual(developpe([50, 50], [135], 2, 2, 0.445), 100 - 8 * 0.41421 + 0.7854 * 2.89, places=2)

    def test_parametrage_manquant(self):
        famille = FamilleMatiere.objects.create(nom="Essai sans K")
        with self.assertRaises(ErreurPliage):
            calculer_pliage(Matiere.objects.create(nom="X-T", densite=1, famille=famille), 2, [50, 50], [90], 100)

    def test_saisie_controlee(self):
        article = Article.objects.create(reference="PL-0", nature="fabrique", libelle="Pièce")
        with self.assertRaises(ValidationError):
            PliageArticle(article=article, matiere=self.acier, epaisseur_mm=2, longueur_pli_mm=100, faces_mm=[50, 50], angles_deg=[180]).full_clean()
        with self.assertRaises(ValidationError):
            PliageArticle(article=article, matiere=self.acier, epaisseur_mm=2, longueur_pli_mm=100, faces_mm=[50], angles_deg=[]).full_clean()


class PliageBoiteTests(TestCase):
    """Boîtes pliées dans deux directions : fond L × l, bords A et C le long de L, B et D le long de l."""

    @classmethod
    def setUpTestData(cls):
        cls.acier = Matiere.objects.create(nom="S235-B", densite=7.85, famille=FamilleMatiere.objects.get(nom="Acier"))

    def test_boite_200_150_40(self):
        r = calculer_boite(self.acier, 2, 200, 150, [40, 40, 40, 40])
        self.assertEqual((r.statut, r.ve, r.plieuse, r.nb_plis), ("ok", "V12/30°", "Plieuse 2 m / 50 t", 4))
        # 146,54 pour une cornière 50 / 100 : chaque pli à 90° retire 3,46 mm
        self.assertEqual(r.flan_mm, [273.08, 223.08])
        # deux réglages distincts (bords A/C sur 200, bords B/D sur 150), 4 plis par pièce
        self.assertEqual((r.nb_reglages, r.temps_reglage_min, r.temps_pli_min), (2, 10.0, 2.0))
        self.assertIn("poinçon en segments", r.notes[-1])

    def test_boite_carree_un_seul_reglage(self):
        r = calculer_boite(self.acier, 2, 400, 400, [50, 50, 50, 50])
        self.assertEqual((r.nb_reglages, r.temps_reglage_min), (1, 5.0))

    def test_deux_bords_opposes_comme_un_u(self):
        r = calculer_boite(self.acier, 2, 200, 150, [40, 0, 40, 0])
        u = calculer_pliage(self.acier, 2, [40, 150, 40], [90, 90], 200)
        self.assertEqual(r.flan_mm, [200, u.developpe_mm])
        self.assertEqual((r.nb_reglages, r.temps_pli_min, r.notes), (u.nb_reglages, u.temps_pli_min, []))

    def test_force_et_plieuse_selon_le_pli_le_plus_long(self):
        r = calculer_boite(self.acier, 3, 2500, 500, [50, 50, 50, 50])
        self.assertEqual((r.ve, r.plieuse), ("V24/30°", "Plieuse 3 m / 200 t"))
        self.assertEqual(r.temps_reglage_min, 12.5)  # 7,5 min pour le pli de 2 500, 5 pour celui de 500

    def test_validations(self):
        r = calculer_boite(self.acier, 2, 300, 100, [120, 0, 120, 0])
        self.assertEqual(r.statut, "validation")
        self.assertIn("Bord A (120 mm) plus haut que la largeur du fond", r.validations[0])
        r = calculer_boite(self.acier, 2, 600, 600, [300, 300, 300, 300])  # P7 H250 trop court pour les derniers plis
        self.assertIn("P7 RF-A H250", r.validations[0])
        r = calculer_boite(self.acier, 2, 200, 150, [40, 40, 40, 40], [80, 90, 90, 90])
        self.assertIn("Bord A rentrant", r.validations[0])
        self.assertIn("Bord B trop courte", calculer_boite(self.acier, 2, 200, 150, [40, 5, 40, 40]).refus[0])

    def test_bac_evase(self):
        r = calculer_boite(self.acier, 2, 200, 150, [40, 40, 40, 40], [100, 100, 100, 100])
        self.assertEqual(r.statut, "ok")
        self.assertGreater(r.flan_mm[0], 273.08)

    def test_saisie_controlee(self):
        article = Article.objects.create(reference="PB-0", nature="fabrique", libelle="Boîte")
        base = dict(article=article, matiere=self.acier, epaisseur_mm=2, forme="boite", longueur_fond_mm=200, largeur_fond_mm=150)
        PliageArticle(**base, bords_mm=[40, 40, 40, 40]).full_clean()
        for bords, angles in (([40, 40, 40], []), ([0, 0, 0, 0], []), ([40, 40, 40, 40], [90, 180, 90, 90])):
            with self.assertRaises(ValidationError):
                PliageArticle(**base, bords_mm=bords, angles_bords_deg=angles).full_clean()


class PliageGammeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.poste = PosteTravail.objects.create(nom="Plieuse 50 t", mode_calcul="horaire")
        TarifPoste.objects.create(poste=cls.poste, cout_horaire=60, date_debut=datetime.date(2020, 1, 1))
        Plieuse.objects.filter(nom="Plieuse 2 m / 50 t").update(poste=cls.poste)
        cls.acier = Matiere.objects.create(nom="S235-G", densite=7.85, famille=FamilleMatiere.objects.get(nom="Acier"))
        cls.article = Article.objects.create(reference="PL-1", nature="fabrique", libelle="Cornière")

    def test_etape_de_gamme_et_temps_de_serie(self):
        pliage = PliageArticle.objects.create(article=self.article, matiere=self.acier, epaisseur_mm=2, longueur_pli_mm=60, faces_mm=[50, 100], angles_deg=[90])
        etape, resultat = alimenter_gamme_pliage(pliage)
        self.assertEqual((etape.origine, etape.poste, etape.temps_fixe, etape.temps_variable), ("pliage", self.poste, 5.0, 0.5))
        jour = datetime.date.today()
        # 600 pièces en série : 5 min + 600 × 15 s = 155 min à 60 €/h
        self.assertEqual(cout_etape_gamme(etape, 600, jour), Decimal("155.00"))
        self.assertEqual(cout_etape_gamme(etape, 5, jour), Decimal("7.50"))  # 5 + 5 × 30 s
        pliage.longueur_pli_mm = 900
        pliage.save()
        etape2, _ = alimenter_gamme_pliage(pliage)
        self.assertEqual(etape2.pk, etape.pk)
        self.assertEqual(Gamme.objects.filter(article=self.article, origine="pliage").count(), 1)
        self.assertEqual(etape2.temps_fixe, 7.5)

    def test_plieuse_sans_poste(self):
        pliage = PliageArticle.objects.create(article=self.article, matiere=self.acier, epaisseur_mm=3, longueur_pli_mm=2500, faces_mm=[50, 50], angles_deg=[90])
        with self.assertRaisesMessage(ErreurPliage, "poste de travail"):
            alimenter_gamme_pliage(pliage)

    def test_boite_dans_la_gamme(self):
        article = Article.objects.create(reference="PB-1", nature="fabrique", libelle="Boîte")
        pliage = PliageArticle.objects.create(article=article, matiere=self.acier, epaisseur_mm=2, forme="boite",
                                              longueur_fond_mm=200, largeur_fond_mm=150, bords_mm=[40, 40, 40, 40])
        etape, resultat = alimenter_gamme_pliage(pliage)
        self.assertEqual((etape.poste, etape.temps_fixe, etape.temps_variable), (self.poste, 10.0, 2.0))
        self.assertEqual(pliage.resultat["flan_mm"], [273.08, 223.08])

    def test_reglage_unique(self):
        self.assertEqual(ReglagePliage.charger().marge_force_pct, 70)


class PliageAdminTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("pli", "p@example.com", "pass-pli-1234"))

    def test_saisie_par_l_admin(self):
        acier = Matiere.objects.create(nom="S235-A", densite=7.85, famille=FamilleMatiere.objects.get(nom="Acier"))
        article = Article.objects.create(reference="PL-2", nature="fabrique", libelle="U")
        page = self.client.post("/admin/decoupe/pliagearticle/add/", {
            "article": article.pk, "matiere": acier.pk, "epaisseur_mm": "2", "forme": "profil", "longueur_pli_mm": "500", "faces": "20 ; 60 ; 20", "angles": "90 ; 90",
        }, follow=True)
        self.assertEqual(page.status_code, 200)
        pliage = PliageArticle.objects.get(article=article)
        self.assertEqual((pliage.faces_mm, pliage.angles_deg, pliage.resultat["statut"]), ([20, 60, 20], [90, 90], "ok"))
        self.assertContains(page, "poste de travail")  # plieuse sans poste : message d'erreur, mais le calcul est enregistré
        self.assertContains(self.client.get(f"/admin/decoupe/pliagearticle/{pliage.pk}/change/"), "V12/30°")
        boite = Article.objects.create(reference="PB-2", nature="fabrique", libelle="Bac")
        page = self.client.post("/admin/decoupe/pliagearticle/add/", {
            "article": boite.pk, "matiere": acier.pk, "epaisseur_mm": "2", "forme": "boite", "longueur_fond_mm": "200",
            "largeur_fond_mm": "150", "bords": "40 ; 40 ; 40 ; 40", "angles_bords": "",
        }, follow=True)
        pliage = PliageArticle.objects.get(article=boite)
        self.assertEqual((pliage.bords_mm, pliage.faces_mm, pliage.resultat["flan_mm"]), ([40, 40, 40, 40], [], [273.08, 223.08]))
        self.assertContains(self.client.get(f"/admin/decoupe/pliagearticle/{pliage.pk}/change/"), "273.08 × 223.08")
        self.assertContains(self.client.get("/admin/decoupe/pliagearticle/"), "Boîte 200 × 150")
        for url in ("/admin/decoupe/plieuse/", "/admin/decoupe/reglagepliage/", "/admin/decoupe/pliagearticle/"):
            self.assertEqual(self.client.get(url).status_code, 200, url)
