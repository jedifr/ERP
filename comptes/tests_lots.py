import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from comptes import lots
from comptes.models import LotModification
from technique.lots_champs import champs_article, champs_gamme, champs_matiere, champs_tarif_poste
from technique.models import Article, Gamme, Matiere, PosteTravail, TarifPoste


def spec(champs, nom):
    return next(c for c in champs if c.nom == nom)


class LotsBase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("lots", "l@example.com", "pass-mot-de-passe-51")
        self.client.force_login(self.user)
        self.matiere = Matiere.objects.create(nom="S235-L", densite=7.85)
        # tôle de 3 mm vendue au kilo : 3 × 7,85 = 23,55 kg/m²
        self.kg = Article.objects.create(reference="T-KG", nature=Article.Nature.MATIERE_PREMIERE, matiere=self.matiere, epaisseur=3, unite_cout="poids", cout_unitaire=Decimal("1.0"))
        self.m2 = Article.objects.create(reference="T-M2", nature=Article.Nature.MATIERE_PREMIERE, matiere=self.matiere, epaisseur=3, unite_cout="surface", cout_unitaire=Decimal("23.55"))
        self.fab = Article.objects.create(reference="PIECE-L", nature=Article.Nature.FABRIQUE)

    def lancer(self, queryset, champs, choix):
        return lots.preparer(Article, queryset, [(spec(champs, nom), v, m, u) for nom, v, m, u in choix])


class ModificationParLotsTests(LotsBase):
    def test_apercu_sans_ecriture_et_cas_ignores(self):
        champs = champs_article()
        lignes = self.lancer(Article.objects.all(), champs, [("cout_unitaire", "10", "pct", None)])
        par_ref = {l["objet"].pk: l for l in lignes}
        self.assertEqual(par_ref["T-KG"]["nouveau"]["cout_unitaire"], Decimal("1.1000"))
        self.assertIn("fabriqué", par_ref["PIECE-L"]["ignore"])
        self.assertEqual(Article.objects.get(pk="T-KG").cout_unitaire, Decimal("1.0"))  # rien n'est écrit

    def test_prix_en_kg_converti_pour_un_article_au_m2(self):
        champs = champs_article()
        lignes = self.lancer(Article.objects.filter(pk="T-M2"), champs, [("cout_unitaire", "1.2", "fixe", "kg")])
        self.assertEqual(lignes[0]["nouveau"]["cout_unitaire"], Decimal("28.2600"))  # 1,20 €/kg × 23,55 kg/m²
        lignes = self.lancer(Article.objects.filter(pk="T-KG"), champs, [("cout_unitaire", "30", "fixe", "m2")])
        self.assertEqual(lignes[0]["nouveau"]["cout_unitaire"], (Decimal("30") / Decimal("23.55")).quantize(Decimal("0.0001")))

    def test_changement_d_unite_conserve_le_prix(self):
        champs = champs_article()
        lignes = self.lancer(Article.objects.filter(pk="T-KG"), champs, [("unite_cout", "surface", "fixe", None)])
        self.assertEqual(lignes[0]["nouveau"], {"unite_cout": "surface", "cout_unitaire": Decimal("23.5500")})
        lignes = self.lancer(Article.objects.filter(pk="T-M2"), champs, [("unite_cout", "poids", "fixe", None)])
        self.assertEqual(lignes[0]["nouveau"], {"unite_cout": "poids", "cout_unitaire": Decimal("1.0000")})

    def test_application_trace_et_annulation(self):
        champs = champs_article()
        lignes = self.lancer(Article.objects.filter(pk__in=["T-KG", "T-M2"]), champs, [("cout_unitaire", "10", "pct", None), ("taux_marge_defaut", "25", "fixe", None)])
        lot, n = lots.appliquer(Article, lignes, self.user, "Test")
        self.assertEqual(n, 2)
        self.assertEqual(Article.objects.get(pk="T-KG").cout_unitaire, Decimal("1.1000"))
        self.assertEqual(Article.objects.get(pk="T-KG").taux_marge_defaut, Decimal("25"))
        self.assertEqual(LotModification.objects.count(), 1)
        # un article modifié depuis n'est pas rétabli, les autres le sont
        Article.objects.filter(pk="T-M2").update(cout_unitaire=Decimal("99"))
        faits, refus = lots.annuler(lot)
        self.assertEqual(faits, 1)
        self.assertTrue(any("T-M2" in r for r in refus))
        self.assertEqual(Article.objects.get(pk="T-KG").cout_unitaire, Decimal("1.0000"))
        self.assertIsNone(Article.objects.get(pk="T-KG").taux_marge_defaut)
        self.assertEqual(Article.objects.get(pk="T-M2").cout_unitaire, Decimal("99"))
        self.assertEqual(lots.annuler(lot)[1], ["lot déjà annulé"])

    def test_stock_tristate_et_matiere(self):
        champs = champs_article()
        lignes = self.lancer(Article.objects.filter(pk="T-KG"), champs, [("gere_en_stock", "non", "fixe", None)])
        lots.appliquer(Article, lignes, self.user, "Stock")
        self.assertFalse(Article.objects.get(pk="T-KG").gere_en_stock)
        lignes = lots.preparer(Matiere, Matiere.objects.filter(pk="S235-L"), [(spec(champs_matiere(), "densite"), "7,9", "fixe", None)])
        lots.appliquer(Matiere, lignes, self.user, "Densité")
        self.assertEqual(Matiere.objects.get(pk="S235-L").densite, 7.9)

    def test_ecran_admin_saisie_apercu_application(self):
        url = "/admin/technique/article/"
        base = {"action": "action_modifier_par_lots", "_selected_action": ["T-KG", "T-M2"]}
        saisie = self.client.post(url, base)
        self.assertContains(saisie, "Prix d&#x27;achat")
        apercu = self.client.post(url, {**base, "lot_phase": "apercu", "c_cout_unitaire": "1", "m_cout_unitaire": "pct", "v_cout_unitaire": "10", "u_cout_unitaire": "natif"})
        self.assertContains(apercu, "Appliquer à 2 objet(s)")
        self.assertEqual(Article.objects.get(pk="T-KG").cout_unitaire, Decimal("1.0"))
        appliquer = self.client.post(url, {**base, "lot_phase": "appliquer", "c_cout_unitaire": "1", "m_cout_unitaire": "pct", "v_cout_unitaire": "10", "u_cout_unitaire": "natif"}, follow=True)
        self.assertContains(appliquer, "2 objet(s) mis à jour")
        self.assertEqual(Article.objects.get(pk="T-KG").cout_unitaire, Decimal("1.1000"))
        lot = LotModification.objects.get()
        self.client.post("/admin/comptes/lotmodification/", {"action": "action_annuler", "_selected_action": [lot.pk]})
        self.assertEqual(Article.objects.get(pk="T-KG").cout_unitaire, Decimal("1.0000"))


class NouvellePeriodeTests(LotsBase):
    def setUp(self):
        super().setUp()
        self.poste = PosteTravail.objects.create(nom="Laser-L", mode_calcul=PosteTravail.ModeCalcul.HORAIRE)
        self.tarif = TarifPoste.objects.create(poste=self.poste, cout_horaire=Decimal("100"), date_debut=datetime.date(2026, 1, 1))

    def test_hausse_avec_effet_a_une_date(self):
        champ = (spec(champs_tarif_poste(), "cout_horaire"), "5", "pct", None)
        lot, n, ignores = lots.appliquer_periode(TarifPoste, TarifPoste.objects.all(), [champ], datetime.date(2026, 6, 1), self.user, "Hausse")
        self.assertEqual(n, 1)
        self.tarif.refresh_from_db()
        self.assertEqual(self.tarif.date_fin, datetime.date(2026, 5, 31))
        nouveau = TarifPoste.objects.exclude(pk=self.tarif.pk).get()
        self.assertEqual((nouveau.cout_horaire, nouveau.date_debut, nouveau.date_fin), (Decimal("105.0000"), datetime.date(2026, 6, 1), None))
        faits, refus = lots.annuler(lot)
        self.assertEqual(TarifPoste.objects.count(), 1)
        self.tarif.refresh_from_db()
        self.assertIsNone(self.tarif.date_fin)

    def test_pas_en_vigueur_a_la_date_et_etape_de_decoupe(self):
        champ = (spec(champs_tarif_poste(), "cout_horaire"), "5", "pct", None)
        _, n, ignores = lots.appliquer_periode(TarifPoste, TarifPoste.objects.all(), [champ], datetime.date(2025, 6, 1), self.user, "Trop tôt")
        self.assertEqual((n, len(ignores)), (0, 1))
        etape_decoupe = Gamme.objects.create(article=self.fab, poste=self.poste, ordre=1, temps_fixe=0, temps_variable=1.0, date_debut=datetime.date(2026, 1, 1), origine="decoupe")
        etape_main = Gamme.objects.create(article=self.fab, poste=self.poste, ordre=2, temps_fixe=0, temps_variable=2.0, date_debut=datetime.date(2026, 1, 1), origine="manuelle")
        champ = (spec(champs_gamme(), "temps_variable"), "50", "pct", None)
        _, n, ignores = lots.appliquer_periode(Gamme, Gamme.objects.all(), [champ], datetime.date(2026, 3, 1), self.user, "Temps")
        self.assertEqual(n, 1)
        self.assertEqual([m for _, m in ignores], ["calculée depuis la pièce"])
        self.assertEqual(Gamme.objects.filter(article=self.fab, date_fin__isnull=True, ordre=2).get().temps_variable, 3.0)
        self.assertEqual(Gamme.objects.get(pk=etape_main.pk).date_fin, datetime.date(2026, 2, 28))  # l'ancienne version est conservée


class FormatsEtCreationsTests(LotsBase):
    def test_formats_de_tole_priorite_actif_et_familles(self):
        from decoupe.admin import FormatToleAdmin
        from decoupe.models import FormatTole
        from django.contrib import admin
        from technique.models import FamilleMatiere

        acier = FamilleMatiere.objects.get_or_create(nom="Acier")[0]
        f = FormatTole.objects.create(largeur_mm=1111, longueur_mm=2222, priorite=1)
        champs = FormatToleAdmin(FormatTole, admin.site).champs_lot
        lignes = lots.preparer(FormatTole, FormatTole.objects.filter(pk=f.pk), [(spec(champs, "priorite"), "2", "fixe", None), (spec(champs, "actif"), "0", "fixe", None), (spec(champs, "familles"), [str(acier.pk)], "fixe", None)])
        lot, n = lots.appliquer(FormatTole, lignes, self.user, "Formats")
        f.refresh_from_db()
        self.assertEqual((f.priorite, f.actif, list(f.familles.all())), (2, False, [acier]))
        lots.annuler(lot)
        f.refresh_from_db()
        self.assertEqual((f.priorite, f.actif, f.familles.count()), (1, True, 0))

    def test_base_matieres_rapide_cree_tout_et_s_annule(self):
        from decoupe.models import ParametreCoupe
        from technique.catalogue_matieres import creer_base
        from technique.models import FamilleMatiere, RegleCreationTole

        inox = FamilleMatiere.objects.get_or_create(nom="Inox-L", defaults={"usinabilite": 82})[0]
        Matiere.objects.create(nom="MODELE-L", densite=7.9, famille=inox)
        ParametreCoupe.objects.create(procede="jet_eau", famille=inox, epaisseur_mm=3, origine="calcule")
        rapport = creer_base(
            [{"nom": "I-NEW", "famille": "Inox-L", "densite": 7.9, "prix": Decimal("4.1")}, {"nom": "S235-L", "famille": "", "densite": 7.85, "prix": None}],
            [3, 4], self.user, unite_cout="poids", creer_regle=True, jet_eau=True,
        )
        self.assertTrue(Matiere.objects.filter(pk="I-NEW", famille=inox).exists())
        tole = Article.objects.get(reference="TOLE-I-NEW-4")
        self.assertEqual((tole.matiere_id, tole.epaisseur, tole.unite_cout, tole.cout_unitaire), ("I-NEW", 4.0, "poids", Decimal("4.1")))
        self.assertTrue(Article.objects.filter(reference="TOLE-I-NEW-3").exists())
        self.assertTrue(ParametreCoupe.objects.filter(procede="jet_eau", famille=inox, epaisseur_mm=4).exists())  # estimé depuis le paramètre de 3 mm
        self.assertTrue(RegleCreationTole.objects.filter(matiere_id="I-NEW").exists())
        self.assertTrue(any("existe déjà" in m for _, m in rapport["ignores"]))  # S235-L 3 mm : tôle T-KG déjà là
        lots.annuler(rapport["lot"])
        self.assertFalse(Matiere.objects.filter(pk="I-NEW").exists())
        self.assertFalse(Article.objects.filter(reference="TOLE-I-NEW-4").exists())
        self.assertTrue(Matiere.objects.filter(pk="S235-L").exists())

    def test_ecran_catalogue(self):
        page = self.client.get("/admin/technique/matiere/catalogue/")
        self.assertContains(page, "Base matières rapide")
        reponse = self.client.post("/admin/technique/matiere/catalogue/", {"n_0": "1", "p_0": "0,95", "epaisseur": ["2", "5"], "unite_cout": "poids", "modele_reference": "TOLE-{matiere}-{epaisseur}", "modele_libelle": "Tôle {matiere} {epaisseur} mm", "creer_regle": "1"})
        self.assertContains(reponse, "créé(s)")
        self.assertEqual(Article.objects.get(reference="TOLE-S235-2").cout_unitaire, Decimal("0.95"))

    def test_import_de_tiers(self):
        from commercial.import_tiers import importer
        from commercial.models import Tiers

        rapport = importer("code;raison_sociale;type;siret;numero_tva\nCLI-A;Alpha SAS;client;;\nFOU-B;Bravo;fournisseur;;FR12345678901\nCLI-A;Doublon;client;;\n;Sans code;client;;", self.user)
        self.assertEqual(rapport["crees"], ["CLI-A", "FOU-B"])
        self.assertEqual(len(rapport["ignores"]), 2)
        self.assertEqual(Tiers.objects.get(pk="FOU-B").type_tiers, "fournisseur")
        lots.annuler(rapport["lot"])
        self.assertFalse(Tiers.objects.filter(pk__in=["CLI-A", "FOU-B"]).exists())
        page = self.client.post("/admin/commercial/tiers/importer/", {"tableau": "code;raison_sociale\nCLI-Z;Zeta"})
        self.assertContains(page, "1 tiers créé(s)")

    def test_gamme_type_et_comptes_d_articles(self):
        from comptabilite.models import ArticleCompteVente, CompteComptable
        from technique.models import GammeType, GammeTypeEtape

        poste = PosteTravail.objects.create(nom="Ebav-L", mode_calcul=PosteTravail.ModeCalcul.HORAIRE)
        gt = GammeType.objects.create(nom="Ébavurage-L")
        GammeTypeEtape.objects.create(gamme_type=gt, ordre=1, poste=poste, temps_fixe=1.0, temps_variable=0.5)
        base = {"action": "action_ajouter_gamme_type", "_selected_action": ["PIECE-L", "T-KG"]}
        self.assertContains(self.client.post("/admin/technique/article/", base), "Gamme type")
        self.client.post("/admin/technique/article/", {**base, "confirmer": "1", "gamme_type": gt.pk}, follow=True)
        self.assertEqual(Gamme.objects.filter(article=self.fab).count(), 1)  # la tôle (non fabriquée) est ignorée
        compte = CompteComptable.objects.create(code="707100", libelle="Ventes test")
        self.client.post("/admin/technique/article/", {"action": "action_affecter_compte", "_selected_action": ["T-KG", "T-M2"], "confirmer": "1", "sens": "vente", "compte": compte.pk}, follow=True)
        self.assertEqual(ArticleCompteVente.objects.filter(compte_vente=compte).count(), 2)
        lot = LotModification.objects.order_by("-date").first()
        lots.annuler(lot)
        self.assertEqual(ArticleCompteVente.objects.count(), 0)
