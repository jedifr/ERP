"""Panneau « Pièces à découper » de la fiche devis : import DXF, article fabriqué, réglages, verdict."""

import datetime
import json
import tempfile
from decimal import Decimal
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
        self.assertEqual(piece.pas_rotation_deg, 90)  # rotations autorisées par défaut (sens de matière libre)
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
        r = self.client.post(url, {"rotation": ""})
        piece.refresh_from_db()
        self.assertIsNone(piece.pas_rotation_deg)  # sens imposé
        self.assertEqual(self.client.post(url, {"rotation": "45"}).status_code, 200)
        for donnees in ({"rotation": "30"}, {"epaisseur": "abc"}, {"epaisseur": "-2"}, {"quantite": "0"}, {"quantite": "x"}, {"procede": "plasma"}, {"matiere": "Inconnue"}, {"nom": " "}, {"gaz_coupe": "H2"}):
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


class ImbricationDevisTests(TestCase):
    """Étape 2 : imbrication de toutes les pièces d'un groupe (matière, épaisseur, procédé), formats comparés, coût réparti."""

    def setUp(self):
        self._media = tempfile.TemporaryDirectory()
        self.addCleanup(self._media.cleanup)
        surcharge = override_settings(MEDIA_ROOT=self._media.name)
        surcharge.enable()
        self.addCleanup(surcharge.disable)
        self.user = get_user_model().objects.create_superuser("imb-admin", "i@example.com", "pass-mot-de-passe-22")
        self.client.force_login(self.user)
        tiers = Tiers.objects.create(code="CLI-IMB", raison_sociale="Client Imbrication", type_tiers=Tiers.TypeTiers.CLIENT)
        self.devis = Devis.objects.create(numero="DEV-IMB-1", client=tiers, date_creation=datetime.date(2026, 10, 7))
        self.matiere = Matiere.objects.create(nom="S235", densite=7.85)
        self.tole = Article.objects.create(
            reference="TOLE-S235-10", libelle="Tôle S235 10 mm", nature=Article.Nature.MATIERE_PREMIERE, matiere=self.matiere, epaisseur=10,
            unite_cout=Article.UniteCout.SURFACE, cout_unitaire=60,
        )
        self.url = f"/admin/chiffrage/devis/{self.devis.pk}/pieces/"

    def piece(self, nom, quantite=4, epaisseur="10", matiere="S235", procede="laser"):
        r = self.client.post(self.url + "importer/", {"fichier": SimpleUploadedFile(f"{nom}.dxf", (DONNEES / "piece_calibrage.dxf").read_bytes())})
        piece = PieceDecoupe.objects.get(pk=r.json()["piece_id"])
        self.client.post(f"{self.url}{piece.pk}/enregistrer/", {"matiere": matiere, "epaisseur": epaisseur, "procede": procede, "quantite": str(quantite)})
        return PieceDecoupe.objects.get(pk=piece.pk)

    def imbriquer(self, choix=None):
        return self.client.post(self.url + "imbrication/", data=json.dumps({"choix": choix or {}}), content_type="application/json")

    def test_groupe_imbrique_ensemble_avec_formats_compares(self):
        a, b = self.piece("a", 4), self.piece("b", 6)
        r = self.imbriquer()
        self.assertEqual(r.status_code, 200)
        html = r.json()["html"]
        self.assertIn("Imbrication — S235 · 10 mm · laser", html)
        self.assertIn("2 pièces", html)
        self.assertIn("Comparaison des formats de tôle", html)
        self.assertIn("TOLE-S235-10", html)
        self.assertIn("le moins cher", html)
        self.assertIn("Écart entre pièces : <strong>10 mm</strong>", html)
        self.assertIn("<svg", html)
        self.assertNotIn("Aucune tôle correspondante", html)

    def test_service_repartit_le_cout_et_choisit_le_moins_cher(self):
        from decoupe.models import FormatTole
        from decoupe.services import imbrication_devis as imb
        from decoupe.services.devis_pieces import pieces_du_devis

        self.piece("a", 4), self.piece("b", 6)
        groupes, a_regler = imb.grouper(pieces_du_devis(self.devis))
        self.assertEqual((len(groupes), a_regler), (1, []))
        groupe = groupes[0]
        self.assertEqual(imb.toles_possibles(groupe), [self.tole])
        formats = FormatTole.objects.filter(actif=True)
        lignes, meilleur = imb.comparer_formats(groupe, list(formats), 5, 0, self.tole)
        valides = [r for _, r, _ in lignes if r]
        self.assertTrue(valides)
        r = next(r for f, r, _ in lignes if f == meilleur)
        self.assertEqual(r.cout_total, min(v.cout_total for v in valides))  # à coût égal, la moindre surface consommée l'emporte
        self.assertEqual(sum((x["total"] for x in r.par_piece), 0), r.cout_total)  # le lot est entièrement réparti (à 1 centime près)  # noqa: E501
        self.assertEqual(r.espacement_mm, 10)
        # une chute récupérable de 100 % ne facture plus que les pièces
        _, plein = imb.comparer_formats(groupe, [meilleur], 5, 0, self.tole)
        r100 = imb.imbriquer_groupe(groupe, meilleur, 5, 100, self.tole)
        self.assertLessEqual(r100.cout_total, r.cout_total)
        self.assertAlmostEqual(r100.surface_facturee_mm2, r100.surface_pieces_mm2, places=3)

    def test_sans_tole_pas_de_cout(self):
        self.tole.delete()
        self.piece("a", 4)
        html = self.imbriquer().json()["html"]
        self.assertIn("Aucune tôle S235 de 10 mm en base", html)
        self.assertIn("Comparaison des formats", html)

    def test_retenir_enregistre_sur_les_pieces(self):
        from decoupe.models import FormatTole

        a, b = self.piece("a", 4), self.piece("b", 6)
        format_tole = FormatTole.objects.get(largeur_mm=1500, longueur_mm=3000)  # tient dans le laser
        r = self.client.post(
            self.url + "imbrication/retenir/",
            data=json.dumps({"cle": f"S235|10|laser", "tole": self.tole.pk, "format": format_tole.pk, "marge": "8", "chute": "25"}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 200)
        for piece in (a, b):
            piece.refresh_from_db()
            self.assertEqual((piece.tole, piece.format_tole, piece.marge_bord_mm, float(piece.taux_chute_recuperable)), (self.tole, format_tole, 8.0, 25.0))
            self.assertFalse(piece.imbrication_chiffrage)  # le chiffrage par imbrication viendra à l'étape suivante
        html = self.imbriquer().json()["html"]  # sans choix : valeurs retenues
        self.assertIn("✓ Retenu", html)
        self.assertIn('value="8.0"', html)
        self.assertEqual(self.client.post(self.url + "imbrication/retenir/", data=json.dumps({"cle": "inconnu"}), content_type="application/json").status_code, 404)
        self.assertEqual(self.client.post(self.url + "imbrication/retenir/", data=json.dumps({"cle": "S235|10|laser"}), content_type="application/json").status_code, 400)

    def test_pieces_exclues_et_a_regler(self):
        self.piece("ok", 2)
        self.piece("trop-fin", 2, epaisseur="7")  # 7 mm : hors base laser
        r = self.client.post(self.url + "importer/", {"fichier": SimpleUploadedFile("sans.dxf", (DONNEES / "piece_calibrage.dxf").read_bytes())})
        html = self.imbriquer().json()["html"]
        self.assertIn("Exclue de l'imbrication", html)
        self.assertIn("Non réalisable au laser", html)
        self.assertIn("À régler avant imbrication", html)
        self.assertIn("matière ou épaisseur à choisir", html)

    def test_devis_valide_et_droits(self):
        self.piece("a", 4)
        Devis.objects.filter(pk=self.devis.pk).update(statut=Devis.Statut.VALIDE)
        html = self.imbriquer().json()["html"]
        self.assertIn("Comparaison des formats", html)
        self.assertNotIn("dp-retenir", html)
        self.assertEqual(self.client.post(self.url + "imbrication/retenir/", data=json.dumps({"cle": "S235|10|laser"}), content_type="application/json").status_code, 409)
        simple = get_user_model().objects.create_user("simple-imb", "s@example.com", "pass-mot-de-passe-23", is_staff=True)
        self.client.force_login(simple)
        self.assertEqual(self.imbriquer().status_code, 403)
        self.assertEqual(self.client.get(self.url + "imbrication/").status_code, 405)

    def test_requete_invalide_et_page(self):
        self.assertEqual(self.client.post(self.url + "imbrication/", data="pas du json", content_type="application/json").status_code, 400)
        page = self.client.get(f"/admin/chiffrage/devis/{self.devis.pk}/change/")
        self.assertContains(page, 'id="dp-imbrication"')
        self.assertContains(page, "/pieces/imbrication/retenir/")


class ChiffrageDevisTests(TestCase):
    """Étape 3 : les pièces prêtes deviennent des lignes du devis, chiffrées avec la matière répartie et le temps de coupe."""

    def setUp(self):
        from commercial.models import TauxTVA
        from decoupe.models import FormatTole
        from technique.models import TarifPoste

        self._media = tempfile.TemporaryDirectory()
        self.addCleanup(self._media.cleanup)
        surcharge = override_settings(MEDIA_ROOT=self._media.name)
        surcharge.enable()
        self.addCleanup(surcharge.disable)
        self.user = get_user_model().objects.create_superuser("chif-admin", "c@example.com", "pass-mot-de-passe-24")
        self.client.force_login(self.user)
        TauxTVA.objects.create(nom="Normal chiffrage pièces", taux=20, est_defaut=True)
        tiers = Tiers.objects.create(code="CLI-CHF", raison_sociale="Client Chiffrage", type_tiers=Tiers.TypeTiers.CLIENT)
        self.devis = Devis.objects.create(numero="DEV-CHF-1", client=tiers, date_creation=datetime.date(2026, 10, 7))
        self.matiere = Matiere.objects.create(nom="S235", densite=7.85)
        self.tole = Article.objects.create(
            reference="TOLE-CHF", nature=Article.Nature.MATIERE_PREMIERE, matiere=self.matiere, epaisseur=10,
            unite_cout=Article.UniteCout.SURFACE, cout_unitaire=60, taux_marge_defaut=25,
        )
        self.poste = PosteTravail.objects.create(nom="Laser chiffrage", mode_calcul=PosteTravail.ModeCalcul.HORAIRE)
        TarifPoste.objects.create(poste=self.poste, cout_horaire=120, date_debut=datetime.date(2020, 1, 1))
        ParametreCoupe.objects.filter(procede="laser").update(poste=self.poste)
        self.format = FormatTole.objects.filter(actif=True).order_by("-longueur_mm").last()
        self.url = f"/admin/chiffrage/devis/{self.devis.pk}/pieces/"

    def piece(self, nom, quantite):
        r = self.client.post(self.url + "importer/", {"fichier": SimpleUploadedFile(f"{nom}.dxf", (DONNEES / "piece_calibrage.dxf").read_bytes())})
        piece = PieceDecoupe.objects.get(pk=r.json()["piece_id"])
        self.client.post(f"{self.url}{piece.pk}/enregistrer/", {"matiere": "S235", "epaisseur": "10", "procede": "laser", "quantite": str(quantite)})
        return PieceDecoupe.objects.get(pk=piece.pk)

    def retenir(self):
        return self.client.post(
            self.url + "imbrication/retenir/",
            data=json.dumps({"cle": "S235|10|laser", "tole": self.tole.pk, "format": self.format.pk, "marge": "5", "chute": "0"}),
            content_type="application/json",
        )

    def ajouter(self):
        return self.client.post(self.url + "ajouter-au-devis/")

    def test_apercu_avant_et_apres_le_choix_du_format(self):
        self.piece("a", 4)
        html = self.client.post(self.url + "imbrication/", data="{}", content_type="application/json").json()["html"]
        self.assertIn("Chiffrage des pièces", html)
        self.assertIn("retenez la tôle et le format", html)
        self.retenir()
        html = self.client.post(self.url + "imbrication/", data="{}", content_type="application/json").json()["html"]
        self.assertIn("Prix unitaire HT", html)
        self.assertNotIn("À compléter", html)

    def test_ajout_chiffre_les_lignes(self):
        from decoupe.services import imbrication_devis as imb
        from decoupe.services.devis_pieces import pieces_du_devis

        from .models import DevisLigneOperation
        from .moteur import cout_matiere_article

        a, b = self.piece("a", 4), self.piece("b", 6)
        self.retenir()
        reponse = self.ajouter()
        self.assertEqual(reponse.status_code, 200, reponse.content)
        self.assertEqual((reponse.json()["ajoutees"], reponse.json()["mises_a_jour"]), (2, 0))
        lignes = {l.article_id: l for l in self.devis.lignes.all()}
        self.assertEqual(sorted(l.quantite for l in lignes.values()), [4.0, 6.0])
        for piece in (a, b):
            piece.refresh_from_db()
            self.assertTrue(piece.imbrication_chiffrage)
            ligne = lignes[piece.article_id]
            self.assertGreater(ligne.cout_matiere_calcule, 0)
            self.assertEqual(float(ligne.taux_marge_matiere_applique), 25.0)  # marge reprise de la tôle
            self.assertEqual(ligne.prix_vente_matiere, (ligne.cout_matiere_calcule * Decimal("1.25")).quantize(Decimal("0.01")))
            self.assertGreater(ligne.prix_vente_operations, 0)  # temps de coupe × tarif du poste
            self.assertTrue(DevisLigneOperation.objects.filter(devis_ligne=ligne, poste=self.poste).exists())
        # la matière des deux lignes = coût du lot imbriqué (réparti au prorata des surfaces)
        groupes, _ = imb.grouper(pieces_du_devis(self.devis))
        lot = imb.imbriquer_groupe(groupes[0], self.format, 5, 0, self.tole)
        total_lignes = sum(l.cout_matiere_calcule for l in lignes.values())
        self.assertLess(abs(total_lignes - lot.cout_total), Decimal("0.05"))
        self.assertEqual(cout_matiere_article(a.article, 4, devis=self.devis), lignes[a.article_id].cout_matiere_calcule)

    def test_ajout_idempotent_et_quantite_mise_a_jour(self):
        a = self.piece("a", 4)
        self.retenir()
        self.ajouter()
        avant = self.devis.lignes.get().cout_matiere_calcule
        self.client.post(f"{self.url}{a.pk}/enregistrer/", {"quantite": "8"})
        reponse = self.ajouter().json()
        self.assertEqual((reponse["ajoutees"], reponse["mises_a_jour"]), (0, 1))
        ligne = self.devis.lignes.get()
        self.assertEqual(ligne.quantite, 8.0)
        self.assertGreater(ligne.cout_matiere_calcule, avant)

    def test_pas_de_ligne_sans_poste_ni_format(self):
        self.piece("a", 4)
        ParametreCoupe.objects.filter(procede="laser").update(poste=None)
        Gamme.objects.all().delete()
        reponse = self.ajouter().json()  # format non retenu
        self.assertEqual((reponse["ajoutees"], reponse["resultats"][0]["etat"]), (0, "ignorée"))
        self.assertIn("retenez la tôle", reponse["resultats"][0]["raison"])
        self.retenir()
        reponse = self.ajouter().json()  # format retenu mais aucun poste : gamme vide, donc pas de prix honnête
        self.assertEqual(reponse["ajoutees"], 0)
        self.assertIn("poste de travail", reponse["resultats"][0]["raison"])
        self.assertFalse(self.devis.lignes.exists())

    def test_devis_valide_et_droits(self):
        self.piece("a", 4)
        self.retenir()
        simple = get_user_model().objects.create_user("simple-chf", "s@example.com", "pass-mot-de-passe-25", is_staff=True)
        for codename in ("change_devis", "add_piecedecoupe", "add_article"):
            simple.user_permissions.add(Permission.objects.get(codename=codename))
        self.client.force_login(simple)
        self.assertEqual(self.ajouter().status_code, 403)  # ni droit d'ajouter une ligne
        self.client.force_login(self.user)
        Devis.objects.filter(pk=self.devis.pk).update(statut=Devis.Statut.VALIDE)
        self.assertEqual(self.ajouter().status_code, 409)
        self.assertEqual(self.client.get(self.url + "ajouter-au-devis/").status_code, 405)


class CapaciteMachineEtFeuillesTests(TestCase):
    """Capacité de coupe par machine (laser 3000 × 1500, jet d'eau 4000 × 2000) et affichage de toutes les feuilles."""

    # mêmes préparatifs et outils que les tests d'imbrication (sans rejouer leurs tests)
    setUp = ImbricationDevisTests.setUp
    piece = ImbricationDevisTests.piece
    imbriquer = ImbricationDevisTests.imbriquer

    def test_capacites_par_defaut(self):
        from decoupe.models import FormatTole, ReglageProcede

        self.assertEqual(ReglageProcede.pour("laser").libelle_capacite, "3000 × 1500 mm")
        self.assertEqual(ReglageProcede.pour("jet_eau").libelle_capacite, "4000 × 2000 mm")
        laser, jet = ReglageProcede.pour("laser"), ReglageProcede.pour("jet_eau")
        petit, grand, tres_grand = FormatTole(largeur_mm=1500, longueur_mm=3000), FormatTole(largeur_mm=2000, longueur_mm=4000), FormatTole(largeur_mm=2000, longueur_mm=6000)
        self.assertEqual((laser.accepte(petit), laser.accepte(grand), laser.accepte(tres_grand)), (True, False, False))
        self.assertEqual((jet.accepte(petit), jet.accepte(grand), jet.accepte(tres_grand)), (True, True, False))
        self.assertTrue(laser.accepte(FormatTole(largeur_mm=3000, longueur_mm=1500)))  # tôle présentée dans l'autre sens
        ReglageProcede.objects.filter(procede="laser").update(capacite_largeur_mm=0)
        self.assertTrue(ReglageProcede.pour("laser").accepte(tres_grand))  # 0 : pas de limite

    def test_laser_exclut_les_formats_trop_grands_mais_pas_le_jet_d_eau(self):
        self.piece("l", 4)
        self.piece("j", 4, procede="jet_eau")
        html = self.imbriquer().json()["html"].replace("&#x27;", "'")
        laser, jet = html.split("Imbrication — S235 · 10 mm · jet d'eau")
        self.assertIn("Dépasse la capacité du laser (3000 × 1500 mm)", laser)
        self.assertEqual(laser.count("Dépasse la capacité du laser"), 2)  # 6000 × 2000 et 4000 × 2000
        self.assertEqual(jet.count("Dépasse la capacité du jet d'eau (4000 × 2000 mm)"), 1)  # seul le 6000 × 2000
        self.assertNotIn("Dépasse la capacité du laser", jet)

    def test_retenir_un_format_trop_grand_est_refuse_et_le_choix_automatique_l_evite(self):
        from decoupe.models import FormatTole

        self.piece("l", 4)
        grand = FormatTole.objects.get(largeur_mm=2000, longueur_mm=4000)
        r = self.client.post(
            self.url + "imbrication/retenir/",
            data=json.dumps({"cle": "S235|10|laser", "tole": self.tole.pk, "format": grand.pk, "marge": "5", "chute": "0"}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("capacité du laser", r.json()["detail"])
        html = self.imbriquer({"S235|10|laser": {"format": grand.pk}}).json()["html"]  # format demandé impossible : on retombe sur le meilleur compatible
        self.assertIn("Feuille 1 sur", html)
        self.assertNotIn("Format 4000 × 2000", html)

    def test_aucun_format_compatible(self):
        from decoupe.models import FormatTole, ReglageProcede

        self.piece("l", 4)
        FormatTole.objects.filter(longueur_mm__lte=3000).update(actif=False)
        html = self.imbriquer().json()["html"]
        self.assertIn("Aucun format de tôle actif ne tient dans le laser (3000 × 1500 mm)", html)

    def test_toutes_les_feuilles_sont_dessinees(self):
        from decoupe.models import FormatTole

        self.piece("beaucoup", 60)
        FormatTole.objects.exclude(largeur_mm=1250, longueur_mm=2500).update(actif=False)  # un seul format : 2500 × 1250
        html = self.imbriquer().json()["html"]
        import re

        total = int(re.search(r"Feuille 1 sur (\d+)", html).group(1))
        self.assertGreater(total, 1)
        self.assertEqual(html.count("<figure class=\"dp-feuille\">"), total)
        self.assertIn(f"Feuille {total} sur {total}", html)
        self.assertIn("entamée : le reste est une chute récupérable", html)
