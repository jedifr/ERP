"""Panneau « Pièces à découper » de la fiche devis : import DXF, article fabriqué, réglages, verdict."""

import datetime
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from codification.models import RegleCodification
from commercial.models import Tiers
from decoupe.models import ParametreCoupe, PieceDecoupe
from technique.models import Article, Gamme, Matiere, PosteTravail

from .models import Devis, DevisLigne

DONNEES = Path(__file__).resolve().parent.parent / "decoupe" / "tests_data"


class PanneauPiecesDevisTests(TestCase):
    def setUp(self):
        self._media = tempfile.TemporaryDirectory()
        self.addCleanup(self._media.cleanup)
        surcharge = override_settings(MEDIA_ROOT=self._media.name)
        surcharge.enable()
        self.addCleanup(surcharge.disable)
        self.user = get_user_model().objects.create_superuser("pieces-admin", "p@example.com", "pass-mot-de-passe-20")
        self.client.force_login(self.user)
        tiers = Tiers.objects.create(code="CLI-PCS", raison_sociale="Client Pièces", type_tiers=Tiers.TypeTiers.CLIENT)
        self.devis = Devis.objects.create(numero="DEV-PCS-1", client=tiers, date_creation=datetime.date(2026, 10, 7))
        self.matiere = Matiere.objects.create(nom="S235", densite=7.85)
        self.url = f"/admin/chiffrage/devis/{self.devis.pk}/pieces/"

    def fichier(self, nom="plaque.dxf"):
        return SimpleUploadedFile(nom, (DONNEES / "piece_calibrage.dxf").read_bytes(), content_type="application/dxf")

    def importer(self, nom="plaque.dxf", **extra):
        return self.client.post(self.url + "importer/", {"fichier": self.fichier(nom), **extra})

    def test_import_cree_la_piece_et_l_article_fabrique(self):
        reponse = self.importer()
        self.assertEqual(reponse.status_code, 200)
        piece = PieceDecoupe.objects.get()
        self.assertEqual((piece.devis, piece.statut, piece.procede, piece.nom, piece.quantite), (self.devis, "ok", "laser", "plaque", 1))
        article = piece.article
        self.assertEqual((article.reference, article.nature, article.libelle), ("DEV-PCS-1-P01", Article.Nature.FABRIQUE, "plaque"))
        self.assertTrue(piece.article_cree_automatiquement)
        html = reponse.json()["html"]
        self.assertIn("DEV-PCS-1-P01", html)
        self.assertIn("Choisissez la matière et l&#x27;épaisseur", html)
        self.assertEqual(self.importer("deuxieme.dxf").status_code, 200)
        self.assertEqual(sorted(Article.objects.values_list("reference", flat=True)), ["DEV-PCS-1-P01", "DEV-PCS-1-P02"])

    def test_codification_article_si_configuree(self):
        RegleCodification.objects.create(entite=RegleCodification.Entite.ARTICLE, prefixe="PD-", nombre_chiffres=4)
        self.importer()
        self.assertEqual(PieceDecoupe.objects.get().article_id, "PD-0001")
        self.assertEqual(RegleCodification.objects.get(pk="article").compteur_actuel, 1)

    def test_fichier_refuse_et_droits(self):
        refus = self.client.post(self.url + "importer/", {"fichier": SimpleUploadedFile("note.txt", b"x")})
        self.assertEqual(refus.status_code, 400)
        self.assertIn(".dxf", refus.json()["detail"])
        self.assertEqual(self.client.post(self.url + "importer/", {}).status_code, 400)
        simple = get_user_model().objects.create_user("simple-pcs", "s@example.com", "pass-mot-de-passe-21", is_staff=True)
        simple.user_permissions.add(Permission.objects.get(codename="change_devis"))
        self.client.force_login(simple)
        self.assertEqual(self.importer().status_code, 403)  # ni pièce ni article à créer
        self.assertEqual(self.client.get(self.url + "importer/").status_code, 405)

    def test_reglages_et_verdict(self):
        self.importer()
        piece = PieceDecoupe.objects.get()
        url = f"{self.url}{piece.pk}/enregistrer/"
        ok = self.client.post(url, {"matiere": "S235", "epaisseur": "10", "procede": "laser", "quantite": "5", "nom": "Platine"})
        self.assertEqual(ok.status_code, 200)
        self.assertIn("Réalisable", ok.json()["html"])
        piece.refresh_from_db()
        self.assertEqual((piece.matiere, piece.epaisseur, piece.quantite, piece.nom), (self.matiere, 10.0, 5, "Platine"))
        self.assertEqual((piece.article.matiere, piece.article.epaisseur, piece.article.libelle), (self.matiere, 10.0, "Platine"))
        self.assertIn("Renseignez le poste", ok.json()["html"])  # base laser sans poste : la gamme n'est pas alimentée
        self.assertFalse(Gamme.objects.exists())
        # 7 mm : hors base laser, donc non réalisable
        refus = self.client.post(url, {"epaisseur": "7"})
        self.assertIn("Non réalisable au laser", refus.json()["html"])
        # le jet d'eau accepte l'épaisseur la plus proche
        jet = self.client.post(url, {"procede": "jet_eau"})
        self.assertIn("Réalisable", jet.json()["html"])
        self.assertIn("la plus proche", jet.json()["html"])

    def test_gamme_alimentee_quand_le_poste_est_connu(self):
        poste = PosteTravail.objects.create(nom="Laser pièces", mode_calcul=PosteTravail.ModeCalcul.HORAIRE)
        ParametreCoupe.objects.filter(procede="laser").update(poste=poste)
        self.importer()
        piece = PieceDecoupe.objects.get()
        reponse = self.client.post(f"{self.url}{piece.pk}/enregistrer/", {"matiere": "S235", "epaisseur": "10"})
        self.assertIn("étape 1 sur Laser pièces", reponse.json()["html"])
        self.assertEqual(Gamme.objects.get(article=piece.article).poste, poste)
        # simple affichage de la fiche : aucune écriture
        Gamme.objects.all().delete()
        self.client.get(f"/admin/chiffrage/devis/{self.devis.pk}/change/")
        self.assertFalse(Gamme.objects.exists())

    def test_valeurs_invalides(self):
        self.importer()
        piece = PieceDecoupe.objects.get()
        url = f"{self.url}{piece.pk}/enregistrer/"
        for donnees in ({"epaisseur": "abc"}, {"epaisseur": "-2"}, {"quantite": "0"}, {"quantite": "x"}, {"procede": "plasma"}, {"matiere": "Inconnue"}, {"nom": " "}, {"gaz_coupe": "H2"}):
            self.assertEqual(self.client.post(url, donnees).status_code, 400, donnees)

    def test_suppression_et_article_conserve(self):
        self.importer("a.dxf")
        self.importer("b.dxf")
        a, b = PieceDecoupe.objects.order_by("pk")
        self.assertEqual(self.client.post(f"{self.url}{a.pk}/supprimer/").json(), {"ok": True, "article_conserve": None})
        self.assertFalse(Article.objects.filter(pk="DEV-PCS-1-P01").exists())
        DevisLigne.objects.create(devis=self.devis, article=b.article, quantite=1)
        reponse = self.client.post(f"{self.url}{b.pk}/supprimer/").json()
        self.assertEqual(reponse["article_conserve"], "DEV-PCS-1-P02")
        self.assertTrue(Article.objects.filter(pk="DEV-PCS-1-P02").exists())
        self.assertFalse(PieceDecoupe.objects.exists())

    def test_devis_valide_verrouille(self):
        self.importer()
        piece = PieceDecoupe.objects.get()
        Devis.objects.filter(pk=self.devis.pk).update(statut=Devis.Statut.VALIDE)
        self.assertEqual(self.importer("c.dxf").status_code, 409)
        self.assertEqual(self.client.post(f"{self.url}{piece.pk}/enregistrer/", {"quantite": "3"}).status_code, 409)
        self.assertEqual(self.client.post(f"{self.url}{piece.pk}/supprimer/").status_code, 409)
        page = self.client.get(f"/admin/chiffrage/devis/{self.devis.pk}/change/")
        self.assertContains(page, "dp-panneau")
        self.assertContains(page, "Devis validé, donc verrouillé")
        self.assertNotContains(page, 'id="dp-drop"')

    def test_page_du_devis(self):
        self.importer()
        page = self.client.get(f"/admin/chiffrage/devis/{self.devis.pk}/change/")
        self.assertContains(page, 'id="dp-drop"')
        self.assertContains(page, "dp-carte")
        self.assertContains(page, "DEV-PCS-1-P01")
        self.assertContains(page, "chiffrage/devis_pieces.")
        ajout = self.client.get("/admin/chiffrage/devis/add/")
        self.assertContains(ajout, "Enregistrez d'abord le devis")
        self.assertNotContains(ajout, 'id="dp-drop"')
