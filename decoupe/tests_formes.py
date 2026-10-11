"""Bibliothèque de formes paramétriques : contours, validation des cotes, DXF, cotes normalisées."""

import math
import tempfile
from pathlib import Path

from django.test import TestCase

from .services import formes
from .services.formes import ErreurForme, construire
from .services.geometrie import extraire_geometrie


def aire(contour):
    return contour.polygone.area


class FormesTests(TestCase):
    def test_rectangle_angles_vifs_arrondis_chanfreines(self):
        c = construire("rectangle", {"largeur": 200, "hauteur": 100})
        self.assertEqual((c.dimensions, aire(c)), ((200, 100), 20000))
        arrondi = construire("rectangle", {"largeur": 200, "hauteur": 100, "rayon_angle": 10})
        self.assertAlmostEqual(aire(arrondi), 20000 - (4 - math.pi) * 100, delta=3)
        self.assertEqual(tuple(round(d, 6) for d in arrondi.dimensions), (200, 100))
        chanfreine = construire("rectangle", {"largeur": 200, "hauteur": 100, "chanfrein": 10})
        self.assertAlmostEqual(aire(chanfreine), 20000 - 4 * 50, places=6)
        self.assertEqual(len(chanfreine.exterieur), 8)
        with self.assertRaises(ErreurForme):
            construire("rectangle", {"largeur": 200, "hauteur": 100, "rayon_angle": 10, "chanfrein": 5})
        with self.assertRaises(ErreurForme):
            construire("rectangle", {"largeur": 200, "hauteur": 100, "rayon_angle": 60})

    def test_disque_anneau_oblong(self):
        d = construire("disque", {"diametre": 100})
        self.assertEqual(tuple(round(x, 6) for x in d.dimensions), (100, 100))
        self.assertAlmostEqual(aire(d), math.pi * 2500, delta=12)
        a = construire("anneau", {"diametre_ext": 100, "diametre_int": 40})
        self.assertEqual(len(a.trous), 1)
        self.assertAlmostEqual(aire(a), math.pi * (2500 - 400), delta=12)
        with self.assertRaises(ErreurForme):
            construire("anneau", {"diametre_ext": 40, "diametre_int": 40})
        o = construire("oblong", {"longueur": 100, "largeur": 40})
        self.assertEqual(tuple(round(x, 6) for x in o.dimensions), (100, 40))
        self.assertAlmostEqual(aire(o), 60 * 40 + math.pi * 400, delta=6)
        with self.assertRaises(ErreurForme):
            construire("oblong", {"longueur": 30, "largeur": 40})

    def test_equerre_u_trapeze(self):
        self.assertEqual(aire(construire("equerre", {"largeur": 200, "hauteur": 150, "epaisseur_horizontale": 40, "epaisseur_verticale": 30})), 200 * 40 + 30 * 110)
        self.assertEqual(aire(construire("u", {"largeur": 200, "hauteur": 150, "epaisseur_fond": 40, "epaisseur_branches": 30})), 200 * 150 - 140 * 110)
        self.assertEqual(aire(construire("trapeze", {"base_grande": 200, "base_petite": 100, "hauteur": 80})), 150 * 80)
        with self.assertRaises(ErreurForme):
            construire("u", {"largeur": 100, "hauteur": 100, "epaisseur_fond": 20, "epaisseur_branches": 50})
        with self.assertRaises(ErreurForme):
            construire("trapeze", {"base_grande": 100, "base_petite": 150, "hauteur": 50})
        with self.assertRaises(ErreurForme):
            construire("equerre", {"largeur": 100, "hauteur": 100, "epaisseur_horizontale": 100, "epaisseur_verticale": 20})

    def test_polygone_regulier(self):
        hexagone = construire("polygone", {"nb_cotes": 6, "diametre": 100})
        self.assertAlmostEqual(aire(hexagone), 3 * math.sqrt(3) / 2 * 50 ** 2, places=4)
        self.assertEqual(len(hexagone.exterieur), 6)
        self.assertAlmostEqual(hexagone.dimensions[1], 100, places=6)
        with self.assertRaises(ErreurForme):
            construire("polygone", {"nb_cotes": 2, "diametre": 100})
        with self.assertRaises(ErreurForme):
            construire("polygone", {"nb_cotes": 6.5, "diametre": 100})

    def test_platines(self):
        grille = construire("platine_grille", {"largeur": 200, "hauteur": 150, "nb_x": 3, "nb_y": 2, "pas_x": 70, "pas_y": 90, "diametre_trou": 13})
        self.assertEqual(len(grille.trous), 6)
        centres = sorted((round(sum(x for x, _ in t) / len(t), 3), round(sum(y for _, y in t) / len(t), 3)) for t in grille.trous)
        self.assertEqual(centres[0], (30.0, 30.0))
        self.assertEqual(centres[-1], (170.0, 120.0))
        with self.assertRaises(ErreurForme):
            construire("platine_grille", {"largeur": 100, "hauteur": 100, "nb_x": 3, "nb_y": 1, "pas_x": 90, "pas_y": 0, "diametre_trou": 13})
        with self.assertRaises(ErreurForme):  # trous qui se chevauchent
            construire("platine_grille", {"largeur": 200, "hauteur": 100, "nb_x": 3, "nb_y": 1, "pas_x": 10, "pas_y": 0, "diametre_trou": 13})
        cercle = construire("platine_cercle", {"largeur": 200, "hauteur": 200, "nb_trous": 6, "diametre_cercle": 120, "diametre_trou": 13, "angle_depart": 0})
        self.assertEqual(len(cercle.trous), 6)
        with self.assertRaises(ErreurForme):
            construire("platine_cercle", {"largeur": 100, "hauteur": 100, "nb_trous": 4, "diametre_cercle": 160, "diametre_trou": 13})

    def test_bride_et_flasque(self):
        b = construire("bride", {"diametre_ext": 165, "alesage": 61, "diametre_percage": 125, "nb_trous": 4, "diametre_trou": 18})
        self.assertEqual(len(b.trous), 5)
        with self.assertRaises(ErreurForme):
            construire("bride", {"diametre_ext": 165, "alesage": 61, "diametre_percage": 170, "nb_trous": 4, "diametre_trou": 18})
        with self.assertRaises(ErreurForme):  # trous sur l'alésage
            construire("bride", {"diametre_ext": 165, "alesage": 100, "diametre_percage": 105, "nb_trous": 4, "diametre_trou": 18})
        pleine = construire("bride", {"diametre_ext": 165, "alesage": 0, "diametre_percage": 125, "nb_trous": 4, "diametre_trou": 18})
        self.assertEqual(len(pleine.trous), 4)
        f = construire("flasque", {"diametre": 300, "alesage": 80, "nb_trous_1": 8, "cercle_1": 240, "diametre_trou_1": 14, "nb_trous_2": 4, "cercle_2": 140, "diametre_trou_2": 10})
        self.assertEqual(len(f.trous), 1 + 8 + 4)
        sans_second = construire("flasque", {})
        self.assertEqual(len(sans_second.trous), 1 + 8)

    def test_valeurs_invalides(self):
        with self.assertRaises(ErreurForme):
            construire("disque", {"diametre": "abc"})
        with self.assertRaises(ErreurForme):
            construire("disque", {"diametre": 0})
        with self.assertRaises(ErreurForme):
            construire("inconnue", {})
        self.assertEqual(construire("disque", {"diametre": "100,5"}).dimensions[0] > 100, True)  # virgule décimale

    def test_dxf_relu_par_l_import(self):
        c = construire("platine_grille", {"largeur": 200, "hauteur": 150, "nb_x": 2, "nb_y": 2, "pas_x": 150, "pas_y": 100, "diametre_trou": 13})
        with tempfile.TemporaryDirectory() as dossier:
            chemin = Path(dossier) / "forme.dxf"
            chemin.write_bytes(formes.dxf_bytes(c))
            lu = extraire_geometrie(str(chemin), "dxf")
        self.assertAlmostEqual(lu.largeur_mm, 200, places=2)
        self.assertAlmostEqual(lu.hauteur_mm, 150, places=2)
        self.assertEqual(len(lu.holes), 4)
        self.assertAlmostEqual(lu.surface_mm2, aire(c), delta=1)

    def test_noms_suggeres(self):
        self.assertEqual(formes.nom_suggere("disque", {"diametre": 100}), "Disque Ø100")
        self.assertEqual(formes.nom_suggere("rectangle", {"largeur": 200, "hauteur": 100.5}), "Rectangle 200×100.5")
        self.assertEqual(formes.nom_suggere("platine_grille", {}), "Platine 200×150")

    def test_catalogue_complet(self):
        cat = formes.catalogue()
        self.assertEqual(len(cat["familles"]), 14)
        self.assertEqual({f["groupe"] for f in cat["familles"]}, {"Formes simples", "Platines et brides", "Cotes normalisées"})
        self.assertIn(50, cat["normalisees"]["dn"])
        self.assertTrue(cat["normalisees"]["brides_non_verifiees"])


class CotesNormaliseesTests(TestCase):
    def test_table_chargee_par_la_migration(self):
        from .models import NormeCote

        self.assertEqual(NormeCote.objects.filter(famille="bride_en1092").count(), 30)
        self.assertGreater(NormeCote.objects.filter(famille="rondelle").count(), 30)
        self.assertFalse(NormeCote.objects.filter(verifie=True).exists())

    def test_bride_en1092_dn50_pn16(self):
        c = construire("bride_en1092", {"dn": 50, "pn": "PN16", "type_bride": "01", "jeu_alesage": 1})
        self.assertEqual(tuple(round(x) for x in c.dimensions), (165, 165))
        self.assertEqual(len(c.trous), 1 + 4)
        alesage = min(c.trous, key=lambda t: -(max(x for x, _ in t) - min(x for x, _ in t)))
        self.assertAlmostEqual(max(x for x, _ in alesage) - min(x for x, _ in alesage), 61.3, delta=0.05)
        self.assertEqual(formes.nom_suggere("bride_en1092", {"dn": 50, "pn": "PN16"}), "Bride DN50 PN16 type 01")

    def test_bride_pleine_type_05_et_dn200_pn16_douze_trous(self):
        pleine = construire("bride_en1092", {"dn": 100, "pn": "PN10", "type_bride": "05"})
        self.assertEqual(len(pleine.trous), 8)
        grande = construire("bride_en1092", {"dn": 200, "pn": "PN16"})
        self.assertEqual(len(grande.trous), 1 + 12)

    def test_bride_inconnue_ou_type_invalide(self):
        with self.assertRaises(ErreurForme):
            construire("bride_en1092", {"dn": 51, "pn": "PN16"})
        with self.assertRaises(ErreurForme):
            construire("bride_en1092", {"dn": 50, "pn": "PN16", "type_bride": "11"})

    def test_rondelle_iso_7089_m10(self):
        c = construire("rondelle", {"norme": "ISO 7089", "taille": "M10"})
        self.assertEqual(tuple(round(x, 3) for x in c.dimensions), (20.0, 20.0))
        self.assertEqual(len(c.trous), 1)
        with self.assertRaises(ErreurForme):
            construire("rondelle", {"norme": "ISO 7089", "taille": "M99"})

    def test_options_normalisees(self):
        o = formes.options_normalisees()
        self.assertEqual(o["pn"], ["PN10", "PN16"])
        self.assertEqual(o["dn"][0], 10)
        self.assertIn("M10", o["tailles_rondelles"]["ISO 7089"])
        self.assertEqual(set(o["normes_rondelles"]), {"ISO 7089", "ISO 7091", "ISO 7093", "ISO 7094"})
