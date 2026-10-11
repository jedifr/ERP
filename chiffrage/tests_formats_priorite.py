"""Formats de tôle : restriction par matière, niveaux de priorité, calcul des niveaux suivants selon le taux de chutes."""

from django.test import TestCase

from decoupe.models import FormatTole, ReglageImbrication
from decoupe.services import imbrication_devis as imb
from decoupe.services.devis_pieces import pieces_du_devis
from technique.models import FamilleMatiere, Matiere

from . import tests_pieces as base


class FormatsParNiveauxTests(TestCase):
    setUp = base.ChiffrageDevisTests.setUp
    piece = base.ChiffrageDevisTests.piece

    def preparer(self, priorites):
        """Attribue les priorités données aux formats actifs (dans l'ordre de leur taille) et retourne (groupe, formats)."""
        tous = list(FormatTole.objects.filter(actif=True).order_by("-longueur_mm", "-largeur_mm"))
        # Les formats qui tiennent dans la laser reçoivent les priorités demandées, les autres la dernière.
        formats = imb.formats_compatibles("laser", tous) + [f for f in tous if f not in imb.formats_compatibles("laser", tous)]
        for i, f in enumerate(formats):
            f.priorite = priorites[i] if i < len(priorites) and f in imb.formats_compatibles("laser", tous) else 3
            f.save()
        self.piece("a", 3)
        groupes, _ = imb.grouper(pieces_du_devis(self.devis))
        return groupes[0], imb.formats_actifs()

    def calculer(self, groupe, formats, **kw):
        return imb.comparer_par_paliers(groupe, formats, 5.0, 0.0, None, **kw)

    def noms(self, lignes):
        return {f.pk for f, _, _ in lignes}

    def test_restriction_par_famille_et_nuance(self):
        fam = FamilleMatiere.objects.create(nom="Inox test")
        inox = Matiere.objects.create(nom="304 test", densite=7.9, famille=fam)
        libre = FormatTole.objects.create(largeur_mm=1011, longueur_mm=2022)
        par_famille = FormatTole.objects.create(largeur_mm=1261, longueur_mm=2522)
        par_famille.familles.add(fam)
        par_nuance = FormatTole.objects.create(largeur_mm=1511, longueur_mm=3522)
        par_nuance.matieres.add(self.matiere)  # S235
        for f in (libre, par_famille, par_nuance):
            f.refresh_from_db()
        self.assertTrue(libre.convient_a(inox) and libre.convient_a(self.matiere))
        self.assertTrue(par_famille.convient_a(inox))
        self.assertFalse(par_famille.convient_a(self.matiere))
        self.assertTrue(par_nuance.convient_a(self.matiere))
        self.assertFalse(par_nuance.convient_a(inox))

    def test_un_niveau_suffit_quand_les_chutes_sont_sous_le_seuil(self):
        groupe, formats = self.preparer([1, 2, 2, 3, 3, 3])
        _, _, non_calcules, message = self.calculer(groupe, formats, seuil=100.0)  # seuil jamais dépassé
        niveau1 = {f.pk for f in formats if f.priorite == 1}
        lignes, _, _, _ = self.calculer(groupe, formats, seuil=100.0)
        self.assertEqual(self.noms(lignes), niveau1)
        self.assertEqual({f.pk for f in non_calcules}, {f.pk for f in formats} - niveau1)
        self.assertEqual(message, "")

    def test_niveau_suivant_calcule_si_le_seuil_est_depasse(self):
        groupe, formats = self.preparer([1, 2, 2, 3, 3, 3])
        lignes, meilleur, non_calcules, message = self.calculer(groupe, formats, seuil=-1.0)  # toujours dépassé : on descend de niveau en niveau
        self.assertEqual(self.noms(lignes), {f.pk for f in formats})
        self.assertEqual(non_calcules, [])
        self.assertIn("formats suivants calculés aussi", message)
        self.assertIsNotNone(meilleur)

    def test_calculer_tous_les_formats_sur_demande(self):
        groupe, formats = self.preparer([1, 2, 2, 3, 3, 3])
        lignes, _, non_calcules, _ = self.calculer(groupe, formats, seuil=100.0, tout=True)
        self.assertEqual(self.noms(lignes), {f.pk for f in formats})
        self.assertEqual(non_calcules, [])

    def test_le_format_retenu_est_toujours_calcule(self):
        groupe, formats = self.preparer([1, 2, 2, 3, 3, 3])
        retenu = next(f for f in formats if f.priorite == 3)
        lignes, _, non_calcules, _ = self.calculer(groupe, formats, seuil=100.0, obligatoires={retenu.pk})
        self.assertIn(retenu.pk, self.noms(lignes))
        self.assertNotIn(retenu.pk, {f.pk for f in non_calcules})

    def test_format_exclu_par_la_matiere_jamais_calcule(self):
        groupe, formats = self.preparer([1, 1, 1, 1, 1, 1])
        autre = Matiere.objects.create(nom="Autre nuance", densite=7.0)
        exclu = formats[0]
        exclu.matieres.add(autre)
        formats = imb.formats_actifs()
        lignes, _, non_calcules, _ = self.calculer(groupe, formats, tout=True)
        self.assertNotIn(exclu.pk, self.noms(lignes))
        self.assertNotIn(exclu.pk, {f.pk for f in non_calcules})

    def test_taux_de_chutes_exclut_la_chute_de_bout(self):
        groupe, formats = self.preparer([1, 1, 1, 1, 1, 1])
        lignes, meilleur, _, _ = self.calculer(groupe, formats, tout=True)
        resultat = next(r for f, r, _ in lignes if f.pk == meilleur.pk)
        attendu = (resultat.surface_consommee_mm2 - resultat.surface_pieces_mm2) / resultat.surface_consommee_mm2 * 100
        self.assertAlmostEqual(imb.taux_chutes_pct(resultat), attendu)
        self.assertLess(imb.taux_chutes_pct(resultat), (1 - resultat.surface_pieces_mm2 / resultat.surface_feuilles_mm2) * 100 + 1e-9)

    def test_panneau_affiche_les_formats_non_calcules_et_le_bouton(self):
        self.preparer([1, 2, 2, 3, 3, 3])
        reglage = ReglageImbrication.charger()
        reglage.seuil_chutes_pct = 100
        reglage.save()
        html = self.client.post(self.url + "imbrication/", data="{}", content_type="application/json").json()["html"]
        self.assertIn("Calculer tous les formats", html)
        self.assertIn("non calculé", html)
        tout = self.client.post(self.url + "imbrication/", data='{"choix": {"S235|10|laser": {"tout": "1"}}}', content_type="application/json").json()["html"]
        self.assertNotIn("Calculer tous les formats", tout)
        self.assertNotIn("non calculé", tout)

    def test_aucun_format_pour_la_matiere(self):
        self.preparer([1, 1, 1, 1, 1, 1])
        autre = Matiere.objects.create(nom="Autre nuance 2", densite=7.0)
        for f in FormatTole.objects.all():
            f.matieres.add(autre)
        html = self.client.post(self.url + "imbrication/", data="{}", content_type="application/json").json()["html"]
        self.assertIn("Aucun format de tôle n&#x27;est prévu pour S235", html)

    def test_reglage_unique_et_admin(self):
        self.assertEqual(ReglageImbrication.charger().seuil_chutes_pct, 20)
        self.assertEqual(ReglageImbrication.objects.count(), 1)
        reponse = self.client.get("/admin/decoupe/reglageimbrication/")
        self.assertContains(reponse, "20")
        self.assertEqual(self.client.get("/admin/decoupe/formattole/").status_code, 200)
