import math
import tempfile
from pathlib import Path

import ezdxf
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from technique.models import Article, Matiere

from .models import (
    ImbricationJob,
    ImbricationLigne,
    ImbricationPlacement,
    PieceDecoupe,
    ProfilImportDecoupe,
    RegleProfilImportDecoupe,
)
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

    def _piece_calques_multiples(self, msp):
        msp.add_lwpolyline([(0, 0), (100, 0), (100, 50), (0, 50)], close=True, dxfattribs={"layer": "COUPE"})
        # Logo gravé À L'INTÉRIEUR de la silhouette : sans profil, ce contour fermé sur un
        # calque séparé serait détecté à tort comme un trou à découper.
        msp.add_lwpolyline([(40, 15), (60, 15), (50, 35)], close=True, dxfattribs={"layer": "GRAVURE"})
        msp.add_line((0, 25), (100, 25), dxfattribs={"layer": "PLIAGE"})

    def test_sans_profil_la_gravure_est_prise_pour_un_trou(self):
        # Comportement historique (sans profil, regles_calques=None) : tout est traité comme
        # découpe, donc la gravure ressort comme un trou — exactement le défaut que corrige la
        # fonctionnalité de profils d'import testée ci-dessous.
        chemin = self._fichier(self._piece_calques_multiples)
        resultat = extraire_geometrie(chemin, "dxf")
        self.assertEqual(len(resultat.holes), 1)

    def test_regles_calques_separent_decoupe_gravure_pliage(self):
        chemin = self._fichier(self._piece_calques_multiples)
        regles = {"coupe": "decoupe", "gravure": "gravure", "pliage": "pliage"}
        resultat = extraire_geometrie(chemin, "dxf", regles_calques=regles)

        self.assertEqual(len(resultat.holes), 0)
        self.assertAlmostEqual(resultat.surface_mm2, 5000, delta=1)
        self.assertGreater(len(resultat.gravure), 0)
        longueur_triangle_attendue = 20 + 2 * math.sqrt(10**2 + 20**2)
        self.assertAlmostEqual(resultat.longueur_gravure_mm, longueur_triangle_attendue, delta=1)
        self.assertEqual(len(resultat.pliage), 1)
        self.assertAlmostEqual(resultat.pliage[0][0][0], 0, delta=0.5)
        self.assertEqual(resultat.calques, ["COUPE", "GRAVURE", "PLIAGE"])
        self.assertEqual(resultat.avertissements, [])

    def test_calque_non_couvert_par_profil_avertit_et_reste_decoupe(self):
        def piece(msp):
            msp.add_lwpolyline([(0, 0), (100, 0), (100, 50), (0, 50)], close=True, dxfattribs={"layer": "AUTRE"})

        chemin = self._fichier(piece)
        resultat = extraire_geometrie(chemin, "dxf", regles_calques={"coupe": "decoupe"})
        self.assertAlmostEqual(resultat.surface_mm2, 5000, delta=1)
        self.assertTrue(any("AUTRE" in avertissement for avertissement in resultat.avertissements))

    def test_calque_ignore_est_exclu(self):
        def piece(msp):
            msp.add_lwpolyline([(0, 0), (100, 0), (100, 50), (0, 50)], close=True, dxfattribs={"layer": "COUPE"})
            # Un trait isolé et non fermé : s'il n'était pas ignoré, il déclencherait
            # l'avertissement "contour non fermé" — sa présence prouve que le calque "ignore"
            # a bien été exclu avant la reconstruction du contour.
            msp.add_line((200, 200), (300, 300), dxfattribs={"layer": "COTES"})

        chemin = self._fichier(piece)
        regles = {"coupe": "decoupe", "cotes": "ignore"}
        resultat = extraire_geometrie(chemin, "dxf", regles_calques=regles)
        self.assertAlmostEqual(resultat.surface_mm2, 5000, delta=1)
        self.assertEqual(resultat.avertissements, [])


class CalculerImbricationTests(TestCase):
    def test_grille_deux_par_deux_tient_sur_une_feuille(self):
        items = [
            ItemANester(
                piece_id=1, largeur_mm=400, hauteur_mm=400, surface_mm2=160_000, pas_rotation_deg=None, quantite=4
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
                piece_id=1, largeur_mm=400, hauteur_mm=400, surface_mm2=160_000, pas_rotation_deg=None, quantite=5
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
                piece_id=1, largeur_mm=130, hauteur_mm=70, surface_mm2=9100, pas_rotation_deg=90, quantite=12
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
                piece_id=99, largeur_mm=2000, hauteur_mm=2000, surface_mm2=4_000_000, pas_rotation_deg=90, quantite=1
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
                piece_id=1, largeur_mm=900, hauteur_mm=400, surface_mm2=360_000, pas_rotation_deg=90, quantite=1
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
                piece_id=1, largeur_mm=100, hauteur_mm=100, surface_mm2=7854, pas_rotation_deg=None, quantite=1
            )
        ]
        resultat = calculer_imbrication(
            items, largeur_feuille_mm=100, longueur_feuille_mm=100, marge_bord_mm=0, espacement_pieces_mm=0
        )
        self.assertAlmostEqual(resultat.taux_utilisation_pct, 78.54, places=1)


class ImbricationAnglesLibresTests(TestCase):
    """Le rectangle englobant d'une pièce non rectangulaire dépend de l'angle sous lequel on le
    calcule : une pièce en losange (carré tourné à 45°) a un rectangle englobant deux fois plus
    grand en aire à 0°/90° qu'une fois ramenée à son orientation « carrée » à 45° — de quoi
    vérifier que le pas de rotation influence réellement le placement, pas seulement les
    métadonnées."""

    def _diamant(self, **kwargs):
        return ItemANester(
            piece_id=1,
            largeur_mm=200,
            hauteur_mm=200,
            surface_mm2=20000,
            exterieur=[(100, 0), (0, 100), (-100, 0), (0, -100)],
            quantite=1,
            **kwargs,
        )

    def test_rotation_a_90_seulement_ne_suffit_pas(self):
        resultat = calculer_imbrication(
            [self._diamant(pas_rotation_deg=90)],
            largeur_feuille_mm=150,
            longueur_feuille_mm=150,
            marge_bord_mm=0,
            espacement_pieces_mm=0,
        )
        self.assertEqual(resultat.pieces_non_placees, [1])

    def test_rotation_a_45_permet_le_placement(self):
        resultat = calculer_imbrication(
            [self._diamant(pas_rotation_deg=45)],
            largeur_feuille_mm=150,
            longueur_feuille_mm=150,
            marge_bord_mm=0,
            espacement_pieces_mm=0,
        )
        self.assertEqual(resultat.pieces_non_placees, [])
        self.assertEqual(resultat.nb_feuilles, 1)
        self.assertEqual(resultat.placements[0].rotation_deg, 45)

    def test_symetrie_n_a_aucun_effet_observable_sur_le_placement(self):
        # Voir le docstring du module `imbrication.py` : un miroir ne change jamais la
        # largeur/hauteur du rectangle englobant, donc le moteur ne retourne jamais une pièce
        # — `symetrie_autorisee` ne doit changer ni la rotation choisie, ni `miroir` (toujours
        # False). Pièce chirale (forme en L, asymétrique) pour écarter tout cas particulier lié
        # à une pièce elle-même symétrique.
        piece_chirale = dict(
            piece_id=1,
            largeur_mm=3,
            hauteur_mm=2,
            surface_mm2=5,
            exterieur=[(0, 0), (3, 0), (3, 1), (1, 1), (1, 2), (0, 2)],
            pas_rotation_deg=45,
            quantite=1,
        )
        resultat_avec = calculer_imbrication(
            [ItemANester(symetrie_autorisee=True, **piece_chirale)],
            largeur_feuille_mm=10,
            longueur_feuille_mm=10,
        )
        resultat_sans = calculer_imbrication(
            [ItemANester(symetrie_autorisee=False, **piece_chirale)],
            largeur_feuille_mm=10,
            longueur_feuille_mm=10,
        )
        self.assertEqual(resultat_avec.placements[0].rotation_deg, resultat_sans.placements[0].rotation_deg)
        self.assertFalse(resultat_avec.placements[0].miroir)
        self.assertFalse(resultat_sans.placements[0].miroir)


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

    def test_matiere_et_epaisseur_persistees(self):
        acier = Matiere.objects.create(nom="Acier inox", densite=7.9)
        contenu = _dxf_bytes(_rectangle_avec_trou)
        piece = PieceDecoupe.objects.create(
            nom="Flasque",
            fichier_source=SimpleUploadedFile("flasque.dxf", contenu),
            matiere=acier,
            epaisseur=3,
        )
        piece.refresh_from_db()
        self.assertEqual(piece.matiere, acier)
        self.assertEqual(piece.epaisseur, 3)

    def test_profil_import_classe_la_gravure_et_desactive_le_trou(self):
        profil = ProfilImportDecoupe.objects.create(nom="Poste laser atelier")
        RegleProfilImportDecoupe.objects.create(profil=profil, calque="COUPE", role="decoupe")
        RegleProfilImportDecoupe.objects.create(profil=profil, calque="GRAVURE", role="gravure")

        def piece_avec_logo(msp):
            msp.add_lwpolyline([(0, 0), (100, 0), (100, 50), (0, 50)], close=True, dxfattribs={"layer": "COUPE"})
            msp.add_lwpolyline(
                [(40, 15), (60, 15), (50, 35)], close=True, dxfattribs={"layer": "GRAVURE"}
            )

        contenu = _dxf_bytes(piece_avec_logo)
        piece = PieceDecoupe.objects.create(
            nom="Flasque logo",
            fichier_source=SimpleUploadedFile("flasque_logo.dxf", contenu),
            profil_import=profil,
        )
        piece.importer_geometrie()
        piece.refresh_from_db()

        self.assertEqual(piece.nb_contours_interieurs, 0)
        self.assertTrue(piece.a_gravure)
        self.assertGreater(piece.longueur_gravure_mm, 0)
        self.assertIn("COUPE", piece.calques_detectes)
        self.assertIn("GRAVURE", piece.calques_detectes)
        # Comportement délibéré : la détection auto informe (a_gravure), mais ne modifie jamais
        # elle-même symetrie_autorisee après coup — seul le JS du formulaire d'ajout pré-suggère
        # la case décochée avant tout enregistrement (voir piecedecoupe_admin.js).
        self.assertTrue(piece.symetrie_autorisee)


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


class AnalyserFichierAdminViewTests(TestCase):
    """Vue AJAX appelée par piecedecoupe_admin.js dès la sélection du fichier, avant tout
    enregistrement — permet d'afficher l'analyse et l'aperçu en direct sur le formulaire."""

    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            "decoupe-admin", "decoupe-admin@example.com", "pass1234"
        )
        self.client.force_login(self.user)
        self.url = "/admin/decoupe/piecedecoupe/analyser/"

    def test_fichier_valide_renvoie_la_geometrie_et_un_apercu_svg(self):
        contenu = _dxf_bytes(_rectangle_avec_trou)
        reponse = self.client.post(
            self.url, {"fichier_source": SimpleUploadedFile("flasque.dxf", contenu)}
        )
        self.assertEqual(reponse.status_code, 200)
        data = reponse.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["statut_display"], "Importée")
        self.assertEqual(data["format_source_display"], "DXF")
        self.assertEqual(data["nb_contours_interieurs"], 1)
        self.assertAlmostEqual(data["largeur_mm"], 100, places=1)
        self.assertAlmostEqual(data["hauteur_mm"], 50, places=1)
        self.assertIn("<svg", data["svg"])

    def test_fichier_sans_contour_ferme_renvoie_une_erreur_lisible(self):
        contenu = _dxf_bytes(lambda msp: msp.add_text("x", dxfattribs={"insert": (0, 0)}))
        reponse = self.client.post(
            self.url, {"fichier_source": SimpleUploadedFile("invalide.dxf", contenu)}
        )
        self.assertEqual(reponse.status_code, 200)
        data = reponse.json()
        self.assertFalse(data["ok"])
        self.assertEqual(data["statut_display"], "Erreur d'import")
        self.assertTrue(data["message_erreur"])

    def test_extension_non_supportee_rejetee(self):
        reponse = self.client.post(
            self.url,
            {"fichier_source": SimpleUploadedFile("piece.txt", b"pas un dxf")},
        )
        self.assertEqual(reponse.status_code, 400)

    def test_aucun_fichier_rejete(self):
        reponse = self.client.post(self.url, {})
        self.assertEqual(reponse.status_code, 400)

    def test_utilisateur_non_authentifie_redirige_vers_le_login(self):
        self.client.logout()
        contenu = _dxf_bytes(_rectangle_avec_trou)
        reponse = self.client.post(
            self.url, {"fichier_source": SimpleUploadedFile("flasque.dxf", contenu)}
        )
        self.assertEqual(reponse.status_code, 302)

    def test_profil_import_applique_a_l_analyse_en_direct(self):
        profil = ProfilImportDecoupe.objects.create(nom="Poste laser")
        RegleProfilImportDecoupe.objects.create(profil=profil, calque="COUPE", role="decoupe")
        RegleProfilImportDecoupe.objects.create(profil=profil, calque="GRAVURE", role="gravure")

        def piece_avec_logo(msp):
            msp.add_lwpolyline([(0, 0), (100, 0), (100, 50), (0, 50)], close=True, dxfattribs={"layer": "COUPE"})
            msp.add_lwpolyline([(40, 15), (60, 15), (50, 35)], close=True, dxfattribs={"layer": "GRAVURE"})

        contenu = _dxf_bytes(piece_avec_logo)
        reponse = self.client.post(
            self.url,
            {
                "fichier_source": SimpleUploadedFile("flasque_logo.dxf", contenu),
                "profil_import": profil.pk,
            },
        )
        self.assertEqual(reponse.status_code, 200)
        data = reponse.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["nb_contours_interieurs"], 0)
        self.assertTrue(data["a_gravure"])
        self.assertGreater(data["longueur_gravure_mm"], 0)
        self.assertIn("COUPE", data["calques_detectes"])
        self.assertIn("GRAVURE", data["calques_detectes"])


class ApercuPieceAdminTests(TestCase):
    """Aperçu SVG affiché sur la fiche PieceDecoupe (formulaire d'ajout et de modification)."""

    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            "apercu-admin", "apercu-admin@example.com", "pass1234"
        )
        self.client.force_login(self.user)

    def test_apercu_absent_avant_import(self):
        reponse = self.client.get("/admin/decoupe/piecedecoupe/add/")
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, "En attente d&#x27;import du fichier source.")

    def test_apercu_svg_present_apres_import(self):
        contenu = _dxf_bytes(_rectangle_avec_trou)
        piece = PieceDecoupe.objects.create(
            nom="Flasque", fichier_source=SimpleUploadedFile("flasque.dxf", contenu)
        )
        piece.importer_geometrie()
        reponse = self.client.get(f"/admin/decoupe/piecedecoupe/{piece.pk}/change/")
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, "<svg")


class ProfilImportDecoupeModelTests(TestCase):
    def test_regles_par_calque_est_insensible_a_la_casse_du_dict(self):
        profil = ProfilImportDecoupe.objects.create(nom="Poste laser")
        RegleProfilImportDecoupe.objects.create(profil=profil, calque="Gravure", role="gravure")
        self.assertEqual(profil.regles_par_calque(), {"gravure": "gravure"})

    def test_reimport_avec_nouveau_profil_reclasse_la_piece(self):
        # Changer le profil d'import sur une pièce déjà enregistrée doit la reclasser sans
        # ré-uploader le fichier (voir PieceDecoupeAdmin.save_model : déclenche un réimport dès
        # que `profil_import` change, même si `fichier_source` ne change pas).
        profil = ProfilImportDecoupe.objects.create(nom="Poste laser")
        RegleProfilImportDecoupe.objects.create(profil=profil, calque="COUPE", role="decoupe")
        RegleProfilImportDecoupe.objects.create(profil=profil, calque="GRAVURE", role="gravure")

        def piece_avec_logo(msp):
            msp.add_lwpolyline([(0, 0), (100, 0), (100, 50), (0, 50)], close=True, dxfattribs={"layer": "COUPE"})
            msp.add_lwpolyline([(40, 15), (60, 15), (50, 35)], close=True, dxfattribs={"layer": "GRAVURE"})

        contenu = _dxf_bytes(piece_avec_logo)
        piece = PieceDecoupe.objects.create(
            nom="Flasque logo", fichier_source=SimpleUploadedFile("flasque_logo.dxf", contenu)
        )
        piece.importer_geometrie()
        piece.refresh_from_db()
        self.assertEqual(piece.nb_contours_interieurs, 1)  # sans profil : gravure prise pour un trou

        piece.profil_import = profil
        piece.save()
        piece.importer_geometrie()
        piece.refresh_from_db()
        self.assertEqual(piece.nb_contours_interieurs, 0)
        self.assertTrue(piece.a_gravure)
