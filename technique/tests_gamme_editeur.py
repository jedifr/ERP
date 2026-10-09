"""Éditeur d'opérations de fabrication : gamme de l'article, historique par date, gammes types, droits."""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from . import gamme_editeur as ge
from .models import Article, Gamme, GammeType, GammeTypeEtape, PosteTravail, TarifPoste

HIER = datetime.date.today() - datetime.timedelta(days=1)


class GammeEditeurTests(TestCase):
    def setUp(self):
        self.article = Article.objects.create(reference="PIECE-GE", nature=Article.Nature.FABRIQUE, taux_marge_defaut=20)
        self.laser = PosteTravail.objects.create(nom="Laser GE", mode_calcul="horaire")
        self.pliage = PosteTravail.objects.create(nom="Pliage GE", mode_calcul="horaire")
        self.traitement = PosteTravail.objects.create(nom="Traitement GE", mode_calcul="forfaitaire")
        for poste in (self.laser, self.pliage):
            TarifPoste.objects.create(poste=poste, cout_horaire=60, date_debut=datetime.date(2020, 1, 1))
        self.decoupe = Gamme.objects.create(article=self.article, poste=self.laser, ordre=1, temps_fixe=0, temps_variable=1.5,
                                            date_debut=HIER, origine="decoupe")

    def _lignes(self, *extras):
        base = [{"id": self.decoupe.pk, "poste": self.laser.pk, "temps_fixe": 0, "temps_variable": 1.5}]
        return base + list(extras)

    def test_ajout_d_operations_et_ordre(self):
        ge.enregistrer(self.article, self._lignes(
            {"id": None, "poste": self.pliage.pk, "temps_fixe": "20", "temps_variable": "3,5"},
            {"id": None, "poste": self.traitement.pk, "cout_forfaitaire": "3,2"},
        ))
        etapes = ge.etapes_actives(self.article)
        self.assertEqual([(e.ordre, e.poste.nom) for e in etapes], [(1, "Laser GE"), (2, "Pliage GE"), (3, "Traitement GE")])
        self.assertEqual(etapes[1].temps_variable, 3.5)
        self.assertEqual(float(etapes[2].cout_forfaitaire), 3.2)
        self.assertEqual(etapes[1].origine, "manuelle")

    def test_la_decoupe_garde_son_origine_et_son_temps(self):
        ge.enregistrer(self.article, [{"id": self.decoupe.pk, "poste": self.pliage.pk, "temps_fixe": "5", "temps_variable": "99"}])
        etape = ge.etapes_actives(self.article)[0]
        self.assertEqual(etape.origine, "decoupe")
        self.assertEqual(etape.poste_id, self.laser.pk)  # le poste suit le paramètre de coupe
        self.assertEqual(etape.temps_variable, 1.5)
        self.assertEqual(etape.temps_fixe, 5)

    def test_modifier_historise_et_ne_touche_pas_aux_anciens_calculs(self):
        ge.enregistrer(self.article, self._lignes({"id": None, "poste": self.pliage.pk, "temps_fixe": 10, "temps_variable": 2}))
        # une étape créée aujourd'hui est modifiée en place ; on la fait remonter dans le passé pour tester l'historique
        pliage = Gamme.objects.get(poste=self.pliage)
        Gamme.objects.filter(pk=pliage.pk).update(date_debut=HIER)
        ge.enregistrer(self.article, self._lignes({"id": pliage.pk, "poste": self.pliage.pk, "temps_fixe": 10, "temps_variable": 4}))
        anciennes = Gamme.objects.filter(poste=self.pliage).order_by("date_debut")
        self.assertEqual(anciennes.count(), 2)
        self.assertIsNotNone(anciennes[0].date_fin)
        self.assertEqual(ge.etapes_actives(self.article)[1].temps_variable, 4)
        # un devis daté d'hier se calcule encore avec l'ancien temps
        from chiffrage.moteur import gamme_active
        self.assertEqual([e.temps_variable for e in gamme_active(self.article, HIER) if e.poste_id == self.pliage.pk], [2])

    def test_retirer_une_operation(self):
        ge.enregistrer(self.article, self._lignes({"id": None, "poste": self.pliage.pk, "temps_fixe": 1, "temps_variable": 1}))
        pliage = Gamme.objects.get(poste=self.pliage)
        ge.enregistrer(self.article, self._lignes())
        self.assertFalse(Gamme.objects.filter(pk=pliage.pk).exists())  # créée aujourd'hui : supprimée

    def test_decoupe_non_supprimable_et_erreurs_lisibles(self):
        with self.assertRaises(ge.ErreurGamme):
            ge.enregistrer(self.article, [])
        with self.assertRaisesMessage(ge.ErreurGamme, "forfait"):
            ge.enregistrer(self.article, self._lignes({"id": None, "poste": self.traitement.pk}))
        self.assertEqual(Gamme.objects.filter(article=self.article).count(), 1)  # transaction annulée

    def test_gamme_type_ajoute_ses_etapes_a_la_suite(self):
        gt = GammeType.objects.create(nom="Pliage + traitement")
        GammeTypeEtape.objects.create(gamme_type=gt, ordre=1, poste=self.pliage, temps_fixe=15, temps_variable=2)
        GammeTypeEtape.objects.create(gamme_type=gt, ordre=2, poste=self.traitement, cout_forfaitaire=4)
        ge.ajouter_gamme_type(self.article, gt)
        self.assertEqual([(e.ordre, e.poste.nom) for e in ge.etapes_actives(self.article)], [(1, "Laser GE"), (2, "Pliage GE"), (3, "Traitement GE")])
        GammeTypeEtape.objects.filter(gamme_type=gt).update(temps_variable=9)  # modifier le type ne change pas la gamme copiée
        self.assertEqual(ge.etapes_actives(self.article)[1].temps_variable, 2)

    def test_reprendre_la_gamme_d_une_autre_piece(self):
        autre = Article.objects.create(reference="AUTRE-GE", nature=Article.Nature.FABRIQUE, taux_marge_defaut=20)
        Gamme.objects.create(article=autre, poste=self.laser, ordre=1, temps_fixe=0, temps_variable=3, date_debut=HIER, origine="decoupe")
        with self.assertRaises(ge.ErreurGamme):
            ge.reprendre_gamme(self.article, autre)  # rien de saisi à la main à reprendre
        Gamme.objects.create(article=autre, poste=self.pliage, ordre=2, temps_fixe=5, temps_variable=1, date_debut=HIER)
        ge.reprendre_gamme(self.article, autre)
        self.assertEqual([e.poste.nom for e in ge.etapes_actives(self.article)], ["Laser GE", "Pliage GE"])

    def test_etat_calcule_les_couts(self):
        ge.enregistrer(self.article, self._lignes({"id": None, "poste": self.pliage.pk, "temps_fixe": 30, "temps_variable": 6}))
        e = ge.etat(self.article, quantite=3)
        self.assertEqual(len(e["etapes"]), 2)
        # pliage : (30 + 6×3) / 60 × 60 € = 48 € pour 3 pièces ; découpe : 1,5×3/60×60 = 4,5 €
        self.assertAlmostEqual(e["etapes"][1]["cout_total"], 48.0)
        self.assertAlmostEqual(e["total_cout"], 52.5)


class GammeEditeurVueTests(TestCase):
    def setUp(self):
        self.article = Article.objects.create(reference="PIECE-GV", nature=Article.Nature.FABRIQUE, taux_marge_defaut=20)
        self.poste = PosteTravail.objects.create(nom="Poste GV", mode_calcul="horaire")
        TarifPoste.objects.create(poste=self.poste, cout_horaire=50, date_debut=datetime.date(2020, 1, 1))
        self.url = reverse("gamme_editeur", args=[self.article.pk])

    def test_lecture_et_ecriture_pour_un_administrateur(self):
        self.client.force_login(get_user_model().objects.create_superuser("adm-ge", "a@x.fr", "pass-mot-de-passe-20"))
        self.assertEqual(self.client.get(self.url).json()["etapes"], [])
        reponse = self.client.post(self.url, {"action": "enregistrer", "etapes": [{"id": None, "poste": self.poste.pk, "temps_fixe": 5, "temps_variable": 2}]}, content_type="application/json")
        self.assertEqual(len(reponse.json()["etapes"]), 1)
        erreur = self.client.post(self.url, {"action": "enregistrer", "etapes": [{"id": None, "poste": self.poste.pk}]}, content_type="application/json")
        self.assertEqual(erreur.status_code, 400)
        self.assertIn("temps", erreur.json()["detail"])

    def test_sans_droit_interdit(self):
        self.client.force_login(get_user_model().objects.create_user("lambda-ge", password="pass-mot-de-passe-20", is_staff=True))
        self.assertEqual(self.client.get(self.url).status_code, 403)


class PosteTarifsInlineTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("adm-pt", "a@x.fr", "pass-mot-de-passe-20"))
        self.poste = PosteTravail.objects.create(nom="Poste PT", mode_calcul="horaire")

    def test_tarifs_dans_la_fiche_du_poste(self):
        reponse = self.client.get(reverse("admin:technique_postetravail_change", args=[self.poste.pk]))
        self.assertContains(reponse, "tarifs-TOTAL_FORMS")

    def test_colonne_cout_horaire_actuel(self):
        liste = reverse("admin:technique_postetravail_changelist")
        self.assertContains(self.client.get(liste), "aucun tarif")
        TarifPoste.objects.create(poste=self.poste, cout_horaire=72, date_debut=datetime.date(2020, 1, 1))
        self.assertContains(self.client.get(liste), "72")

    def test_poste_forfaitaire_sans_tarif_n_est_pas_signale(self):
        from comptes.a_completer import _postes_sans_tarif

        PosteTravail.objects.create(nom="Sous-traitance PT", mode_calcul="forfaitaire")
        total, elements = _postes_sans_tarif()
        self.assertEqual([e[0] for e in elements], ["Poste PT"])


class ConstructeurEditeurTests(TestCase):
    def test_page_constructeur_utilise_l_editeur_partage(self):
        from chiffrage.models import Devis
        from commercial.models import Tiers

        self.client.force_login(get_user_model().objects.create_superuser("adm-cb", "a@x.fr", "pass-mot-de-passe-20"))
        client = Tiers.objects.create(code="CLI-CB", raison_sociale="Client CB")
        devis = Devis.objects.create(numero="DEV-CB-1", client=client, date_creation=datetime.date(2026, 10, 1))
        reponse = self.client.get(reverse("admin:chiffrage_devis_builder", args=[devis.pk]))
        self.assertContains(reponse, 'id="gamme-editeur"')
        self.assertContains(reponse, reverse("gamme_editeur_options"))
        self.assertNotContains(reponse, "template-gamme-row")
        options = self.client.get(reverse("gamme_editeur_options")).json()
        self.assertIn("postes", options)
        self.assertIn("types", options)

    def test_creation_d_article_avec_temps_a_zero(self):
        """Un temps de réglage à 0 est une valeur valide (et non « absente ») pour un poste horaire."""
        from chiffrage.builder_views import _creer_article_depuis_payload

        poste = PosteTravail.objects.create(nom="Poste CB", mode_calcul="horaire")
        TarifPoste.objects.create(poste=poste, cout_horaire=50, date_debut=datetime.date(2020, 1, 1))
        composant = Article.objects.create(reference="MAT-CB", nature=Article.Nature.MATIERE_PREMIERE, taux_marge_defaut=10, cout_unitaire=5, unite_cout="piece")
        article = _creer_article_depuis_payload({
            "reference": "NEW-CB", "taux_marge_defaut": 20,
            "composants": [{"article_composant": composant.pk, "quantite": 1}],
            "etapes": [{"poste": poste.pk, "ordre": 1, "temps_fixe": 0, "temps_variable": 2.5, "date_debut": "2026-10-01"}],
        })
        etape = article.gamme_etapes.get()
        self.assertEqual((etape.temps_fixe, etape.temps_variable), (0, 2.5))
