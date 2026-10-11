"""Réglage machine par tôle : une mise en place par tôle posée, répartie entre les pièces du groupe."""

import json
from decimal import Decimal

from django.test import TestCase

from decoupe.models import ParametreCoupe, PieceDecoupe
from technique.models import Gamme

from . import reglage
from . import tests_pieces as base
from .models import DevisLigne


class ReglageParToleTests(TestCase):
    # On réutilise le décor (devis, tôle, poste laser tarifé, format) et les gestes du chiffrage des pièces sans rejouer ses tests.
    setUp = base.ChiffrageDevisTests.setUp
    piece = base.ChiffrageDevisTests.piece
    retenir = base.ChiffrageDevisTests.retenir
    ajouter = base.ChiffrageDevisTests.ajouter

    def avec_mise_en_place(self, minutes=10):
        self.poste.temps_mise_en_place_min = minutes
        self.poste.save()

    def nb_toles(self):
        """Nombre de tôles de l'imbrication retenue du (seul) groupe du devis."""
        from decoupe.services import imbrication_devis as imb
        from decoupe.services.devis_pieces import pieces_du_devis

        groupes, _ = imb.grouper(pieces_du_devis(self.devis))
        piece = groupes[0].pieces[0]
        return imb.imbriquer_groupe(groupes[0], piece.format_tole, piece.marge_bord_mm, 0.0, piece.tole).nb_feuilles

    def parts(self):
        self.devis.__dict__.pop("_cache_reglage", None)
        return reglage.parts_par_piece(self.devis)

    def test_un_seul_reglage_pour_toutes_les_pieces_d_une_tole(self):
        self.avec_mise_en_place(10)
        pieces = [self.piece(nom, 1) for nom in ("a", "b", "c")]
        self.retenir()
        parts = self.parts()
        nb = self.nb_toles()
        self.assertLess(nb, len(pieces))  # trois pièces sur moins de trois tôles : le réglage n'est pas compté par pièce
        minutes = sum(p.minutes for p in parts.values())
        self.assertAlmostEqual(minutes, 10 * nb, places=6)  # nb tôles × 10 min, quel que soit le nombre de pièces
        cout = sum(p.cout for p in parts.values())
        self.assertAlmostEqual(float(cout), nb * 20, delta=0.01)  # 10 min × 120 €/h par tôle (les parts sont arrondies au dix-millième)
        self.assertTrue(all(not p.estime for p in parts.values()))
        self.assertEqual(len(parts), len(pieces))

    def test_autant_de_reglages_que_de_toles(self):
        self.avec_mise_en_place(10)
        self.piece("a", 400)  # assez de pièces pour remplir plusieurs tôles
        self.retenir()
        piece = PieceDecoupe.objects.get()
        part = self.parts()[piece.pk]
        from decoupe.services import imbrication_devis as imb
        groupes, _ = imb.grouper([piece])
        nb = imb.imbriquer_groupe(groupes[0], piece.format_tole, piece.marge_bord_mm, 0.0, piece.tole).nb_feuilles
        self.assertGreater(nb, 1)
        self.assertAlmostEqual(part.minutes, nb * 10, places=6)
        self.assertIn(f"{nb} tôles × 10 min", part.note)

    def test_exception_par_epaisseur_remplace_la_valeur_du_poste(self):
        self.avec_mise_en_place(10)
        ParametreCoupe.objects.filter(procede="laser", epaisseur_mm=10).update(temps_mise_en_place_min=25)
        self.piece("a", 2)
        self.retenir()
        self.assertAlmostEqual(sum(p.minutes for p in self.parts().values()), 25 * self.nb_toles(), places=6)

    def test_tole_non_retenue_estimee_avec_avertissement(self):
        self.avec_mise_en_place(10)
        self.piece("a", 2)
        parts = self.parts()
        part = next(iter(parts.values()))
        self.assertTrue(part.estime)
        self.assertGreater(part.minutes, 0)
        self.assertEqual(part.minutes % 10, 0)  # un nombre entier de tôles × 10 min
        self.assertTrue(any("non retenue" in a for a in part.avertissements))
        self.assertIn("(estimé)", part.note)

    def test_sans_temps_de_mise_en_place_rien_n_est_ajoute(self):
        self.piece("a", 2)
        self.assertEqual(sum(p.cout for p in self.parts().values()), 0)

    def test_le_reglage_se_retrouve_dans_la_ligne_du_devis(self):
        self.avec_mise_en_place(10)
        self.piece("a", 1)
        self.piece("b", 1)
        self.retenir()
        nb = self.nb_toles()
        self.ajouter()
        lignes = list(DevisLigne.objects.filter(devis=self.devis))
        self.assertEqual(len(lignes), 2)
        total_reglage = sum(l.prix_vente_reglage for l in lignes)
        # coût du réglage 20 € réparti, majoré de la marge du poste (0 par défaut) : somme inchangée, incluse dans le prix des opérations
        self.assertAlmostEqual(float(total_reglage), nb * 20, delta=0.02)
        for ligne in lignes:
            self.assertIn(f"{nb} tôle{'s' if nb > 1 else ''} × 10 min", ligne.note_reglage)
            self.assertGreaterEqual(ligne.prix_vente_operations, ligne.prix_vente_reglage)

    def test_l_ancien_reglage_de_l_etape_de_decoupe_est_ignore(self):
        self.avec_mise_en_place(0)
        self.piece("a", 2)
        self.retenir()
        self.ajouter()
        ligne = DevisLigne.objects.get()
        avant = ligne.prix_vente_operations
        Gamme.objects.filter(article=ligne.article, origine="decoupe").update(temps_fixe=60)
        from .moteur import calculer_ligne
        calculer_ligne(self.devis, ligne)
        ligne.refresh_from_db()
        self.assertEqual(ligne.prix_vente_operations, avant)  # le réglage ne se compte plus « par pièce » sur l'étape

    def test_apercu_du_panneau_affiche_la_mise_en_place(self):
        self.avec_mise_en_place(10)
        self.piece("a", 2)
        html = self.client.post(self.url + "imbrication/", data="{}", content_type="application/json").json()["html"]
        self.assertIn("Mise en place machine", html)
        self.assertIn("estimé : tôle non retenue", html)
        self.retenir()
        html = self.client.post(self.url + "imbrication/", data="{}", content_type="application/json").json()["html"]
        self.assertNotIn("estimé : tôle non retenue", html)
