import json
import math
import tempfile
from pathlib import Path

import ezdxf
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
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
        self.assertEqual(
            resultat.calques_roles, {"COUPE": "decoupe", "GRAVURE": "gravure", "PLIAGE": "pliage"}
        )

    def test_erreur_porte_les_calques_pour_permettre_une_correction(self):
        # Tout classé en gravure/ignoré par erreur : plus aucune découpe, l'import échoue —
        # mais les calques doivent rester consultables pour corriger sans ré-uploader.
        chemin = self._fichier(self._piece_calques_multiples)
        regles = {"coupe": "gravure", "gravure": "gravure", "pliage": "ignore"}
        with self.assertRaises(ErreurImportGeometrie) as cm:
            extraire_geometrie(chemin, "dxf", regles_calques=regles)
        self.assertEqual(cm.exception.calques, ["COUPE", "GRAVURE", "PLIAGE"])
        self.assertEqual(cm.exception.calques_roles["COUPE"], "gravure")

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


class ImbricationDirectionCoinDepartTests(TestCase):
    """`direction` (horizontal/vertical) et `coin_depart` (bas_gauche par défaut) — valeurs
    obtenues par exécution directe puis fixées en dur (cf. commentaires), l'algorithme
    d'étagères n'étant pas trivial à vérifier à la main de bout en bout."""

    def test_coin_depart_reflete_les_coordonnees(self):
        # Deux pièces 100×80 sur une feuille utile 150×200 : une seule tient par rangée
        # (100+100=200 > 150), donc rangée 1 en (0,0), rangée 2 en (0,80) dans le repère
        # canonique (haut gauche, sans réflexion).
        item = ItemANester(piece_id=1, largeur_mm=100, hauteur_mm=80, surface_mm2=8000, quantite=2)
        attendu = {
            "haut_gauche": {(0.0, 0.0), (0.0, 80.0)},
            "bas_gauche": {(0.0, 200 - 80.0), (0.0, 200 - 160.0)},
            "haut_droite": {(150 - 100.0, 0.0), (150 - 100.0, 80.0)},
            "bas_droite": {(150 - 100.0, 200 - 80.0), (150 - 100.0, 200 - 160.0)},
        }
        for coin, points_attendus in attendu.items():
            resultat = calculer_imbrication(
                [item], largeur_feuille_mm=150, longueur_feuille_mm=200, coin_depart=coin
            )
            points = {(p.x_mm, p.y_mm) for p in resultat.placements}
            self.assertEqual(points, points_attendus, coin)

    def test_direction_verticale_peut_reduire_le_nombre_de_feuilles(self):
        # Deux pièces de tailles différentes (120×60 et 60×120) sur une feuille 200×150 :
        # en horizontal, la seconde ne rentre pas sous la première (60+120=180 > 150 de haut
        # utile) et ouvre une deuxième feuille ; en vertical, le même calcul — mené sur les
        # axes inversés — la fait tenir à côté sur la même feuille. Un exemple concret que le
        # sens de remplissage n'est pas qu'un simple réétiquetage : il peut changer le nombre
        # de feuilles nécessaires sur un lot de tailles mélangées.
        item_a = ItemANester(piece_id=1, largeur_mm=120, hauteur_mm=60, surface_mm2=7200, quantite=1)
        item_b = ItemANester(piece_id=2, largeur_mm=60, hauteur_mm=120, surface_mm2=7200, quantite=1)

        resultat_horizontal = calculer_imbrication(
            [item_a, item_b], largeur_feuille_mm=200, longueur_feuille_mm=150, direction="horizontal"
        )
        self.assertEqual(resultat_horizontal.nb_feuilles, 2)

        resultat_vertical = calculer_imbrication(
            [item_a, item_b], largeur_feuille_mm=200, longueur_feuille_mm=150, direction="vertical"
        )
        self.assertEqual(resultat_vertical.nb_feuilles, 1)

    def test_aucun_chevauchement_quelle_que_soit_la_combinaison(self):
        from shapely.geometry import box

        item = ItemANester(piece_id=1, largeur_mm=130, hauteur_mm=70, surface_mm2=9100, quantite=12)
        for direction in ("horizontal", "vertical"):
            for coin in ("bas_gauche", "bas_droite", "haut_gauche", "haut_droite"):
                resultat = calculer_imbrication(
                    [item],
                    largeur_feuille_mm=500,
                    longueur_feuille_mm=500,
                    marge_bord_mm=5,
                    espacement_pieces_mm=3,
                    direction=direction,
                    coin_depart=coin,
                )
                par_feuille = {}
                for p in resultat.placements:
                    par_feuille.setdefault(p.numero_feuille, []).append(
                        box(p.x_mm, p.y_mm, p.x_mm + p.largeur_placee_mm, p.y_mm + p.hauteur_placee_mm)
                    )
                    self.assertGreaterEqual(p.x_mm, 5 - 1e-6, (direction, coin))
                    self.assertGreaterEqual(p.y_mm, 5 - 1e-6, (direction, coin))
                    self.assertLessEqual(p.x_mm + p.largeur_placee_mm, 500 - 5 + 1e-6, (direction, coin))
                    self.assertLessEqual(p.y_mm + p.hauteur_placee_mm, 500 - 5 + 1e-6, (direction, coin))
                for rectangles in par_feuille.values():
                    for i, a in enumerate(rectangles):
                        for b in rectangles[i + 1 :]:
                            self.assertAlmostEqual(a.intersection(b).area, 0, places=6, msg=(direction, coin))


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


class ImbricationJobAdminApercuTests(TestCase):
    """L'aperçu visuel des feuilles n'était visible nulle part dans l'admin (seuls les
    chiffres — nb_feuilles, taux d'utilisation — l'étaient) ; `generer_svg_feuille` n'était
    branché que sur un endpoint API brut. Couvre son ajout sur la fiche ImbricationJob."""

    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            "imbrication-admin", "imbrication-admin@example.com", "pass1234"
        )
        self.client.force_login(self.user)
        contenu = _dxf_bytes(_rectangle_avec_trou)
        self.piece = PieceDecoupe.objects.create(
            nom="Flasque", fichier_source=SimpleUploadedFile("flasque.dxf", contenu)
        )
        self.piece.importer_geometrie()

    def test_aucune_feuille_avant_calcul(self):
        job = ImbricationJob.objects.create(largeur_feuille_mm=1000, longueur_feuille_mm=2000)
        reponse = self.client.get(f"/admin/decoupe/imbricationjob/{job.pk}/change/")
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, "Aucune feuille calculée pour l&#x27;instant.")

    def test_apercu_svg_par_feuille_apres_calcul(self):
        job = ImbricationJob.objects.create(largeur_feuille_mm=1000, longueur_feuille_mm=2000)
        ImbricationLigne.objects.create(job=job, piece=self.piece, quantite=3)
        job.calculer()

        reponse = self.client.get(f"/admin/decoupe/imbricationjob/{job.pk}/change/")
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, "Feuille 1")
        self.assertContains(reponse, "<svg")


class PrevisualiserImbricationAdminViewTests(TestCase):
    """Vue AJAX appelée par imbricationjob_admin.js à chaque changement de la fiche
    ImbricationJob (feuille, direction, coin de départ, lignes) — permet de voir le résultat
    en direct sans "Enregistrer et continuer les modifications", sur le formulaire d'ajout
    comme de modification."""

    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            "imbrication-live-admin", "imbrication-live-admin@example.com", "pass1234"
        )
        self.client.force_login(self.user)
        self.url = "/admin/decoupe/imbricationjob/previsualiser/"
        contenu = _dxf_bytes(_rectangle_avec_trou)
        self.piece = PieceDecoupe.objects.create(
            nom="Flasque", fichier_source=SimpleUploadedFile("flasque.dxf", contenu)
        )
        self.piece.importer_geometrie()

    def _poster(self, **kwargs):
        payload = {
            "largeur_feuille_mm": "1000",
            "longueur_feuille_mm": "2000",
            "marge_bord_mm": "5",
            "espacement_pieces_mm": "5",
            "direction": "horizontal",
            "coin_depart": "bas_gauche",
            "lignes": [],
        }
        payload.update(kwargs)
        return self.client.post(self.url, data=json.dumps(payload), content_type="application/json")

    def test_calcule_sans_rien_enregistrer(self):
        nb_avant = ImbricationJob.objects.count()
        reponse = self._poster(lignes=[{"piece": self.piece.pk, "quantite": 3}])
        self.assertEqual(reponse.status_code, 200)
        data = reponse.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["nb_feuilles"], 1)
        self.assertEqual(len(data["feuilles"]), 1)
        self.assertIn("<svg", data["feuilles"][0]["svg"])
        self.assertEqual(ImbricationJob.objects.count(), nb_avant)

    def test_sans_lignes_renvoie_un_resultat_vide(self):
        reponse = self._poster(lignes=[])
        self.assertEqual(reponse.status_code, 200)
        data = reponse.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["nb_feuilles"], 0)
        self.assertEqual(data["feuilles"], [])

    def test_dimensions_de_feuille_manquantes_ne_plante_pas(self):
        reponse = self._poster(largeur_feuille_mm="", longueur_feuille_mm="")
        self.assertEqual(reponse.status_code, 200)
        self.assertFalse(reponse.json()["ok"])

    def test_piece_non_importee_est_ignoree_sans_erreur(self):
        piece_en_echec = PieceDecoupe.objects.create(
            nom="Non importée", fichier_source=SimpleUploadedFile("x.dxf", _dxf_bytes(_rectangle_avec_trou))
        )
        reponse = self._poster(lignes=[{"piece": piece_en_echec.pk, "quantite": 1}])
        self.assertEqual(reponse.status_code, 200)
        data = reponse.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["nb_feuilles"], 0)
        self.assertIn("Non importée", data["pieces_ignorees"])

    def test_direction_verticale_appliquee_en_direct(self):
        # Même cas que ImbricationDirectionCoinDepartTests.test_direction_verticale_.... :
        # 2 pièces de tailles différentes tiennent sur 1 feuille en vertical, 2 en horizontal.
        contenu_a = _dxf_bytes(lambda msp: msp.add_lwpolyline([(0, 0), (120, 0), (120, 60), (0, 60)], close=True))
        piece_a = PieceDecoupe.objects.create(nom="A", fichier_source=SimpleUploadedFile("a.dxf", contenu_a))
        piece_a.importer_geometrie()
        contenu_b = _dxf_bytes(lambda msp: msp.add_lwpolyline([(0, 0), (60, 0), (60, 120), (0, 120)], close=True))
        piece_b = PieceDecoupe.objects.create(nom="B", fichier_source=SimpleUploadedFile("b.dxf", contenu_b))
        piece_b.importer_geometrie()

        lignes = [{"piece": piece_a.pk, "quantite": 1}, {"piece": piece_b.pk, "quantite": 1}]
        reponse_h = self._poster(
            largeur_feuille_mm="200", longueur_feuille_mm="150", marge_bord_mm="0", espacement_pieces_mm="0",
            direction="horizontal", lignes=lignes,
        )
        reponse_v = self._poster(
            largeur_feuille_mm="200", longueur_feuille_mm="150", marge_bord_mm="0", espacement_pieces_mm="0",
            direction="vertical", lignes=lignes,
        )
        self.assertEqual(reponse_h.json()["nb_feuilles"], 2)
        self.assertEqual(reponse_v.json()["nb_feuilles"], 1)

    def test_utilisateur_non_authentifie_redirige_vers_le_login(self):
        self.client.logout()
        reponse = self._poster(lignes=[{"piece": self.piece.pk, "quantite": 1}])
        self.assertEqual(reponse.status_code, 302)


class ApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        utilisateur = get_user_model().objects.create_user(username="op", password="x", email="jedifr@gmail.com")
        utilisateur.groups.add(Group.objects.get(name="Méthodes et bureau d'études"))
        self.client.force_authenticate(utilisateur)

    def test_api_refusee_sans_role(self):
        sans_role = get_user_model().objects.create_user(username="sans-role", password="x")
        self.client.force_authenticate(sans_role)
        self.assertEqual(self.client.get("/api/v1/pieces-decoupe/").status_code, 403)

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

    def _piece_avec_logo_dxf(self):
        def piece_avec_logo(msp):
            msp.add_lwpolyline([(0, 0), (100, 0), (100, 50), (0, 50)], close=True, dxfattribs={"layer": "COUPE"})
            msp.add_lwpolyline([(40, 15), (60, 15), (50, 35)], close=True, dxfattribs={"layer": "GRAVURE"})

        return _dxf_bytes(piece_avec_logo)

    def test_calques_roles_manuel_prioritaire_sur_le_profil(self):
        profil = ProfilImportDecoupe.objects.create(nom="Poste laser")
        RegleProfilImportDecoupe.objects.create(profil=profil, calque="GRAVURE", role="decoupe")

        reponse = self.client.post(
            self.url,
            {
                "fichier_source": SimpleUploadedFile("flasque_logo.dxf", self._piece_avec_logo_dxf()),
                "profil_import": profil.pk,
                "calques_roles": json.dumps({"COUPE": "decoupe", "GRAVURE": "gravure"}),
            },
        )
        data = reponse.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["nb_contours_interieurs"], 0)
        self.assertEqual(data["calques_roles"]["GRAVURE"], "gravure")

    def test_role_choices_renvoyes_pour_construire_le_tableau(self):
        reponse = self.client.post(
            self.url, {"fichier_source": SimpleUploadedFile("flasque.dxf", _dxf_bytes(_rectangle_avec_trou))}
        )
        data = reponse.json()
        valeurs = [choix[0] for choix in data["role_choices"]]
        self.assertEqual(set(valeurs), {"decoupe", "gravure", "pliage", "ignore"})

    def test_reanalyse_depuis_piece_existante_sans_reuploader(self):
        piece = PieceDecoupe.objects.create(
            nom="Flasque logo", fichier_source=SimpleUploadedFile("flasque_logo.dxf", self._piece_avec_logo_dxf())
        )
        piece.importer_geometrie()
        piece.refresh_from_db()
        self.assertEqual(piece.nb_contours_interieurs, 1)  # sans classement : gravure prise pour un trou

        reponse = self.client.post(
            self.url,
            {
                "piece_id": piece.pk,
                "calques_roles": json.dumps({"COUPE": "decoupe", "GRAVURE": "gravure"}),
            },
        )
        self.assertEqual(reponse.status_code, 200)
        data = reponse.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["nb_contours_interieurs"], 0)
        # Non persisté : la pièce en base n'a pas bougé.
        piece.refresh_from_db()
        self.assertEqual(piece.nb_contours_interieurs, 1)

    def test_erreur_renvoie_quand_meme_les_calques_pour_correction(self):
        reponse = self.client.post(
            self.url,
            {
                "fichier_source": SimpleUploadedFile("flasque_logo.dxf", self._piece_avec_logo_dxf()),
                "calques_roles": json.dumps({"COUPE": "ignore", "GRAVURE": "gravure"}),
            },
        )
        data = reponse.json()
        self.assertFalse(data["ok"])
        self.assertIn("COUPE", data["calques_detectes"])
        self.assertEqual(data["calques_roles"]["COUPE"], "ignore")


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

    def test_tableau_calques_absent_avant_import(self):
        reponse = self.client.get("/admin/decoupe/piecedecoupe/add/")
        self.assertEqual(reponse.status_code, 200)
        self.assertNotContains(reponse, "decoupe-calque-role")

    def test_tableau_calques_reflete_le_classement_manuel_persiste(self):
        def piece_avec_logo(msp):
            msp.add_lwpolyline([(0, 0), (100, 0), (100, 50), (0, 50)], close=True, dxfattribs={"layer": "COUPE"})
            msp.add_lwpolyline([(40, 15), (60, 15), (50, 35)], close=True, dxfattribs={"layer": "GRAVURE"})

        piece = PieceDecoupe.objects.create(
            nom="Flasque logo",
            fichier_source=SimpleUploadedFile("flasque_logo.dxf", _dxf_bytes(piece_avec_logo)),
            regles_calques_manuelles={"COUPE": "decoupe", "GRAVURE": "gravure"},
        )
        piece.importer_geometrie()

        reponse = self.client.get(f"/admin/decoupe/piecedecoupe/{piece.pk}/change/")
        contenu_html = reponse.content.decode()
        self.assertIn('data-calque="COUPE"', contenu_html)
        self.assertIn('data-calque="GRAVURE"', contenu_html)
        # La ligne GRAVURE doit avoir l'option "gravure" sélectionnée.
        bloc_gravure = contenu_html.split('data-calque="GRAVURE"')[1].split("</select>")[0]
        self.assertIn('value="gravure" selected', bloc_gravure)


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


class ReglesCalquesManuellesTests(TestCase):
    """Classement manuel par calque (PieceDecoupe.regles_calques_manuelles) — le tableau de
    calques affiché après analyse, un choix par calque, prioritaire sur le profil d'import :
    corrige le classement pièce par pièce sans devoir créer/modifier un profil réutilisable."""

    def _piece_avec_logo(self, msp):
        msp.add_lwpolyline([(0, 0), (100, 0), (100, 50), (0, 50)], close=True, dxfattribs={"layer": "COUPE"})
        msp.add_lwpolyline([(40, 15), (60, 15), (50, 35)], close=True, dxfattribs={"layer": "GRAVURE"})

    def test_regles_manuelles_prioritaires_sur_le_profil(self):
        profil = ProfilImportDecoupe.objects.create(nom="Poste laser")
        # Le profil classe (à tort) GRAVURE en découpe : sans classement manuel, ce serait un
        # trou de plus.
        RegleProfilImportDecoupe.objects.create(profil=profil, calque="GRAVURE", role="decoupe")
        RegleProfilImportDecoupe.objects.create(profil=profil, calque="COUPE", role="decoupe")

        contenu = _dxf_bytes(self._piece_avec_logo)
        piece = PieceDecoupe.objects.create(
            nom="Flasque logo",
            fichier_source=SimpleUploadedFile("flasque_logo.dxf", contenu),
            profil_import=profil,
            regles_calques_manuelles={"COUPE": "decoupe", "GRAVURE": "gravure"},
        )
        piece.importer_geometrie()
        piece.refresh_from_db()
        self.assertEqual(piece.nb_contours_interieurs, 0)
        self.assertTrue(piece.a_gravure)

    def test_regles_manuelles_vides_retombent_sur_le_profil(self):
        profil = ProfilImportDecoupe.objects.create(nom="Poste laser")
        RegleProfilImportDecoupe.objects.create(profil=profil, calque="COUPE", role="decoupe")
        RegleProfilImportDecoupe.objects.create(profil=profil, calque="GRAVURE", role="gravure")

        contenu = _dxf_bytes(self._piece_avec_logo)
        piece = PieceDecoupe.objects.create(
            nom="Flasque logo",
            fichier_source=SimpleUploadedFile("flasque_logo.dxf", contenu),
            profil_import=profil,
        )
        piece.importer_geometrie()
        piece.refresh_from_db()
        self.assertEqual(piece.nb_contours_interieurs, 0)
        self.assertTrue(piece.a_gravure)

    def test_reimport_declenche_par_changement_des_regles_manuelles_seules(self):
        # Modifier uniquement regles_calques_manuelles (ni le fichier, ni profil_import) sur
        # une fiche déjà enregistrée doit quand même déclencher un réimport — vérifié au niveau
        # admin via form.changed_data dans PieceDecoupeAdmin.save_model.
        contenu = _dxf_bytes(self._piece_avec_logo)
        piece = PieceDecoupe.objects.create(
            nom="Flasque logo", fichier_source=SimpleUploadedFile("flasque_logo.dxf", contenu)
        )
        piece.importer_geometrie()
        piece.refresh_from_db()
        self.assertEqual(piece.nb_contours_interieurs, 1)  # gravure prise pour un trou, par défaut

        piece.regles_calques_manuelles = {"COUPE": "decoupe", "GRAVURE": "gravure"}
        piece.save()
        piece.importer_geometrie()
        piece.refresh_from_db()
        self.assertEqual(piece.nb_contours_interieurs, 0)


class TempsDeDecoupeTests(TestCase):
    """Estimation du temps de découpe (jet d'eau) depuis la géométrie et les paramètres de coupe."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        from technique.models import PosteTravail

        from .models import ParametreCoupe, VitesseCoupe

        self.user = get_user_model().objects.create_superuser("temps-admin", "t@example.com", "pass-mot-de-passe-14")
        self.client.force_login(self.user)
        self.matiere = Matiere.objects.create(nom="Steel", densite=7800)
        self.poste = PosteTravail.objects.create(nom="Jet d'eau", mode_calcul=PosteTravail.ModeCalcul.HORAIRE)
        self.parametre = ParametreCoupe.objects.create(
            matiere=self.matiere, epaisseur_mm=10, poste=self.poste, percage_stationnaire_hp_s=10, temporisation_pointage_s=0.5,
            percement_lineaire_mm=6, chevauchement_mm=6, vitesse_marquage_mm_min=5000, temporisation_marquage_s=1,
        )
        for qualite, haute, basse in ((3, 154.9, 80.7), (4, 111.3, 66.2)):
            VitesseCoupe.objects.create(
                parametre=self.parametre, qualite=qualite, vitesse_haute_mm_min=haute, vitesse_basse_mm_min=basse, paliers=3,
                distance_acceleration_mm=3, distance_deceleration_mm=3,
            )
        self.article = Article.objects.create(reference="FLASQUE-JET", nature=Article.Nature.FABRIQUE)
        self.piece = PieceDecoupe.objects.create(
            nom="Plaque 100x50", fichier_source="decoupe/sources/x.dxf", format_source="dxf", statut=PieceDecoupe.Statut.OK,
            matiere=self.matiere, epaisseur=10, article=self.article, largeur_mm=100, hauteur_mm=50, surface_mm2=5000,
            perimetre_decoupe_mm=300,
            contour_json={"exterieur": [[0, 0], [100, 0], [100, 50], [0, 50]], "trous": []},
        )

    def test_rectangle_sans_trou(self):
        from .services.temps import estimer_temps_decoupe

        e = estimer_temps_decoupe(self.piece)
        coupe = 300 / (154.9 / 60) + 4 * 6 * (1 / (80.7 / 60) - 1 / (154.9 / 60)) + 12 / (80.7 / 60)
        self.assertAlmostEqual(e.coupe_s, coupe, places=3)
        self.assertAlmostEqual(e.percage_s, 10.5 * 0.5, places=6)  # un contour : (pointage + perçage stationnaire) × facteur de perçage
        self.assertAlmostEqual(e.deplacements_s, 2.75, places=6)
        self.assertEqual((e.nb_percages, e.nb_coins), (1, 4))
        self.assertAlmostEqual(e.total_s, coupe + 5.25 + 2.75, places=3)

    def test_trou_ajoute_un_percage_et_de_la_coupe(self):
        from .services.temps import estimer_temps_decoupe

        avant = estimer_temps_decoupe(self.piece)
        self.piece.contour_json["trous"] = [[[40, 20], [60, 20], [60, 30], [40, 30]]]
        apres = estimer_temps_decoupe(self.piece)
        self.assertEqual(apres.nb_percages, 2)
        self.assertGreater(apres.total_s, avant.total_s + 8)  # perçage 5,25 s + déplacement 2,75 s + la coupe du trou

    def test_qualite_plus_fine_plus_lente_et_coefficient(self):
        from .services.temps import estimer_temps_decoupe

        moyen = estimer_temps_decoupe(self.piece, qualite=3)
        fin = estimer_temps_decoupe(self.piece, qualite=4)
        self.assertGreater(fin.coupe_s, moyen.coupe_s)
        self.parametre.coefficient_ajustement = 1.5
        self.parametre.save()
        self.piece.refresh_from_db()
        self.assertAlmostEqual(estimer_temps_decoupe(self.piece).total_s, moyen.total_s * 1.5, places=3)

    def test_marquage(self):
        from .services.temps import estimer_temps_decoupe

        self.piece.gravure_json = {"traits": [[[0, 0], [50, 0]], [[0, 5], [50, 5]]]}
        self.piece.longueur_gravure_mm = 100
        e = estimer_temps_decoupe(self.piece)
        self.assertAlmostEqual(e.marquage_s, 100 / (5000 / 60) + 2 * 1, places=6)

    def test_cas_impossibles(self):
        from .services.temps import ErreurTemps, estimer_temps_decoupe

        with self.assertRaises(ErreurTemps):
            estimer_temps_decoupe(self.piece, qualite=5)  # pas de vitesses pour cette qualité
        self.piece.matiere = Matiere.objects.create(nom="Cuivre", densite=8900)
        with self.assertRaises(ErreurTemps):
            estimer_temps_decoupe(self.piece)  # aucune matière paramétrée
        sans_geometrie = PieceDecoupe.objects.create(nom="vide", fichier_source="decoupe/sources/v.dxf", matiere=self.matiere, epaisseur=10)
        with self.assertRaises(ErreurTemps):
            estimer_temps_decoupe(sans_geometrie)

    def test_epaisseur_la_plus_proche_avec_avertissement(self):
        from .services.temps import estimer_temps_decoupe

        self.piece.epaisseur = 12
        e = estimer_temps_decoupe(self.piece)
        self.assertTrue(any("la plus proche" in a for a in e.avertissements))

    def test_alimenter_la_gamme(self):
        import datetime

        from technique.models import Gamme

        from .services.gamme import alimenter_gamme
        from .services.temps import estimer_temps_decoupe

        jour = datetime.date(2026, 10, 5)
        etape, e = alimenter_gamme(self.piece, aujourdhui=jour)
        self.assertEqual((etape.poste, etape.origine, etape.ordre), (self.poste, "decoupe", 1))
        self.assertAlmostEqual(etape.temps_variable, round(e.total_min, 3))
        # même jour : mise à jour sur place
        self.parametre.coefficient_ajustement = 2
        self.parametre.save()
        self.piece.refresh_from_db()
        etape2, _ = alimenter_gamme(self.piece, aujourdhui=jour)
        self.assertEqual(etape2.pk, etape.pk)
        self.assertEqual(Gamme.objects.filter(article=self.article).count(), 1)
        # autre jour : l'ancienne étape est historisée, une nouvelle prend le relais au même rang
        etape3, _ = alimenter_gamme(self.piece, aujourdhui=jour + datetime.timedelta(days=3))
        etape.refresh_from_db()
        self.assertEqual(etape.date_fin, jour + datetime.timedelta(days=2))
        self.assertEqual((etape3.ordre, etape3.date_debut), (1, jour + datetime.timedelta(days=3)))

    def test_gamme_manuelle_jamais_ecrasee(self):
        import datetime

        from technique.models import Gamme

        from .services.gamme import alimenter_gamme

        jour = datetime.date(2026, 10, 5)
        manuelle = Gamme.objects.create(article=self.article, poste=self.poste, ordre=1, temps_variable=99, date_debut=datetime.date(2020, 1, 1))
        etape, _ = alimenter_gamme(self.piece, aujourdhui=jour)
        manuelle.refresh_from_db()
        self.assertEqual(manuelle.temps_variable, 99)
        self.assertEqual(etape.ordre, 2)

    def test_gamme_sans_article_ou_poste(self):
        from .services.gamme import alimenter_gamme
        from .services.temps import ErreurTemps

        self.piece.article = None
        with self.assertRaises(ErreurTemps):
            alimenter_gamme(self.piece)
        self.piece.article = self.article
        self.parametre.poste = None
        self.parametre.save()
        with self.assertRaises(ErreurTemps):
            alimenter_gamme(self.piece)

    def test_fiche_piece_et_action(self):
        from technique.models import Gamme

        page = self.client.get(f"/admin/decoupe/piecedecoupe/{self.piece.pk}/change/")
        self.assertContains(page, "min par pièce")
        self.assertContains(page, "Alimenter la gamme de l&#x27;article")
        reponse = self.client.get(f"/admin/decoupe/piecedecoupe/{self.piece.pk}/alimenter-gamme/", follow=True)
        self.assertContains(reponse, "par pièce.")
        self.assertTrue(Gamme.objects.filter(article=self.article, origine="decoupe").exists())

    def test_retoucher_une_etape_calculee_la_rend_manuelle(self):
        from technique.models import Gamme

        from .services.gamme import alimenter_gamme

        etape, _ = alimenter_gamme(self.piece)
        reponse = self.client.post(
            f"/admin/technique/gamme/{etape.pk}/change/",
            {"article": self.article.pk, "poste": self.poste.pk, "ordre": 1, "temps_fixe": "0", "temps_variable": "7", "date_debut": etape.date_debut.strftime("%d/%m/%Y")},
        )
        self.assertEqual(reponse.status_code, 302, reponse.context["adminform"].form.errors if reponse.context else "")
        etape.refresh_from_db()
        self.assertEqual((etape.temps_variable, etape.origine), (7, "manuelle"))

    def test_admin_parametres_de_coupe(self):
        self.assertContains(self.client.get("/admin/decoupe/parametrecoupe/"), "Steel")
        page = self.client.get(f"/admin/decoupe/parametrecoupe/{self.parametre.pk}/change/")
        self.assertContains(page, "154.9")
        self.assertContains(page, "Réglages du calcul")


class VitessesDepuisUsinabiliteTests(TestCase):
    """Usinabilités standard, vitesses calculées et duplication d'un paramètre relevé vers d'autres épaisseurs."""

    RELEVES = {  # (usinabilité, épaisseur) -> vitesses élevées relevées sur la machine, qualités 1,5 → 5
        (87.0, 10): [343.7, 246.9, 154.9, 111.3, 86.1],
        (220.0, 20): [450.1, 323.3, 202.8, 145.7, 112.7],
        (110.0, 8): [581.8, 417.9, 262.2, 188.3, 145.7],
    }

    def setUp(self):
        from technique.models import PosteTravail

        self.user = get_user_model().objects.create_superuser("vitesses-admin", "v@example.com", "pass-mot-de-passe-15")
        self.client.force_login(self.user)
        self.poste = PosteTravail.objects.create(nom="Jet", mode_calcul=PosteTravail.ModeCalcul.HORAIRE)
        self.matiere = Matiere.objects.create(nom="Acier S235", densite=7.8, usinabilite=87.6)

    def test_le_modele_retrouve_les_releves(self):
        from .services.vitesses import vitesses_depuis_usinabilite

        for (usinabilite, epaisseur), attendu in self.RELEVES.items():
            calcule = [v["vitesse_haute_mm_min"] for v in vitesses_depuis_usinabilite(usinabilite, epaisseur)]
            for c, a in zip(calcule, attendu):
                self.assertAlmostEqual(c / a, 1, delta=0.05, msg=f"{usinabilite} / {epaisseur} mm")

    def test_plus_epais_ou_moins_usinable_plus_lent(self):
        from .services.vitesses import vitesses_depuis_usinabilite

        v10 = vitesses_depuis_usinabilite(87.6, 10)[2]["vitesse_haute_mm_min"]
        self.assertLess(vitesses_depuis_usinabilite(87.6, 20)[2]["vitesse_haute_mm_min"], v10)
        self.assertGreater(vitesses_depuis_usinabilite(213, 10)[2]["vitesse_haute_mm_min"], v10)
        for v in vitesses_depuis_usinabilite(87.6, 15):
            self.assertLess(v["vitesse_basse_mm_min"], v["vitesse_haute_mm_min"])
        with self.assertRaises(ValueError):
            vitesses_depuis_usinabilite(0, 10)

    def test_usinabilites_standard_par_nom(self):
        from .services.vitesses import usinabilite_standard_pour

        attendus = {"Acier S235": 87.6, "Inox 304": 81.9, "Acier trempé": 80.4, "Acier inoxydable": 81.9, "Laiton": 110.0,
                    "Cuivre": 110.0, "Titane": 115.0, "Alliage de zinc": 136.0, "Aluminium 5754": 213.0, "Granit": 322.0,
                    "Marbre": 535.0, "Nylon": 538.0, "Plexiglas": 690.0, "Graphite": 879.0, "Polypropylène": 985.0}
        for nom, valeur in attendus.items():
            self.assertEqual(usinabilite_standard_pour(nom), valeur, nom)
        self.assertIsNone(usinabilite_standard_pour("Bois"))

    def test_action_matiere_usinabilite_standard(self):
        for nom in ("Inox 316", "Bois"):
            Matiere.objects.create(nom=nom, densite=7)
        self.client.post("/admin/technique/matiere/", {"action": "action_usinabilite_standard", "_selected_action": ["Inox 316", "Bois", "Acier S235"]}, follow=True)
        self.assertEqual(Matiere.objects.get(nom="Inox 316").usinabilite, 81.9)
        self.assertIsNone(Matiere.objects.get(nom="Bois").usinabilite)
        self.assertEqual(Matiere.objects.get(nom="Acier S235").usinabilite, 87.6)  # déjà renseignée : inchangée

    def modele(self):
        from .models import ParametreCoupe, VitesseCoupe

        parametre = ParametreCoupe.objects.create(
            matiere=self.matiere, epaisseur_mm=10, poste=self.poste, percage_stationnaire_hp_s=10, percage_stationnaire_bp_s=20,
            percement_lineaire_mm=3, chevauchement_mm=3, percage_circulaire_hp_tours=5, intervalle_pieces_mm=4,
        )
        VitesseCoupe.objects.create(parametre=parametre, qualite=3, vitesse_haute_mm_min=154.9, vitesse_basse_mm_min=80.7)
        return parametre

    def test_un_releve_machine_n_est_jamais_ecrase(self):
        from .services.parametres import ErreurParametre, calculer_vitesses

        parametre = self.modele()
        with self.assertRaises(ErreurParametre):
            calculer_vitesses(parametre)
        self.assertEqual(parametre.vitesses.get().vitesse_haute_mm_min, 154.9)

    def test_dupliquer_vers_d_autres_epaisseurs(self):
        from .models import ParametreCoupe
        from .services.parametres import dupliquer_vers_epaisseurs

        modele = self.modele()
        crees, existants = dupliquer_vers_epaisseurs(modele, [5, 10, 20])
        self.assertEqual([p.epaisseur_mm for p in crees], [5, 20])
        self.assertEqual(existants, [10])
        p20 = ParametreCoupe.objects.get(epaisseur_mm=20)
        self.assertEqual((p20.origine, p20.poste, p20.matiere), ("calcule", self.poste, self.matiere))
        self.assertEqual((p20.percage_stationnaire_hp_s, p20.percement_lineaire_mm, p20.percage_stationnaire_bp_s), (20, 6, 40))  # ∝ épaisseur
        self.assertEqual(p20.intervalle_pieces_mm, 4)  # inchangé
        self.assertEqual(p20.vitesses.count(), 5)
        self.assertEqual(ParametreCoupe.objects.get(epaisseur_mm=10).origine, "machine")

    def test_dupliquer_sans_usinabilite_refuse(self):
        from .services.parametres import ErreurParametre, dupliquer_vers_epaisseurs

        modele = self.modele()
        self.matiere.usinabilite = None
        self.matiere.save()
        with self.assertRaises(ErreurParametre):
            dupliquer_vers_epaisseurs(modele, [5])

    def test_admin_dupliquer_et_calculer(self):
        from .models import ParametreCoupe

        modele = self.modele()
        page = self.client.get(f"/admin/decoupe/parametrecoupe/{modele.pk}/dupliquer-epaisseurs/")
        self.assertContains(page, "proportionnels à l")
        reponse = self.client.post(f"/admin/decoupe/parametrecoupe/{modele.pk}/dupliquer-epaisseurs/", {"epaisseurs": "6, 8,12"}, follow=True)
        self.assertContains(reponse, "3 paramètre(s) créé(s)")
        calcule = ParametreCoupe.objects.get(epaisseur_mm=12)
        reponse = self.client.post(f"/admin/decoupe/parametrecoupe/{modele.pk}/dupliquer-epaisseurs/", {"epaisseurs": "abc"}, follow=True)
        self.assertContains(reponse, "séparées par des virgules")
        # l'action de liste refuse le relevé machine et recalcule l'estimation
        reponse = self.client.post("/admin/decoupe/parametrecoupe/", {"action": "action_calculer_vitesses", "_selected_action": [modele.pk, calcule.pk]}, follow=True)
        self.assertContains(reponse, "relevées sur la machine")
        self.assertContains(reponse, "vitesses calculées")

    def test_l_estimation_previent_quand_les_vitesses_sont_calculees(self):
        from .services.parametres import dupliquer_vers_epaisseurs
        from .services.temps import estimer_temps_decoupe

        crees, _ = dupliquer_vers_epaisseurs(self.modele(), [12])
        piece = PieceDecoupe.objects.create(
            nom="P", fichier_source="decoupe/sources/p.dxf", statut=PieceDecoupe.Statut.OK, matiere=self.matiere, epaisseur=12,
            contour_json={"exterieur": [[0, 0], [100, 0], [100, 50], [0, 50]], "trous": []},
        )
        e = estimer_temps_decoupe(piece)
        self.assertTrue(any("calculées depuis l'usinabilité" in a for a in e.avertissements))
        self.assertGreater(e.total_s, 0)


class ImportMaterialsLuaEtCalibrageTests(TestCase):
    """Import du materials.lua d'IGEMS et calage du temps de découpe sur des temps réels (même pièce, 5 matières)."""

    DONNEES = Path(__file__).parent / "tests_data"
    # (nom dans le fichier, épaisseur mm, temps réel en minutes) — pièce « piece_calibrage.dxf », qualité 3
    TEMPS_REELS = [("Aluminium", 30, 46.5), ("Aluminium", 10, 11.75), ("Stainless Steel", 20, 78.0), ("Copper", 15, 39.0), ("Steel", 5, 14.1)]
    # plaque percée (1101,7 mm de coupe, 4 contours), qualité 3
    TEMPS_REELS_PLAQUE = [("Steel", 25, 27.75), ("Steel", 35, 43.5), ("Aluminium", 50, 27.75)]

    def setUp(self):
        from technique.models import PosteTravail

        self.user = get_user_model().objects.create_superuser("lua-admin", "l@example.com", "pass-mot-de-passe-16")
        self.client.force_login(self.user)
        self.poste = PosteTravail.objects.create(nom="Jet d'eau lua", mode_calcul=PosteTravail.ModeCalcul.HORAIRE)
        self.texte = (self.DONNEES / "materials_extrait.lua").read_text()

    def importer(self):
        from .services.lua_materiaux import NOMS_FRANCAIS, importer_materiaux, lire_materials_lua

        entrees = lire_materials_lua(self.texte)
        return entrees, importer_materiaux(entrees, NOMS_FRANCAIS, poste=self.poste)

    def test_lecture_du_fichier(self):
        from .services.lua_materiaux import ErreurLua, lire_materials_lua

        entrees = lire_materials_lua(self.texte)
        self.assertEqual(len(entrees), 8)
        acier = next(e for e in entrees if e["nom"] == "Steel" and e["epaisseur"] == 5)
        self.assertEqual((acier["epaisseur"], acier["usinabilite"], acier["densite"]), (5.0, 87.0, 7.8))
        self.assertEqual((acier["percage_hp_s"], acier["percage_bp_s"], acier["linear"], acier["overcut"], acier["intervalle"]), (5, 10, 1.5, 1.5, 4))
        self.assertEqual(acier["qualites"][2]["paliers"], 2)
        for invalide in ("n'importe quoi", "materials={}"):
            with self.assertRaises(ErreurLua):
                lire_materials_lua(invalide)

    def test_import_cree_matieres_parametres_et_vitesses(self):
        from technique.models import Matiere

        from .models import ParametreCoupe

        _entrees, stats = self.importer()
        self.assertEqual((stats["crees"], stats["matieres_creees"]), (8, 4))  # aluminium ×3, acier ×3 : une seule matière chacun
        acier = ParametreCoupe.objects.get(matiere__nom="Acier", epaisseur_mm=5)
        self.assertEqual((acier.origine, acier.poste, acier.usinabilite, acier.percage_stationnaire_hp_s), ("calcule", self.poste, 87.0, 5))
        self.assertEqual(acier.vitesses.count(), 5)
        vitesse = acier.vitesses.get(qualite=3)
        self.assertEqual((vitesse.paliers, vitesse.distance_acceleration_mm), (2, 1.5))
        self.assertEqual(Matiere.objects.get(nom="Acier").usinabilite, 87.0)
        # un second import met à jour sans doublon
        _entrees, stats = self.importer()
        self.assertEqual((stats["crees"], stats["mis_a_jour"]), (0, 8))
        self.assertEqual(ParametreCoupe.objects.count(), 8)

    def test_import_ne_touche_pas_aux_releves_machine_et_ignore_les_matieres_non_associees(self):
        from technique.models import Matiere

        from .models import ParametreCoupe
        from .services.lua_materiaux import importer_materiaux, lire_materials_lua

        matiere = Matiere.objects.create(nom="Acier", densite=7.8)
        releve = ParametreCoupe.objects.create(matiere=matiere, epaisseur_mm=5, poste=self.poste, percage_stationnaire_hp_s=99)
        entrees = lire_materials_lua(self.texte)
        stats = importer_materiaux(entrees, {"Steel": "Acier", "Copper": ""}, poste=self.poste)
        releve.refresh_from_db()
        self.assertEqual((releve.origine, releve.percage_stationnaire_hp_s), ("machine", 99))
        self.assertEqual((stats["proteges"], stats["ignores"], stats["crees"]), (1, 5, 2))  # acier 5 mm relevé protégé ; acier 25 et 35 créés

    def piece_plaque(self):
        """Plaque 155,5 × 142,4 mm percée d'un polygone et de deux cercles (1101,7 mm de coupe, 4 contours)."""
        import math

        polygone = [[14.734, 55.469], [14.734, 103.594], [40.0, 103.594], [40.0, 136.078], [69.906, 150.172], [104.625, 147.25],
                    [123.703, 108.75], [113.219, 66.984], [85.719, 101.359], [73.344, 81.594], [74.031, 56.5]]
        cercle = lambda cx, cy, r: [[cx + r * math.cos(2 * math.pi * i / 360), cy + r * math.sin(2 * math.pi * i / 360)] for i in range(360)]
        return PieceDecoupe.objects.create(
            nom="Plaque", fichier_source="decoupe/sources/plaque.dxf", format_source="dxf", statut=PieceDecoupe.Statut.OK, qualite_coupe=3,
            contour_json={"exterieur": [[0, 0], [155.543, 0], [155.543, 142.414], [0, 142.414]],
                          "trous": [polygone, cercle(16.625, 147.422, 5.0), cercle(97.406, 55.469, 10.0)]},
        )

    def test_calage_sur_les_temps_reels(self):
        import tempfile

        from django.core.files import File
        from django.test import override_settings

        from technique.models import Matiere

        from .services.lua_materiaux import NOMS_FRANCAIS
        from .services.temps import estimer_temps_decoupe

        self.importer()
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            arrondie = PieceDecoupe(nom="Calibrage", epaisseur=10, qualite_coupe=3)
            arrondie.fichier_source.save("piece_calibrage.dxf", File(open(self.DONNEES / "piece_calibrage.dxf", "rb")), save=False)
            arrondie.save()
            self.assertTrue(arrondie.importer_geometrie())
        self.assertEqual(arrondie.nb_contours_interieurs, 5)
        plaque = self.piece_plaque()
        ecarts = []
        for piece, cas in ((arrondie, self.TEMPS_REELS), (plaque, self.TEMPS_REELS_PLAQUE)):
            for nom, epaisseur, reel in cas:
                piece.matiere = Matiere.objects.get(nom=NOMS_FRANCAIS[nom])
                piece.epaisseur = epaisseur
                calcule = estimer_temps_decoupe(piece).total_min
                ecarts.append(calcule / reel - 1)
                self.assertAlmostEqual(calcule / reel, 1, delta=0.08, msg=f"{nom} {epaisseur} mm : calculé {calcule:.1f} min, réel {reel} min")
        self.assertLess(sum(abs(e) for e in ecarts) / len(ecarts), 0.04)  # écart moyen < 4 % sur les 8 temps réels

    def test_decomposition_du_logiciel_pour_la_plaque_en_alu_50(self):
        """Le logiciel de la machine donne pour cette plaque : coupe 25 min 43 s, perçage 1 min 44 s, transferts 11 s."""
        from technique.models import Matiere

        from .services.lua_materiaux import NOMS_FRANCAIS
        from .services.temps import estimer_temps_decoupe

        self.importer()
        plaque = self.piece_plaque()
        plaque.matiere, plaque.epaisseur = Matiere.objects.get(nom=NOMS_FRANCAIS["Aluminium"]), 50
        e = estimer_temps_decoupe(plaque)
        self.assertAlmostEqual(e.longueur_coupe_mm - 4 * 30, 1101.7, delta=15)  # 4 contours × (amorce + chevauchement de 15 mm)
        self.assertAlmostEqual(e.coupe_s, 25 * 60 + 43, delta=0.08 * 1543)
        self.assertAlmostEqual(e.percage_s, 104, delta=10)
        self.assertAlmostEqual(e.deplacements_s, 11, delta=0.5)

    def test_admin_import_en_deux_temps(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        from .models import ParametreCoupe

        url = "/admin/decoupe/parametrecoupe/importer-lua/"
        self.assertContains(self.client.get(url), "Lire le fichier")
        page = self.client.post(url, {"fichier": SimpleUploadedFile("materials.lua", self.texte.encode())})
        for attendu in ("Steel", "Stainless Steel", "Matière de l'ERP", 'value="Acier"', 'value="Inox"'):
            self.assertContains(page, attendu)
        reponse = self.client.post(url, {"confirmer": "1", "poste": self.poste.pk, "remplacer": "on", "matiere__Steel": "Acier",
                                         "matiere__Stainless Steel": "Inox", "matiere__Aluminium": "Aluminium", "matiere__Copper": ""}, follow=True)
        self.assertContains(reponse, "Import terminé : 7 paramètre(s) créé(s)")
        self.assertEqual(ParametreCoupe.objects.count(), 7)
        # fichier invalide, ou confirmation sans fichier en mémoire
        invalide = self.client.post(url, {"fichier": SimpleUploadedFile("x.lua", b"rien")}, follow=True)
        self.assertContains(invalide, "materials.lua")
        vide = self.client.post(url, {"confirmer": "1"}, follow=True)
        self.assertContains(vide, "plus en mémoire")
