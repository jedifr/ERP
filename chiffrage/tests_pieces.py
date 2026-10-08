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
        self.assertEqual((article.reference, article.nature, article.libelle), ("plaque", Article.Nature.FABRIQUE, "plaque"))
        self.assertTrue(piece.article_cree_automatiquement)
        self.assertEqual(piece.pas_rotation_deg, 90)  # rotations autorisées par défaut (sens de matière libre)
        html = reponse.json()["html"]
        self.assertIn(">plaque</a>", html)
        self.assertIn("Choisissez la matière et l&#x27;épaisseur", html)
        self.assertEqual(self.importer("deuxieme.dxf").status_code, 200)
        self.assertEqual(sorted(Article.objects.values_list("reference", flat=True)), ["deuxieme", "plaque"])

    def test_la_reference_est_le_nom_du_dxf_meme_avec_une_codification(self):
        RegleCodification.objects.create(entite=RegleCodification.Entite.ARTICLE, prefixe="PD-", nombre_chiffres=4)
        self.importer("Plexi 1 (AV INF).dxf")
        self.assertEqual(PieceDecoupe.objects.get().article_id, "Plexi 1 (AV INF)")
        self.assertEqual(RegleCodification.objects.get(pk="article").compteur_actuel, 0)  # le compteur n'avance pas

    def test_reference_deja_prise_et_caracteres_interdits(self):
        self.importer("a:b.dxf")
        self.importer("a:b.dxf")
        self.assertEqual(sorted(Article.objects.values_list("reference", flat=True)), ["a-b", "a-b-2"])

    def test_sans_nom_exploitable_on_retombe_sur_la_codification(self):
        from decoupe.services.devis_pieces import _reference_article

        RegleCodification.objects.create(entite=RegleCodification.Entite.ARTICLE, prefixe="PD-", nombre_chiffres=4)
        self.assertEqual(_reference_article(self.devis, "///"), ("PD-0001", True))
        self.assertEqual(_reference_article(self.devis, None), ("PD-0001", True))

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
        self.assertFalse(Article.objects.filter(pk="a").exists())
        DevisLigne.objects.create(devis=self.devis, article=b.article, quantite=1)
        reponse = self.client.post(f"{self.url}{b.pk}/supprimer/").json()
        self.assertEqual(reponse["article_conserve"], "b")
        self.assertTrue(Article.objects.filter(pk="b").exists())
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
        self.assertContains(page, ">plaque</a>")
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
        self.assertIn("chute de bout", html)


class ImbricationFormeDevisTests(TestCase):
    """L'imbrication selon la forme est réglable par groupe et reportée sur les pièces retenues."""

    setUp = ImbricationDevisTests.setUp
    piece = ImbricationDevisTests.piece
    imbriquer = ImbricationDevisTests.imbriquer

    def test_case_selon_la_forme_et_retenue(self):
        from decoupe.models import FormatTole

        a = self.piece("a", 4)
        html = self.imbriquer().json()["html"]
        self.assertIn("Selon la forme", html)
        self.assertRegex(html, r'data-i="forme"\s+checked')
        html = self.imbriquer({"S235|10|laser": {"forme": "0"}}).json()["html"]
        self.assertNotRegex(html, r'data-i="forme"\s+checked')
        format_tole = FormatTole.objects.get(largeur_mm=1500, longueur_mm=3000)
        self.client.post(
            self.url + "imbrication/retenir/",
            data=json.dumps({"cle": "S235|10|laser", "tole": self.tole.pk, "format": format_tole.pk, "marge": "5", "chute": "0", "forme": "0"}),
            content_type="application/json",
        )
        a.refresh_from_db()
        self.assertFalse(a.imbrication_forme)
        self.assertNotRegex(self.imbriquer().json()["html"], r'data-i="forme"\s+checked')  # valeur retenue

    def test_aucun_format_ne_contient_la_piece(self):
        from decoupe.models import FormatTole

        self.piece("a", 4)
        FormatTole.objects.update(actif=False)
        FormatTole.objects.create(largeur_mm=300, longueur_mm=300, actif=True)
        html = self.imbriquer().json()["html"]
        self.assertIn("Ne tient pas sur 300 × 300", html)  # sans exception : la liste des pièces non placées est bien lue


class FormesParametriquesDevisTests(TestCase):
    """Bibliothèque de formes dans le panneau du devis : aperçu, création de la pièce, modification des cotes."""

    setUp = PanneauPiecesDevisTests.setUp

    def poster(self, chemin, donnees):
        return self.client.post(self.url + chemin, data=json.dumps(donnees), content_type="application/json")

    def test_apercu_svg_et_erreur_de_cotes(self):
        reponse = self.poster("formes/apercu/", {"famille": "disque", "cotes": {"diametre": "120"}})
        self.assertEqual(reponse.status_code, 200)
        corps = reponse.json()
        self.assertEqual((corps["nom"], corps["largeur"], corps["hauteur"], corps["trous"]), ("Disque Ø120", 120.0, 120.0, 0))
        self.assertIn("<svg", corps["svg"])
        refus = self.poster("formes/apercu/", {"famille": "anneau", "cotes": {"diametre_ext": 40, "diametre_int": 50}})
        self.assertEqual(refus.status_code, 400)
        self.assertIn("diamètre intérieur", refus.json()["detail"])
        self.assertEqual(self.poster("formes/apercu/", {"cotes": {}}).status_code, 400)

    def test_creation_de_la_piece_et_de_l_article(self):
        reponse = self.poster("formes/ajouter/", {"famille": "bride_en1092", "cotes": {"dn": "50", "pn": "PN16", "type_bride": "01"}, "quantite": 4, "procede": "jet_eau"})
        self.assertEqual(reponse.status_code, 200)
        piece = PieceDecoupe.objects.get()
        self.assertEqual((piece.devis, piece.nom, piece.quantite, piece.procede, piece.statut), (self.devis, "Bride DN50 PN16 type 01", 4, "jet_eau", "ok"))
        self.assertEqual(piece.parametres_forme["famille"], "bride_en1092")
        self.assertEqual((round(piece.largeur_mm), round(piece.hauteur_mm), piece.nb_contours_interieurs), (165, 165, 5))
        self.assertEqual(piece.article.nature, Article.Nature.FABRIQUE)
        self.assertEqual(piece.pas_rotation_deg, 90)
        html = reponse.json()["html"]
        self.assertIn("Forme paramétrique", html)
        self.assertIn("Modifier la forme", html)

    def test_cotes_invalides_ne_creent_rien(self):
        reponse = self.poster("formes/ajouter/", {"famille": "bride", "cotes": {"diametre_ext": 100, "alesage": 20, "diametre_percage": 150, "nb_trous": 4, "diametre_trou": 10}})
        self.assertEqual(reponse.status_code, 400)
        self.assertEqual((PieceDecoupe.objects.count(), Article.objects.count()), (0, 0))

    def test_modification_des_cotes_garde_la_piece_et_l_article(self):
        self.poster("formes/ajouter/", {"famille": "rectangle", "cotes": {"largeur": 200, "hauteur": 100}})
        piece = PieceDecoupe.objects.get()
        article = piece.article_id
        ancien = piece.fichier_source.name
        reponse = self.poster("formes/ajouter/", {"piece_id": piece.pk, "famille": "rectangle", "cotes": {"largeur": 300, "hauteur": 120, "rayon_angle": 10}})
        self.assertEqual(reponse.status_code, 200)
        piece.refresh_from_db()
        self.assertEqual((piece.nom, piece.article_id, PieceDecoupe.objects.count()), ("Rectangle 300×120", article, 1))
        self.assertEqual((round(piece.largeur_mm), round(piece.hauteur_mm)), (300, 120))
        self.assertEqual(piece.parametres_forme["cotes"]["largeur"], 300)
        self.assertNotEqual(piece.fichier_source.name, ancien)
        self.assertEqual(Article.objects.get(pk=article).libelle, "Rectangle 300×120")

    def test_nom_personnalise_conserve_a_la_modification(self):
        self.poster("formes/ajouter/", {"famille": "disque", "cotes": {"diametre": 100}})
        piece = PieceDecoupe.objects.get()
        piece.nom = "Rondelle client X"
        piece.save()
        self.poster("formes/ajouter/", {"piece_id": piece.pk, "famille": "disque", "cotes": {"diametre": 150}})
        piece.refresh_from_db()
        self.assertEqual((piece.nom, round(piece.largeur_mm)), ("Rondelle client X", 150))

    def test_piece_importee_sans_cotes_a_modifier(self):
        self.client.post(self.url + "importer/", {"fichier": SimpleUploadedFile("p.dxf", (DONNEES / "piece_calibrage.dxf").read_bytes())})
        piece = PieceDecoupe.objects.get()
        self.assertEqual(self.poster("formes/ajouter/", {"piece_id": piece.pk, "famille": "disque", "cotes": {"diametre": 50}}).status_code, 400)

    def test_devis_verrouille_et_panneau(self):
        page = self.client.get(f"/admin/chiffrage/devis/{self.devis.pk}/change/")
        self.assertContains(page, "Bibliothèque de formes")
        self.assertContains(page, 'id="dp-catalogue-formes"')
        self.assertContains(page, "bride_en1092")
        self.devis.statut = Devis.Statut.VALIDE
        self.devis.save()
        self.assertEqual(self.poster("formes/ajouter/", {"famille": "disque", "cotes": {"diametre": 50}}).status_code, 409)
        self.assertNotContains(self.client.get(f"/admin/chiffrage/devis/{self.devis.pk}/change/"), "Bibliothèque de formes")

    def test_admin_cotes_normalisees_et_action_verifier(self):
        from decoupe.models import NormeCote

        self.assertContains(self.client.get("/admin/decoupe/normecote/"), "DN50 PN16")
        ligne = NormeCote.objects.get(designation="DN50 PN16")
        self.client.post("/admin/decoupe/normecote/", {"action": "marquer_verifie", "_selected_action": [ligne.pk]})
        ligne.refresh_from_db()
        self.assertTrue(ligne.verifie)


class SensEtCoinImbricationTests(TestCase):
    """Tôles posées à plat (longueur à l'horizontale), sens de remplissage, coin de départ, chute de bout et surface consommée."""

    setUp = ImbricationDevisTests.setUp
    piece = ImbricationDevisTests.piece
    imbriquer = ImbricationDevisTests.imbriquer

    def resultat(self, sens="longueur", coin="bas_gauche", forme=True, quantite=7):
        from decoupe.models import FormatTole
        from decoupe.services import imbrication_devis as imb
        from decoupe.services.devis_pieces import pieces_du_devis

        self.piece("a", quantite)
        groupe = imb.grouper(pieces_du_devis(self.devis))[0][0]
        format_tole = FormatTole.objects.get(largeur_mm=1500, longueur_mm=3000)
        return groupe, imb.imbriquer_groupe(groupe, format_tole, 5, 0, self.tole, forme=forme, sens=sens, coin=coin)

    def polygones(self, groupe, resultat, placements):
        from shapely.affinity import rotate, scale, translate
        from shapely.geometry import Polygon

        p = groupe.pieces[0]
        base = Polygon(p.contour_json["exterieur"], p.contour_json["trous"])
        sortie = []
        for pl in placements:
            g = scale(base, xfact=-1, origin=(0, 0)) if pl.miroir else base
            g = rotate(g, pl.rotation_deg, origin=(0, 0)) if pl.rotation_deg else g
            minx, miny, _, _ = g.bounds
            sortie.append((pl.numero_feuille, translate(g, pl.x_mm - minx, pl.y_mm - miny)))
        return sortie

    def controle(self, groupe, resultat, placements):
        from shapely.geometry import box

        feuille = box(0, 0, resultat.largeur_x_mm, resultat.hauteur_y_mm)
        polys = self.polygones(groupe, resultat, placements)
        for _, g in polys:
            self.assertTrue(feuille.buffer(1e-6).contains(g), "pièce hors de la tôle")
        for i, (n1, g1) in enumerate(polys):
            for n2, g2 in polys[:i]:
                if n1 == n2:
                    self.assertLess(g1.intersection(g2).area, 1.0, "pièces qui se chevauchent")

    def test_les_quatre_coins_et_les_deux_sens_restent_valides(self):
        from decoupe.services import imbrication_devis as imb

        for forme in (False, True):
            for sens in ("longueur", "largeur"):
                for coin in ("bas_gauche", "haut_gauche", "bas_droite", "haut_droite"):
                    with self.subTest(forme=forme, sens=sens, coin=coin):
                        PieceDecoupe.objects.all().delete()
                        groupe, r = self.resultat(sens, coin, forme)
                        self.assertEqual((r.largeur_x_mm, r.hauteur_y_mm), (3000, 1500))
                        self.controle(groupe, r, r.placements_affichage)
                        self.assertEqual(r.coin_depart, coin)
                        if r.chute_bout:
                            from shapely.geometry import box

                            x, y, w, h = r.chute_bout
                            zone = box(x, y, x + w, y + h)
                            for numero, g in self.polygones(groupe, r, r.placements_affichage):
                                if numero == r.nb_feuilles:
                                    self.assertLess(g.intersection(zone).area, 1.0, "une pièce de la dernière feuille est sur la chute de bout")

    def test_la_chute_de_bout_est_a_l_extremite_du_sens_de_remplissage(self):
        _, r = self.resultat("longueur", "bas_gauche")
        x, y, w, h = r.chute_bout
        self.assertEqual((y, h, round(x + w)), (0.0, 1500, 3000))  # bande pleine hauteur, à droite
        PieceDecoupe.objects.all().delete()
        _, r2 = self.resultat("largeur", "bas_gauche")
        x, y, w, h = r2.chute_bout
        self.assertEqual((x, w, round(y + h)), (0.0, 3000, 1500))  # bande pleine longueur, en haut
        PieceDecoupe.objects.all().delete()
        _, r3 = self.resultat("longueur", "haut_droite")
        self.assertEqual(round(r3.chute_bout[0]), 0)  # départ à droite : la chute est à gauche

    def test_surface_consommee_est_tole_complete_moins_chute_de_bout(self):
        _, r = self.resultat()
        self.assertAlmostEqual(r.surface_consommee_mm2 + r.chute_bout_mm2, r.nb_feuilles * 3000 * 1500, places=3)
        self.assertAlmostEqual(r.chute_bout_mm2, r.chute_bout[2] * r.chute_bout[3] + (0 if r.nb_feuilles == 1 else 0), delta=1)
        self.assertLess(r.surface_consommee_mm2, r.nb_feuilles * 3000 * 1500)

    def test_le_coin_ne_change_pas_le_cout_le_sens_peut_le_changer(self):
        _, a = self.resultat("longueur", "bas_gauche")
        PieceDecoupe.objects.all().delete()
        _, b = self.resultat("longueur", "haut_droite")
        self.assertEqual((a.cout_total, a.surface_consommee_mm2), (b.cout_total, b.surface_consommee_mm2))

    def test_coin_avec_retournement_refuse_si_piece_non_symetrique(self):
        self.piece("a", 3)
        PieceDecoupe.objects.update(symetrie_autorisee=False)
        from decoupe.models import FormatTole
        from decoupe.services import imbrication_devis as imb
        from decoupe.services.devis_pieces import pieces_du_devis

        groupe = imb.grouper(pieces_du_devis(self.devis))[0][0]
        r = imb.imbriquer_groupe(groupe, FormatTole.objects.get(largeur_mm=1500, longueur_mm=3000), 5, 0, self.tole, coin="haut_gauche")
        self.assertEqual(r.coin_depart, "bas_gauche")
        self.assertIn("ne peut pas être retournée", r.avertissement_coin)
        r2 = imb.imbriquer_groupe(groupe, FormatTole.objects.get(largeur_mm=1500, longueur_mm=3000), 5, 0, self.tole, coin="haut_droite")
        self.assertEqual((r2.coin_depart, r2.avertissement_coin), ("haut_droite", ""))

    def test_panneau_affiche_bilan_sens_depart_et_les_retient(self):
        from decoupe.models import FormatTole

        a = self.piece("a", 7)
        html = self.imbriquer().json()["html"]
        for texte in ("Tôles complètes", "− Chute de bout", "= Surface consommée", "data-i=\"sens\"", "data-i=\"coin\"", "En bas à gauche", "dp-tole"):
            self.assertIn(texte, html)
        html = self.imbriquer({"S235|10|laser": {"sens": "largeur", "coin": "haut_droite"}}).json()["html"]
        self.assertRegex(html, r'<option value="largeur"\s+selected')
        self.assertRegex(html, r'<option value="haut_droite"\s+selected')
        format_tole = FormatTole.objects.get(largeur_mm=1500, longueur_mm=3000)
        self.client.post(
            self.url + "imbrication/retenir/",
            data=json.dumps({"cle": "S235|10|laser", "tole": self.tole.pk, "format": format_tole.pk, "marge": "5", "chute": "0", "forme": "1", "sens": "largeur", "coin": "haut_droite"}),
            content_type="application/json",
        )
        a.refresh_from_db()
        self.assertEqual((a.sens_imbrication, a.coin_depart), ("largeur", "haut_droite"))
        self.assertRegex(self.imbriquer().json()["html"], r'<option value="largeur"\s+selected')  # valeur retenue relue


class ProfilesDevisTests(TestCase):
    """Débits de profilés (cornière, UPN, tubes) : bibliothèque, article fabriqué, imbrication des barres, chiffrage."""

    setUp = PanneauPiecesDevisTests.setUp

    def poster(self, chemin, donnees):
        return self.client.post(self.url.replace("/pieces/", "/") + chemin, data=json.dumps(donnees), content_type="application/json")

    def section(self, designation="UPN 100", cout=1.10):
        from decoupe.models import ProfileSection

        section = ProfileSection.objects.get(designation=designation)
        if cout is not None:
            article = Article.objects.create(
                reference="ACH-" + designation, nature=Article.Nature.MATIERE_PREMIERE, unite_cout=Article.UniteCout.LONGUEUR,
                poids_lineique=section.masse_lineique, cout_unitaire=cout,
            )
            section.article = article
            section.save()
        return section

    def ajouter(self, section, longueur=1450, quantite=4, a=90, b=90):
        r = self.poster("profils/ajouter/", {"section": section.pk, "longueur": longueur, "coupe_a": a, "coupe_b": b, "quantite": quantite})
        self.assertEqual(r.status_code, 200, r.content)
        from decoupe.models import PieceProfile

        return PieceProfile.objects.get(pk=r.json()["piece_id"])

    def test_catalogue_seme_non_verifie(self):
        from decoupe.models import ProfileSection

        familles = set(ProfileSection.objects.values_list("famille", flat=True))
        self.assertEqual(familles, {"corniere", "upn", "tube_carre", "tube_rectangulaire", "tube_rond"})
        self.assertFalse(ProfileSection.objects.filter(verifie=True).exists())
        self.assertAlmostEqual(ProfileSection.objects.get(designation="Tube Ø60.3×3.6").masse_lineique, 5.03, places=2)
        self.assertEqual(ProfileSection.objects.get(designation="UPN 100").masse_lineique, 10.6)

    def test_creation_du_debit_avec_son_article_et_carte(self):
        section = self.section()
        r = self.poster("profils/ajouter/", {"section": section.pk, "longueur": "1450", "coupe_a": "90", "coupe_b": "45", "quantite": 6})
        self.assertEqual(r.status_code, 200)
        from decoupe.models import PieceProfile

        piece = PieceProfile.objects.get()
        self.assertEqual((piece.nom, piece.quantite, piece.coupe_b_deg, piece.devis), ("UPN 100 L=1450", 6, 45.0, self.devis))
        self.assertEqual(piece.article.nature, Article.Nature.FABRIQUE)
        self.assertAlmostEqual(piece.masse_kg, 1.45 * 10.6, places=3)
        self.assertIn("Profilé", r.json()["html"])

    def test_cotes_invalides_refusees(self):
        section = self.section()
        for donnees in ({"longueur": "abc"}, {"longueur": "-5"}, {"longueur": "30", "coupe_a": 30, "coupe_b": 30}, {"longueur": "1000", "coupe_a": 10}, {"section": 0}):
            r = self.poster("profils/ajouter/", {"section": section.pk, **donnees})
            self.assertEqual(r.status_code, 400, donnees)
        from decoupe.models import PieceProfile

        self.assertEqual((PieceProfile.objects.count(), Article.objects.filter(nature="fabrique").count()), (0, 0))

    def test_apercu(self):
        section = self.section()
        r = self.poster("profils/apercu/", {"section": section.pk, "longueur": "1450", "coupe_a": "45", "coupe_b": "90"})
        corps = r.json()
        self.assertEqual((r.status_code, corps["nom"], corps["masse_lineique"]), (200, "UPN 100 L=1450", 10.6))
        self.assertIn("<polygon", corps["svg"])
        self.assertEqual(corps["prix"], "11.66 €/m")  # 1,10 €/kg × 10,6 kg/m

    def test_imbrication_des_barres_chute_de_bout_et_prix(self):
        from decoupe.services import profiles

        section = self.section()
        a = self.ajouter(section, 1450, 3)
        b = self.ajouter(section, 2000, 2)
        r = profiles.imbriquer_barres([a, b], section, None, 3, 0, 0)
        # 7 débits de 8350 mm au total (+ traits de scie) dans des barres de 6 m
        self.assertEqual(r.nb_barres, 2)
        self.assertAlmostEqual(r.pieces_mm, 1450 * 3 + 2000 * 2)
        self.assertAlmostEqual(r.consommee_mm + r.chute_bout_mm, 2 * 6000)
        self.assertAlmostEqual(r.facturee_mm, r.consommee_mm)  # chute récupérable 0 % : toute la consommée est facturée
        self.assertEqual(r.cout_total, round(Decimal(r.consommee_mm) / 1000 * Decimal("1.10") * Decimal("10.6"), 2))
        self.assertEqual(sum(x["total"] for x in r.par_piece), r.cout_total)
        for barre in r.barres:  # aucun débit ne dépasse la barre
            self.assertLessEqual(max(x + l for _, x, l in barre.pieces), 6000 + 1e-6)
        recup = profiles.imbriquer_barres([a, b], section, None, 3, 0, 100)
        self.assertAlmostEqual(recup.facturee_mm, recup.pieces_mm)  # tout le reste est récupéré

    def test_debit_plus_long_que_la_barre(self):
        from decoupe.services import profiles

        section = self.section()
        piece = self.ajouter(section, 5990, 1)
        with self.assertRaises(profiles.ErreurProfile):
            profiles.imbriquer_barres([piece], section, None, 3, 20, 0)

    def test_panneau_imbrication_bilan_et_retenue(self):
        section = self.section()
        piece = self.ajouter(section, 1450, 7)
        html = self.client.post(self.url + "imbrication/", data=json.dumps({"choix": {}}), content_type="application/json").json()["html"]
        for texte in ("Imbrication des profilés — UPN 100", "= Longueur consommée", "− Chute de bout", "Barres complètes", "dp-barre-svg", "11,66 €/m"):
            self.assertIn(texte, html)
        self.poster("profils/retenir/", {"section": section.pk, "trait": "4", "marge": "10", "chute": "50"})
        piece.refresh_from_db()
        self.assertEqual((piece.trait_scie_mm, piece.marge_bout_mm, float(piece.taux_chute_recuperable)), (4, 10, 50.0))

    def test_ajout_aux_lignes_du_devis_avec_le_prix_de_la_matiere(self):
        from decimal import Decimal as Dec

        section = self.section()
        piece = self.ajouter(section, 1450, 7)
        self.client.post(f"/admin/chiffrage/devis/{self.devis.pk}/pieces/ajouter-au-devis/")
        ligne = self.devis.lignes.get(article=piece.article)
        self.assertEqual(ligne.quantite, 7)
        self.assertGreater(ligne.cout_matiere_calcule, 0)
        from decoupe.services import profiles

        attendu = profiles.cout_matiere_piece_profile(piece, 7)
        self.assertEqual(ligne.cout_matiere_calcule, attendu)
        self.assertIsInstance(attendu, Dec)

    def test_sans_article_d_achat_le_prix_est_signale(self):
        section = self.section(cout=None)
        self.ajouter(section, 1000, 2)
        html = self.client.post(self.url + "imbrication/", data=json.dumps({"choix": {}}), content_type="application/json").json()["html"]
        self.assertIn("Aucun article d&#x27;achat", html)

    def test_modification_et_suppression(self):
        section = self.section()
        piece = self.ajouter(section, 1450, 2)
        r = self.client.post(f"{self.url.replace('/pieces/', '/')}profils/{piece.pk}/enregistrer/", {"longueur": "2000", "quantite": "5", "coupe_a": "45"})
        self.assertEqual(r.status_code, 200)
        piece.refresh_from_db()
        self.assertEqual((piece.longueur_mm, piece.quantite, piece.coupe_a_deg, piece.nom), (2000, 5, 45, "UPN 100 L=2000"))
        article = piece.article_id
        self.client.post(f"{self.url.replace('/pieces/', '/')}profils/{piece.pk}/supprimer/")
        self.assertFalse(Article.objects.filter(pk=article).exists())

    def test_panneau_propose_les_profiles(self):
        page = self.client.get(f"/admin/chiffrage/devis/{self.devis.pk}/change/")
        self.assertContains(page, "tube_rond")
        self.assertContains(page, 'id="dp-profils"')
        self.assertContains(self.client.get("/admin/decoupe/profilesection/"), "UPN 100")


class VuesImbricationTests(TestCase):
    setUp = PanneauPiecesDevisTests.setUp

    def test_panneau_propose_1_a_4_imbrications_par_ligne(self):
        page = self.client.get(f"/admin/chiffrage/devis/{self.devis.pk}/change/")
        self.assertContains(page, 'id="dp-vues"')
        for n in "1234":
            self.assertContains(page, f'data-colonnes="{n}" aria-pressed')


class MiniaturePdfEtTolesTests(TestCase):
    setUp = ImbricationDevisTests.setUp
    piece = ImbricationDevisTests.piece
    imbriquer = ImbricationDevisTests.imbriquer

    def test_message_sans_tole_donne_les_epaisseurs_existantes(self):
        self.piece("a", 2, epaisseur="5")
        html = self.imbriquer().json()["html"]
        self.assertIn("Aucune tôle S235 de 5 mm en base", html)
        self.assertIn("Épaisseurs déjà en base pour cette matière : 10 mm", html)

    def test_pdf_du_devis_avec_miniatures(self):
        from chiffrage.documents import _miniatures_devis, generer_pdf_devis

        piece = self.piece("a", 2)
        self.client.post(f"{self.url}{piece.pk}/enregistrer/", {"matiere": "S235", "epaisseur": "10"})
        ligne = DevisLigne.objects.create(devis=self.devis, article=piece.article, quantite=2, prix_vente_matiere=Decimal("20"))
        self.assertEqual(list(_miniatures_devis(self.devis)), [piece.article_id])
        pdf = generer_pdf_devis(self.devis)
        self.assertTrue(bytes(pdf).startswith(b"%PDF"))
