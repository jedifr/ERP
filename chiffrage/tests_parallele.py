"""Imbrication parallèle : même résultat que le calcul séquentiel, repli en cas de problème, nombre de processus."""

import os
from unittest import mock

from django.test import TestCase, override_settings

from decoupe.models import FormatTole
from decoupe.services import imbrication_devis as imb
from decoupe.services import parallele
from decoupe.services.devis_pieces import pieces_du_devis

from . import tests_pieces as base


class ImbricationParalleleTests(TestCase):
    setUp = base.ChiffrageDevisTests.setUp
    piece = base.ChiffrageDevisTests.piece

    def preparer(self):
        self.piece("a", 3)
        self.piece("b", 2)
        groupes, _ = imb.grouper(pieces_du_devis(self.devis))
        formats = imb.formats_compatibles("laser", imb.formats_actifs())
        self.assertGreaterEqual(len(formats), 2)
        return groupes[0], formats

    def calculer(self, groupe, formats):
        imb._CACHE.clear()
        lignes, meilleur = imb.comparer_formats(groupe, formats, 5.0, 0.0, None)
        return [(f.pk, r.nb_feuilles, round(r.surface_consommee_mm2, 3), len(r.placements)) for f, r, _ in lignes if r is not None], meilleur.pk

    @override_settings(IMBRICATION_PROCESSUS=1)
    def test_sequentiel_de_reference(self):
        groupe, formats = self.preparer()
        self.assertEqual(parallele.processus(), 1)
        attendu = self.calculer(groupe, formats)
        self.assertTrue(attendu[0])

    def test_resultat_identique_en_parallele(self):
        groupe, formats = self.preparer()
        with override_settings(IMBRICATION_PROCESSUS=1):
            sequentiel = self.calculer(groupe, formats)
        with override_settings(IMBRICATION_PROCESSUS=3), mock.patch.object(parallele, "calculer", wraps=parallele.calculer) as espion:
            en_parallele = self.calculer(groupe, formats)
        self.assertEqual(en_parallele, sequentiel)
        self.assertEqual(espion.call_count, 1)  # le calcul parallèle a bien eu lieu
        self.assertGreaterEqual(len(espion.call_args[0][0]), 2)

    def test_repli_sequentiel_si_les_processus_sont_inutilisables(self):
        groupe, formats = self.preparer()
        with override_settings(IMBRICATION_PROCESSUS=1):
            attendu = self.calculer(groupe, formats)
        with override_settings(IMBRICATION_PROCESSUS=3), mock.patch.object(parallele, "calculer", return_value={}):
            self.assertEqual(self.calculer(groupe, formats), attendu)

    def test_un_format_en_cache_n_est_pas_recalcule(self):
        groupe, formats = self.preparer()
        with override_settings(IMBRICATION_PROCESSUS=3):
            self.calculer(groupe, formats)
            with mock.patch.object(parallele, "calculer") as espion:
                imb.comparer_formats(groupe, formats, 5.0, 0.0, None)  # tout est déjà en cache
            espion.assert_not_called()

    def test_nombre_de_processus(self):
        with override_settings(IMBRICATION_PROCESSUS=4):
            self.assertEqual(parallele.processus(), 4)
        with override_settings(IMBRICATION_PROCESSUS=0):
            with mock.patch.object(os, "cpu_count", return_value=4):
                self.assertEqual(parallele.processus(), 3)  # un cœur reste libre
            with mock.patch.object(os, "cpu_count", return_value=20):
                self.assertEqual(parallele.processus(), parallele.PLAFOND_AUTO)
            with mock.patch.object(os, "cpu_count", return_value=1):
                self.assertEqual(parallele.processus(), 1)
