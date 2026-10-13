import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from chiffrage.models import Devis
from commercial.models import Tiers
from technique.models import ProfileSection

from .models import DebitSciage, OptimisationSciage, PieceProfile
from .services.sciage import Debit, ErreurSciage, calculer_plan, lignes_de, optimiser, svg_piece

# Liste de l'affaire C026-0822 (capture CuttingOptimization) : 12 ensembles de 2 débits par repère, tube 20×20.
EXEMPLE = [("1", 680, 0, 0), ("2", 396, 0, 0), ("3", 226, 45, -45), ("4", 400, 0, 0), ("5", 1230, 0, 0)]


def debits_exemple():
    return [Debit(r, L, g, d) for r, L, g, d in EXEMPLE for _ in range(24)]


class AlgorithmeSciageTests(TestCase):
    def test_exemple_tient_dans_douze_barres_comme_cuttingoptimization(self):
        r = optimiser(debits_exemple(), 6000, 20, lame=3, chute_initiale=20)
        self.assertEqual(r.nb_neuves, 12)
        self.assertEqual(r.nb_debits, 120)
        for b in r.barres:
            self.assertLessEqual(b.fin, 6000 + 1e-6)
            self.assertGreaterEqual(b.places[0].xl0, 20)  # rien dans la chute initiale

    def test_resultat_reproductible(self):
        a = optimiser(debits_exemple(), 6000, 20, 3, 20)
        b = optimiser(debits_exemple(), 6000, 20, 3, 20)
        self.assertEqual([x.signature() for x in a.barres], [x.signature() for x in b.barres])

    def test_onglets_imbriques_tete_beche(self):
        """Deux trapèzes 45°/-45° de 226 mm dans un tube de 20 : le second est retourné et partage la coupe du premier."""
        r = optimiser([Debit("3", 226, 45, -45)] * 2, 6000, 20, lame=3, chute_initiale=0)
        p1, p2 = r.barres[0].places
        self.assertAlmostEqual(p1.tr, p2.tl)  # même coupe
        # sans imbrication il faudrait 2 × 226 ; avec : 226 + 226 − 20 + trait de scie en biais
        self.assertAlmostEqual(r.barres[0].fin, 3 * 2 ** 0.5 + 226 + 3 * 2 ** 0.5 + 226 - 20, places=6)

    def test_debit_trop_long(self):
        with self.assertRaisesMessage(ErreurSciage, "ne tient pas"):
            optimiser([Debit("1", 6100)], 6000, 20)

    def test_coupes_qui_se_rejoignent(self):
        with self.assertRaisesMessage(ErreurSciage, "se rejoignent"):
            optimiser([Debit("1", 30, 45, -45)], 6000, 20)

    def test_chutes_du_stock_utilisees_avant_les_barres_neuves(self):
        r = optimiser([Debit("1", 1000)] * 4, 6000, 20, lame=3, chute_initiale=0, chutes_stock=[2500, 2100])
        self.assertEqual(r.nb_neuves, 0)
        self.assertEqual(sorted(b.longueur for b in r.barres), [2100, 2500])

    def test_sequence_et_groupes(self):
        r = optimiser([Debit("5", 1230)] * 9, 6000, 20, lame=3, chute_initiale=20)
        self.assertEqual([g.nombre for g in r.groupes], [2, 1])  # deux barres identiques regroupées, la barre entamée à part
        self.assertEqual(r.barres[0].sequence(), "•(5)4x1 230[0°↔0°]")

    def test_croquis(self):
        svg = svg_piece(226, 45, -45, "#7ee07e")
        self.assertIn("<polygon", svg)
        self.assertIn("45°", svg)
        self.assertIn(">226<", svg)


class OptimisationSciageTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("scie", "s@example.com", "pass-mot-de-passe-71")
        self.client.force_login(self.user)
        self.section = ProfileSection.objects.create(
            famille="tube_carre", designation="Tube test 20×20×2", dimensions={"c": 20, "e": 2}, masse_lineique=1.1, longueur_barre_mm=6000,
        )
        self.opt = OptimisationSciage.objects.create(intitule="Garde-corps", quantite_ensembles=12)
        for i, (r, L, g, d) in enumerate(EXEMPLE):
            DebitSciage.objects.create(optimisation=self.opt, repere=r, longueur_mm=L, quantite=2, angle_gauche_deg=g, angle_droit_deg=d,
                                       section=self.section, ordre=i)

    def test_numero_attribue_par_annee(self):
        self.assertEqual(self.opt.numero, f"OS-{self.opt.date.year}-0001")
        autre = OptimisationSciage.objects.create(intitule="Autre")
        self.assertEqual(autre.numero, f"OS-{self.opt.date.year}-0002")

    def test_plan_du_projet(self):
        plan = calculer_plan(self.opt, lignes_de(self.opt))
        self.assertEqual(plan.nb_neuves, 12)
        self.assertEqual(plan.nb_debits, 120)
        self.assertEqual(plan.sections[0].lignes[2]["quantite"], 24)
        self.assertIsNone(plan.cout)  # pas d'article d'achat sur la section

    def test_fiche_affiche_le_plan(self):
        page = self.client.get(reverse("admin:decoupe_optimisationsciage_change", args=[self.opt.pk]))
        self.assertContains(page, "Plan de coupe")
        self.assertContains(page, "•(5)4x1 230[0°↔0°]")

    def test_apercu_en_direct(self):
        donnees = {
            "quantite_ensembles": "1", "longueur_barre": "imposee", "longueur_barre_mm": "6000", "largeur_lame_mm": "3", "chute_initiale_mm": "20",
            "chute_reutilisable_mm": "300", "debits-TOTAL_FORMS": "2",
            "debits-0-repere": "A", "debits-0-longueur_mm": "2000", "debits-0-quantite": "3", "debits-0-section": str(self.section.pk),
            "debits-0-angle_gauche_deg": "0", "debits-0-angle_droit_deg": "0", "debits-0-couleur": "#ff0000",
            "debits-1-repere": "B", "debits-1-longueur_mm": "500", "debits-1-quantite": "1", "debits-1-section": str(self.section.pk),
            "debits-1-DELETE": "on",
        }
        r = self.client.post(reverse("admin:decoupe_optimisationsciage_previsualiser"), donnees)
        self.assertEqual(r.status_code, 200)
        json = r.json()
        self.assertIn("•(A)2x2 000[0°↔0°]", json["html"])
        self.assertNotIn("(B)", json["html"])
        self.assertEqual(list(json["croquis"]), ["0"])

    def test_pdf_liste_de_debits(self):
        r = self.client.get(reverse("admin:decoupe_optimisationsciage_action_pdf", args=[self.opt.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "application/pdf")
        self.assertTrue(r.content.startswith(b"%PDF"))

    def test_import_des_debits_d_un_devis(self):
        tiers = Tiers.objects.create(code="CLI-SCIE", raison_sociale="Client scie", type_tiers=Tiers.TypeTiers.CLIENT)
        devis = Devis.objects.create(numero="DEV-SCIE", client=tiers, date_creation=datetime.date(2026, 10, 12))
        PieceProfile.objects.create(devis=devis, section=self.section, nom="Traverse", longueur_mm=800, coupe_a_deg=45, coupe_b_deg=90, quantite=4)
        url = reverse("admin:decoupe_optimisationsciage_action_importer", args=[self.opt.pk])
        page = self.client.get(url, {"source": "devis", "valeur": "DEV-SCIE"})
        self.assertContains(page, "Traverse")
        self.client.post(url, {"source": "devis", "valeur": "DEV-SCIE", "importer": "1", "choisi-0": "on", "quantite-0": "6"})
        d = self.opt.debits.get(longueur_mm=800)
        self.assertEqual((d.quantite, d.angle_gauche_deg, d.angle_droit_deg, d.repere), (6, 45, 0, "6"))

    def test_nouvelle_ligne_grise_recoit_une_couleur_libre(self):
        """Sans le script de la fiche, une ligne ajoutée garde le gris par défaut : l'enregistrement lui donne la première couleur libre."""
        url = reverse("admin:decoupe_optimisationsciage_change", args=[self.opt.pk])
        donnees = {
            "intitule": self.opt.intitule, "date": "2026-10-13", "quantite_ensembles": "12", "longueur_barre": "imposee", "longueur_barre_mm": "6000",
            "largeur_lame_mm": "3", "chute_initiale_mm": "20", "chute_reutilisable_mm": "300", "chutes_disponibles": "",
            "debits-TOTAL_FORMS": "6", "debits-INITIAL_FORMS": "5", "debits-MIN_NUM_FORMS": "0", "debits-MAX_NUM_FORMS": "1000",
        }
        for i, d in enumerate(self.opt.debits.all()):
            donnees.update({f"debits-{i}-id": d.pk, f"debits-{i}-optimisation": self.opt.pk, f"debits-{i}-repere": d.repere, f"debits-{i}-couleur": "#d4d4d4",
                            f"debits-{i}-longueur_mm": d.longueur_mm, f"debits-{i}-quantite": d.quantite, f"debits-{i}-angle_gauche_deg": d.angle_gauche_deg,
                            f"debits-{i}-angle_droit_deg": d.angle_droit_deg, f"debits-{i}-section": self.section.pk})
        donnees.update({"debits-5-repere": "6", "debits-5-couleur": "#d4d4d4", "debits-5-longueur_mm": "500", "debits-5-quantite": "1",
                        "debits-5-angle_gauche_deg": "0", "debits-5-angle_droit_deg": "0", "debits-5-section": self.section.pk})
        r = self.client.post(url, donnees)
        self.assertEqual(r.status_code, 302, r.content[:2000])
        self.assertEqual(self.opt.debits.get(repere="6").couleur, "#fdf26b")
