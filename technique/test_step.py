import tempfile
from pathlib import Path
from unittest import skipIf

from django.test import SimpleTestCase

from .step import ErreurLectureStep, analyser_step

try:
    import cadquery as cq
except ImportError:  # dépendance optionnelle
    cq = None


@skipIf(cq is None, "cadquery non installé")
class AnalyserStepTests(SimpleTestCase):
    def _ecrire_step(self, dossier, forme, nom="piece.step"):
        chemin = Path(dossier) / nom
        cq.exporters.export(forme, str(chemin))
        return chemin

    def test_plaque_percee(self):
        # Plaque 100 x 50 x 5 mm avec un trou Ø10 traversant.
        plaque = cq.Workplane("XY").box(100, 50, 5).faces(">Z").workplane().hole(10)
        with tempfile.TemporaryDirectory() as dossier:
            analyse = analyser_step(self._ecrire_step(dossier, plaque))

        self.assertAlmostEqual(analyse.longueur_mm, 100, places=2)
        self.assertAlmostEqual(analyse.largeur_mm, 50, places=2)
        self.assertAlmostEqual(analyse.hauteur_mm, 5, places=2)
        self.assertEqual(analyse.nb_solides, 1)
        volume_attendu = 100 * 50 * 5 - 3.141592653589793 * 5**2 * 5
        self.assertAlmostEqual(analyse.volume_mm3, volume_attendu, delta=0.1)
        # Acier (7,85 kg/dm³) : on vérifie la conversion mm³ -> dm³.
        self.assertAlmostEqual(analyse.masse_kg(7.85), volume_attendu / 1e6 * 7.85, places=4)

    def test_dimensions_independantes_de_l_orientation(self):
        barre = cq.Workplane("XY").box(5, 100, 20)
        with tempfile.TemporaryDirectory() as dossier:
            analyse = analyser_step(self._ecrire_step(dossier, barre))
        self.assertEqual(
            (analyse.longueur_mm, analyse.largeur_mm, analyse.hauteur_mm), (100.0, 20.0, 5.0)
        )

    def test_fichier_invalide(self):
        with tempfile.TemporaryDirectory() as dossier:
            chemin = Path(dossier) / "faux.step"
            chemin.write_text("ceci n'est pas un STEP")
            with self.assertRaises(ErreurLectureStep):
                analyser_step(chemin)
