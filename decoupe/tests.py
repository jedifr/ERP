import math
import tempfile
from pathlib import Path

import ezdxf
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from technique.models import Article, Matiere

from .models import ImbricationJob, ImbricationLigne, ImbricationPlacement, PieceDecoupe
from .services.geometrie import ErreurImportGeometrie, extraire_geometrie
from .services.imbrication import ItemANester, calculer_imbrication


def _dxf_bytes(build):
    """Construit un DXF minimal via `build(modelspace)` et renvoie son contenu en bytes."""
    doc = ezdxf.new()
    build(doc.modelspace())
    with tempfile.TemporaryDirectory() as tmp:
        chemin = Path(tmp) / "piece.dxf"
        doc.saveas(chemin)
        return chemin.read_bytes()


def _rectangle_avec_trou(msp):
    msp.add_lwpolyline([(0, 0), (100, 0), (100, 50), (0, 50)], close=True)
    msp.add_circle((50, 25), radius=10)


def _l_bracket(msp):
    msp.add_line((0, 0), (60, 0))
    msp.add_line((60, 0), (60, 20))
    msp.add_arc((50, 20), radius=10, start_angle=0, end_angle=90)
    msp.add_line((50, 30), (0, 30))
    msp.add_line((0, 30), (0, 0))


class ExtraireGeometrieTests(TestCase):
    def _fichier(self, build, suffix="dxf"):
        contenu = _dxf_bytes(build)
        tmp = tempfile.NamedTemporaryFile(suffix=f".{suffix}", delete=False)
        tmp.write(contenu)
        tmp.close()
        return tmp.name

    def test_rectangle_avec_trou(self):
        chemin = self._fichier(_rectangle_avec_trou)
        resultat = extraire_geometrie(chemin, "dxf")
        self.assertAlmostEqual(resultat.largeur_mm, 100, places=2)
        self.assertAlmostEqual(resultat.hauteur_mm, 50, places=2)
        self.assertEqual(len(resultat.holes), 1)
        # Surface = rectangle - disque, à la tolérance de discrétisation du cercle près.
        attendu = 100 * 50 - math.pi * 10**2
        self.assertAlmostEqual(resultat.surface_mm2, attendu, delta=5)
        perimetre_attendu = 2 * (100 + 50) + 2 * math.pi * 10
        self.assertAlmostEqual(resultat.perimetre_mm, perimetre_attendu, delta=2)
        self.assertEqual(resultat.avertissements, [])

    def test_chaine_ligne_et_arc(self):
        chemin = self._fichier(_l_bracket)
        resultat = extraire_geometrie(chemin, "dxf")
        self.assertGreater(resultat.surface_mm2, 0)
        self.assertAlmostEqual(resultat.largeur_mm, 60, places=1)
        self.assertAlmostEqual(resultat.hauteur_mm, 30, places=1)

    def test_origine_ramenee_au_coin_du_rectangle_englobant(self):
        def decale(msp):
            msp.add_lwpolyline([(500, 500), (600, 500), (600, 550), (500, 550)], close=True)

        chemin = self._fichier(decale)
        resultat = extraire_geometrie(chemin, "dxf")
        xs = [p[0] for p in resultat.exterior]
        ys = [p[1] for p in resultat.exterior]
        self.assertAlmostEqual(min(xs), 0, places=2)
        self.assertAlmostEqual(min(ys), 0, places=2)

    def test_ile_flottante_dans_un_trou_signalee_et_ecartee(self):
        # Trois cercles concentriques (anneau + îlot central non relié par des pattes) forment
        # géométriquement deux pièces disjointes : l'îlot central tomberait du trou une fois
        # découpé. `extraire_geometrie` retient la plus grande silhouette connexe (l'anneau) et
        # signale l'îlot ignoré, plutôt que de fusionner deux pièces physiquement séparées.
        def concentriques(msp):
            msp.add_circle((0, 0), radius=30)
            msp.add_circle((0, 0), radius=20)
            msp.add_circle((0, 0), radius=10)

        chemin = self._fichier(concentriques)
        resultat = extraire_geometrie(chemin, "dxf")
        attendu_anneau = math.pi * (30**2 - 20**2)
        self.assertAlmostEqual(resultat.surface_mm2, attendu_anneau, delta=10)
        self.assertTrue(resultat.avertissements)

    def test_assembler_silhouette_gere_les_iles_par_regle_pair_impair(self):
        # Vérifie l'algorithme d'imbrication pair/impair lui-même (anneau + trou + îlot central),
        # indépendamment de la règle "une seule silhouette connexe" appliquée en sortie publique.
        def concentriques(msp):
            msp.add_circle((0, 0), radius=30)
            msp.add_circle((0, 0), radius=20)
            msp.add_circle((0, 0), radius=10)

        chemin = self._fichier(concentriques)
        from .services import geometrie as service

        document = service._charger_document(chemin, "dxf")
        avertissements = []
        lignes = []
        for entite in document.modelspace():
            lignes.extend(service._vers_lignes(entite, avertissements))
        anneaux = service._contours_fermes(lignes, avertissements)
        silhouette = service._assembler_silhouette(anneaux)
        attendu = math.pi * (30**2 - 20**2 + 10**2)
        self.assertAlmostEqual(silhouette.area, attendu, delta=10)

    def test_contour_non_ferme_leve_une_erreur(self):
        def ouvert(msp):
            msp.add_line((0, 0), (10, 0))
            msp.add_line((10, 0), (10, 10))

        chemin = self._fichier(ouvert)
        with self.assertRaises(ErreurImportGeometrie):
            extraire_geometrie(chemin, "dxf")

    def test_fichier_sans_entite_geometrique(self):
        def vide(msp):
            msp.add_text("bonjour", dxfattribs={"insert": (0, 0)})

        chemin = self._fichier(vide)
        with self.assertRaises(ErreurImportGeometrie):
            extraire_geometrie(chemin, "dxf")

    def test_dwg_sans_convertisseur_installe(self):
        chemin = self._fichier(_rectangle_avec_trou)
        with self.assertRaises(ErreurImportGeometrie):
            extraire_geometrie(chemin, "dwg")


class CalculerImbricationTests(TestCase):
    def test_grille_deux_par_deux_tient_sur_une_feuille(self):
        items = [
            ItemANester(
                piece_id=1, largeur_mm=400, hauteur_mm=400, surface_mm2=160_000, rotation_autorisee=False, quantite=4
            )
        ]
        resultat = calculer_imbrication(
            items, largeur_feuille_mm=1000, longueur_feuille_mm=1000, marge_bord_mm=0, espacement_pieces_mm=0
        )
        self.assertEqual(resultat.nb_feuilles, 1)
        self.assertEqual(len(resultat.placements), 4)
        self.assertEqual(resultat.pieces_non_placees, [])

    def test_cinquieme_piece_ouvre_une_deuxieme_feuille(self):
        items = [
            ItemANester(
                piece_id=1, largeur_mm=400, hauteur_mm=400, surface_mm2=160_000, rotation_autorisee=False, quantite=5
            )
        ]
        resultat = calculer_imbrication(
            items, largeur_feuille_mm=1000, longueur_feuille_mm=1000, marge_bord_mm=0, espacement_pieces_mm=0
        )
        self.assertEqual(resultat.nb_feuilles, 2)
        par_feuille = {}
        for placement in resultat.placements:
            par_feuille.setdefault(placement.numero_feuille, 0)
            par_feuille[placement.numero_feuille] += 1
        self.assertEqual(sorted(par_feuille.values()), [1, 4])

    def test_aucun_chevauchement_entre_pieces_placees(self):
        from shapely.geometry import box

        items = [
            ItemANester(
                piece_id=1, largeur_mm=130, hauteur_mm=70, surface_mm2=9100, rotation_autorisee=True, quantite=12
            )
        ]
        resultat = calculer_imbrication(
            items, largeur_feuille_mm=500, longueur_feuille_mm=500, marge_bord_mm=5, espacement_pieces_mm=3
        )
        par_feuille = {}
        for p in resultat.placements:
            par_feuille.setdefault(p.numero_feuille, []).append(
                box(p.x_mm, p.y_mm, p.x_mm + p.largeur_placee_mm, p.y_mm + p.hauteur_placee_mm)
            )
        for rectangles in par_feuille.values():
            for i, a in enumerate(rectangles):
                for b in rectangles[i + 1 :]:
                    self.assertAlmostEqual(a.intersection(b).area, 0, places=6)

    def test_piece_plus_grande_que_la_feuille_est_ecartee(self):
        items = [
            ItemANester(
                piece_id=99, largeur_mm=2000, hauteur_mm=2000, surface_mm2=4_000_000, rotation_autorisee=True, quantite=1
            )
        ]
        resultat = calculer_imbrication(
            items, largeur_feuille_mm=1000, longueur_feuille_mm=1000, marge_bord_mm=0, espacement_pieces_mm=0
        )
        self.assertEqual(resultat.nb_feuilles, 0)
        self.assertEqual(resultat.pieces_non_placees, [99])

    def test_rotation_permet_de_faire_tenir_la_piece(self):
        items = [
            ItemANester(
                piece_id=1, largeur_mm=900, hauteur_mm=400, surface_mm2=360_000, rotation_autorisee=True, quantite=1
            )
        ]
        resultat = calculer_imbrication(
            items, largeur_feuille_mm=500, longueur_feuille_mm=1000, marge_bord_mm=0, espacement_pieces_mm=0
        )
        self.assertEqual(resultat.nb_feuilles, 1)
        self.assertEqual(resultat.placements[0].rotation_deg, 90)

    def test_taux_utilisation_base_sur_surface_reelle(self):
        items = [
            ItemANester(
                piece_id=1, largeur_mm=100, hauteur_mm=100, surface_mm2=7854, rotation_autorisee=False, quantite=1
            )
        ]
        resultat = calculer_imbrication(
            items, largeur_feuille_mm=100, longueur_feuille_mm=100, marge_bord_mm=0, espacement_pieces_mm=0
        )
        self.assertAlmostEqual(resultat.taux_utilisation_pct, 78.54, places=1)


class PieceDecoupeModelTests(TestCase):
    def test_importer_geometrie_peuple_les_champs(self):
        contenu = _dxf_bytes(_rectangle_avec_trou)
        piece = PieceDecoupe.objects.create(
            nom="Flasque", fichier_source=SimpleUploadedFile("flasque.dxf", contenu)
        )
        self.assertEqual(piece.format_source, "dxf")
        ok = piece.importer_geometrie()
        self.assertTrue(ok)
        piece.refresh_from_db()
        self.assertEqual(piece.statut, PieceDecoupe.Statut.OK)
        self.assertEqual(piece.nb_contours_interieurs, 1)
        self.assertIsNotNone(piece.surface_mm2)

    def test_importer_geometrie_echec_marque_en_erreur(self):
        contenu = _dxf_bytes(lambda msp: msp.add_text("x", dxfattribs={"insert": (0, 0)}))
        piece = PieceDecoupe.objects.create(
            nom="Invalide", fichier_source=SimpleUploadedFile("invalide.dxf", contenu)
        )
        ok = piece.importer_geometrie()
        self.assertFalse(ok)
        piece.refresh_from_db()
        self.assertEqual(piece.statut, PieceDecoupe.Statut.ERREUR)
        self.assertTrue(piece.message_erreur)

    def test_extension_incoherente_avec_format_declare(self):
        contenu = _dxf_bytes(_rectangle_avec_trou)
        piece = PieceDecoupe(
            nom="Bad",
            fichier_source=SimpleUploadedFile("piece.dxf", contenu),
            format_source=PieceDecoupe.FormatSource.DWG,
        )
        from django.core.exceptions import ValidationError

        with self.assertRaises(ValidationError):
            piece.full_clean()


class ImbricationJobModelTests(TestCase):
    def setUp(self):
        self.acier = Matiere.objects.create(nom="Acier", densite=7.85)
        self.tole = Article.objects.create(
            reference="TOLE-S235-3MM",
            nature=Article.Nature.MATIERE_PREMIERE,
            matiere=self.acier,
            unite_cout=Article.UniteCout.SURFACE,
            epaisseur=3,
            cout_unitaire=25,
        )
        contenu = _dxf_bytes(_rectangle_avec_trou)
        self.piece = PieceDecoupe.objects.create(
            nom="Flasque", fichier_source=SimpleUploadedFile("flasque.dxf", contenu)
        )
        self.piece.importer_geometrie()

    def test_calculer_persiste_placements_et_cout(self):
        job = ImbricationJob.objects.create(
            article_matiere=self.tole,
            largeur_feuille_mm=1000,
            longueur_feuille_mm=2000,
            marge_bord_mm=5,
            espacement_pieces_mm=5,
        )
        ImbricationLigne.objects.create(job=job, piece=self.piece, quantite=10)
        job.calculer()

        job.refresh_from_db()
        self.assertEqual(job.nb_feuilles, 1)
        self.assertEqual(ImbricationPlacement.objects.filter(job=job).count(), 10)
        surface_feuille_m2 = (1000 * 2000) / 1_000_000
        self.assertAlmostEqual(job.cout_matiere_estime, surface_feuille_m2 * 25, places=2)

    def test_article_matiere_doit_etre_matiere_premiere(self):
        fabrique = Article.objects.create(reference="PIECE-100", nature=Article.Nature.FABRIQUE)
        job = ImbricationJob(
            article_matiere=fabrique, largeur_feuille_mm=1000, longueur_feuille_mm=2000
        )
        from django.core.exceptions import ValidationError

        with self.assertRaises(ValidationError):
            job.full_clean()

    def test_ligne_refuse_une_piece_non_importee(self):
        piece_en_echec = PieceDecoupe.objects.create(
            nom="Non importée",
            fichier_source=SimpleUploadedFile("x.dxf", _dxf_bytes(_rectangle_avec_trou)),
        )
        job = ImbricationJob.objects.create(largeur_feuille_mm=1000, longueur_feuille_mm=2000)
        ligne = ImbricationLigne(job=job, piece=piece_en_echec, quantite=1)
        from django.core.exceptions import ValidationError

        with self.assertRaises(ValidationError):
            ligne.full_clean()


class ApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        utilisateur = get_user_model().objects.create_user(username="op", password="x", email="jedifr@gmail.com")
        self.client.force_authenticate(utilisateur)

    def test_upload_piece_puis_creation_imbrication(self):
        contenu = _dxf_bytes(_rectangle_avec_trou)
        reponse = self.client.post(
            "/api/v1/pieces-decoupe/",
            {"nom": "Flasque", "fichier_source": SimpleUploadedFile("flasque.dxf", contenu)},
            format="multipart",
        )
        self.assertEqual(reponse.status_code, 201, reponse.data)
        self.assertEqual(reponse.data["statut"], "ok")
        piece_id = reponse.data["id"]
        self.assertGreater(reponse.data["surface_mm2"], 0)

        reponse_job = self.client.post(
            "/api/v1/imbrications/",
            {
                "largeur_feuille_mm": 1000,
                "longueur_feuille_mm": 2000,
                "marge_bord_mm": 5,
                "espacement_pieces_mm": 5,
                "lignes": [{"piece": piece_id, "quantite": 6}],
            },
            format="json",
        )
        self.assertEqual(reponse_job.status_code, 201, reponse_job.data)
        self.assertEqual(reponse_job.data["nb_feuilles"], 1)
        self.assertEqual(len(reponse_job.data["placements"]), 6)

        job_id = reponse_job.data["id"]
        apercu = self.client.get(f"/api/v1/imbrications/{job_id}/apercu/1/")
        self.assertEqual(apercu.status_code, 200)
        self.assertEqual(apercu["Content-Type"], "image/svg+xml")
        self.assertIn(b"<svg", apercu.content)

    def test_upload_fichier_invalide_est_signale_en_erreur_pas_rejete(self):
        contenu = _dxf_bytes(lambda msp: msp.add_text("x", dxfattribs={"insert": (0, 0)}))
        reponse = self.client.post(
            "/api/v1/pieces-decoupe/",
            {"nom": "Invalide", "fichier_source": SimpleUploadedFile("invalide.dxf", contenu)},
            format="multipart",
        )
        self.assertEqual(reponse.status_code, 201, reponse.data)
        self.assertEqual(reponse.data["statut"], "erreur")

    def test_imbrication_sans_ligne_est_rejetee(self):
        reponse = self.client.post(
            "/api/v1/imbrications/",
            {"largeur_feuille_mm": 1000, "longueur_feuille_mm": 2000, "lignes": []},
            format="json",
        )
        self.assertEqual(reponse.status_code, 400)
