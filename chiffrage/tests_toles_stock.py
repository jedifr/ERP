"""Imbrication d'un devis : seuls les formats des tôles en matière première sont proposés, chacun chiffré avec sa tôle."""

import json

from django.test import TestCase

from decoupe.models import FormatTole, PieceDecoupe
from technique.models import Article, RegleCreationTole, format_tole_depuis_texte

from . import tests_pieces as base


class FormatDepuisTexteTests(TestCase):
    def test_lecture_des_dimensions(self):
        f = FormatTole.objects.get(largeur_mm=1500, longueur_mm=3000)
        for texte in ("S235-3 1500x3000", "5754-5 3000X1500", "S235 - 1500 x 3000 x 3", "3x1500x3000", "Tôle 1500,0 × 3000"):
            self.assertEqual(format_tole_depuis_texte(texte), f, texte)
        for texte in ("TOLE-S235-10", "S235 1600x3200", "", None):
            self.assertIsNone(format_tole_depuis_texte(texte), texte)

    def test_format_renseigne_a_l_enregistrement_d_une_tole(self):
        tole = Article.objects.create(reference="S235-3 1250x2500", nature=Article.Nature.MATIERE_PREMIERE, epaisseur=3)
        self.assertEqual((tole.format_tole.largeur_mm, tole.format_tole.longueur_mm), (1250, 2500))
        autre = Article.objects.create(reference="VIS 1500x3000", nature=Article.Nature.COMPOSANT)
        self.assertIsNone(autre.format_tole)  # seulement les tôles


class ImbricationParToleTests(TestCase):
    setUp = base.ImbricationDevisTests.setUp
    piece = base.ImbricationDevisTests.piece
    imbriquer = base.ImbricationDevisTests.imbriquer

    def creer_tole(self, reference, cout):
        return Article.objects.create(
            reference=reference, nature=Article.Nature.MATIERE_PREMIERE, matiere=self.matiere, epaisseur=10,
            unite_cout=Article.UniteCout.SURFACE, cout_unitaire=cout,
        )

    def test_seuls_les_formats_des_toles_sont_compares(self):
        grande, petite = self.creer_tole("S235-10 1500x3000", 60), self.creer_tole("S235-10 1000x2000", 80)
        self.piece("a", 4)
        html = self.imbriquer().json()["html"]
        self.assertIn("Comparaison des tôles disponibles en matière première", html)
        self.assertNotIn('data-i="tole"', html)  # plus de choix de tôle séparé : il suit le format
        lignes = html.count('data-i="format"')
        self.assertEqual(lignes, 2)
        for t in (grande, petite):
            self.assertIn(f'data-valeur="{t.format_tole_id}"', html)
        self.assertNotIn(f'data-valeur="{FormatTole.objects.get(largeur_mm=1250, longueur_mm=2500).pk}"', html)
        self.assertIn("Tôle sans format, non proposée", html)  # TOLE-S235-10 (sans dimensions)

    def test_chaque_format_est_chiffre_avec_sa_tole(self):
        from decoupe.services import imbrication_devis as imb
        from decoupe.services.devis_pieces import pieces_du_devis

        grande, petite = self.creer_tole("S235-10 1500x3000", 60), self.creer_tole("S235-10 1000x2000", 600)
        self.piece("a", 2)
        groupe = imb.grouper(pieces_du_devis(self.devis))[0][0]
        formats, toles = imb.toles_par_format(imb.toles_possibles(groupe))
        self.assertEqual([f.pk for f in formats], [grande.format_tole_id, petite.format_tole_id])
        lignes, meilleur, _, _ = imb.comparer_par_paliers(groupe, formats, 5, 0, None, toles=toles, tout=True)
        couts = {f.pk: r.cout_total for f, r, _ in lignes}
        self.assertEqual(meilleur.pk, grande.format_tole_id)  # 600 €/m² : la petite tôle n'est pas la moins chère
        self.assertGreater(couts[petite.format_tole_id], couts[grande.format_tole_id])

    def test_retenir_un_format_retient_sa_tole(self):
        grande, petite = self.creer_tole("S235-10 1500x3000", 60), self.creer_tole("S235-10 1000x2000", 80)
        a = self.piece("a", 4)
        r = self.client.post(
            self.url + "imbrication/retenir/", content_type="application/json",
            data=json.dumps({"cle": "S235|10|laser", "format": petite.format_tole_id, "marge": "5", "chute": "0"}),
        )
        self.assertEqual(r.status_code, 200)
        a = PieceDecoupe.objects.get(pk=a.pk)
        self.assertEqual((a.tole_id, a.format_tole_id), (petite.pk, petite.format_tole_id))
        html = self.imbriquer().json()["html"]
        self.assertIn("✓ Retenu", html)
        self.assertIn("S235-10 1000x2000", html)
        # un format sans tôle en matière première est refusé
        autre = FormatTole.objects.get(largeur_mm=1250, longueur_mm=2500)
        r = self.client.post(self.url + "imbrication/retenir/", content_type="application/json", data=json.dumps({"cle": "S235|10|laser", "format": autre.pk}))
        self.assertEqual(r.status_code, 400)

    def test_tole_creee_par_regle_porte_son_format(self):
        regle = RegleCreationTole.objects.create(nom="Acier", unite_cout=Article.UniteCout.SURFACE, cout_unitaire=50)
        f = FormatTole.objects.get(largeur_mm=1250, longueur_mm=2500)
        tole = regle.creer_tole(self.matiere, 4, f)
        self.assertEqual(tole.format_tole, f)
