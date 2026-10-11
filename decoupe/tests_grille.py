"""Paramètres de coupe en grille (matière × épaisseur) : contenu, états, affectation d'un poste par ligne / colonne / tout."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from technique.models import PosteTravail

from . import grille_parametres as gp
from .models import ParametreCoupe


class GrilleParametresTests(TestCase):
    def setUp(self):
        from technique.models import FamilleMatiere

        self.client.force_login(get_user_model().objects.create_superuser("adm-grille", "g@x.fr", "pass-mot-de-passe-20"))
        ParametreCoupe.objects.all().delete()
        self.acier = FamilleMatiere.objects.create(nom="Acier GR")
        self.inox = FamilleMatiere.objects.create(nom="Inox GR")
        self.poste = PosteTravail.objects.create(nom="Laser GR", mode_calcul="horaire", temps_mise_en_place_min=10)
        self.autre = PosteTravail.objects.create(nom="Jet GR", mode_calcul="horaire")
        creer = lambda famille, e, **kw: ParametreCoupe.objects.create(procede="laser", gaz="o2", famille=famille, epaisseur_mm=e, **kw)
        self.p1 = creer(self.acier, 3, poste=self.poste, vitesse_coupe_production_m_min=4.1)
        self.p2 = creer(self.acier, 5)  # sans poste
        self.p3 = creer(self.inox, 3, poste=self.poste, temps_mise_en_place_min=25)
        self.url = reverse("admin:decoupe_parametrecoupe_action_grille")

    def cases(self, vue="poste", **kw):
        d = gp.contexte_grille("laser", "o2", vue)
        return d, {(l["nom"], c["libelle"]): case for l in d["lignes"] for c, case in zip(d["colonnes"], l["cases"])}

    def test_structure_et_etats(self):
        d, cases = self.cases()
        self.assertEqual([c["libelle"] for c in d["colonnes"]], ["3", "5"])
        self.assertEqual([l["nom"] for l in d["lignes"]], ["Acier GR", "Inox GR"])
        self.assertEqual(cases[("Acier GR", "3")]["texte"], "Laser GR")
        self.assertEqual(cases[("Acier GR", "3")]["etat"], "ok")
        self.assertEqual((cases[("Acier GR", "5")]["texte"], cases[("Acier GR", "5")]["etat"]), ("sans poste", "ko"))
        self.assertTrue(cases[("Inox GR", "5")]["vide"])  # pas de paramètre inox 5 mm : case à créer
        self.assertEqual(d["compte"], {"total": 3, "sans_poste": 1, "estimes": 0})

    def test_vues_vitesse_et_mise_en_place(self):
        _, v = self.cases("vitesse")
        self.assertEqual(v[("Acier GR", "3")]["texte"], "4.1 m/min")
        _, m = self.cases("mise_en_place")
        self.assertEqual(m[("Acier GR", "3")]["texte"], "10 min (poste)")  # hérite du poste
        self.assertEqual(m[("Inox GR", "3")]["texte"], "25 min")  # exception du paramètre

    def test_page_et_lien_de_creation(self):
        page = self.client.get(self.url + "?procede=laser&gaz=o2")
        self.assertContains(page, "Acier GR")
        self.assertContains(page, "Affecter un poste à")
        self.assertContains(page, "procede=laser&amp;gaz=o2&amp;famille=")
        self.assertContains(page, "epaisseur_mm=5")

    def test_affectation_par_ligne_colonne_et_tout(self):
        def poster(cible, poste):
            return self.client.post(self.url, {"procede": "laser", "gaz": "o2", "vue": "poste", "cible": cible, "poste": poste.pk if poste else ""})

        self.assertEqual(poster(f"ligne:f{self.acier.pk}", self.autre).status_code, 302)
        for p in (self.p1, self.p2, self.p3):
            p.refresh_from_db()
        self.assertEqual((self.p1.poste, self.p2.poste, self.p3.poste), (self.autre, self.autre, self.poste))  # seule la ligne Acier change
        poster("colonne:3", self.poste)
        for p in (self.p1, self.p2, self.p3):
            p.refresh_from_db()
        self.assertEqual((self.p1.poste, self.p2.poste, self.p3.poste), (self.poste, self.autre, self.poste))
        poster("tout", None)
        self.assertEqual(ParametreCoupe.objects.filter(poste__isnull=False).count(), 0)

    def test_selection_inconnue_et_droits(self):
        r = self.client.post(self.url, {"procede": "laser", "cible": "n'importe quoi"}, follow=True)
        self.assertContains(r, "Sélection inconnue")
        lecteur = get_user_model().objects.create_user("lecteur-gr", password="pass-mot-de-passe-20", is_staff=True)
        from django.contrib.auth.models import Permission

        lecteur.user_permissions.add(Permission.objects.get(content_type__app_label="decoupe", codename="view_parametrecoupe"))
        self.client.force_login(lecteur)
        page = self.client.get(self.url)
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "Affecter un poste à")  # lecture seule : pas de formulaire d'affectation
        self.assertEqual(self.client.post(self.url, {"cible": "tout"}).status_code, 403)

    def test_bouton_vue_en_grille_sur_la_liste(self):
        self.assertContains(self.client.get(reverse("admin:decoupe_parametrecoupe_changelist")), "Vue en grille")


class GrilleCreationDirecteTests(TestCase):
    """Cliquer « + » (ou « Créer les manquants ») crée le paramètre jet d'eau tout de suite : copie du plus proche, vitesses estimées, poste choisi."""

    def setUp(self):
        from technique.models import FamilleMatiere

        self.client.force_login(get_user_model().objects.create_superuser("adm-grille2", "g2@x.fr", "pass-mot-de-passe-21"))
        ParametreCoupe.objects.all().delete()
        self.acier = FamilleMatiere.objects.create(nom="Acier JE", usinabilite=87.6)
        self.sans_usinabilite = FamilleMatiere.objects.create(nom="Marbre JE")
        self.poste = PosteTravail.objects.create(nom="Jet JE", mode_calcul="horaire")
        self.autre = PosteTravail.objects.create(nom="Jet JE 2", mode_calcul="horaire")
        creer = lambda famille, e, **kw: ParametreCoupe.objects.create(procede="jet_eau", famille=famille, epaisseur_mm=e, poste=self.poste, **kw)
        self.modele = creer(self.acier, 3, percage_stationnaire_hp_s=3.0)
        creer(self.acier, 10)
        creer(self.sans_usinabilite, 3)
        self.url = reverse("admin:decoupe_parametrecoupe_action_grille")

    def poster(self, cible, poste=None, procede="jet_eau"):
        return self.client.post(self.url, {"procede": procede, "gaz": "", "vue": "poste", "creer": "1", "cible": cible, "poste": poste.pk if poste else ""}, follow=True)

    def test_page_propose_la_creation_directe(self):
        page = self.client.get(self.url + "?procede=jet_eau")
        self.assertContains(page, 'data-cible="cell:f%d:10"' % self.sans_usinabilite.pk)
        self.assertContains(page, "Créer les manquants")
        self.assertContains(page, 'id="grille-creer"')

    def test_clic_sur_une_case_vide_cree_le_parametre_avec_le_poste_choisi(self):
        r = self.poster(f"cell:f{self.acier.pk}:5", self.autre)
        self.assertContains(r, "1 paramètre(s) créé(s)")
        p = ParametreCoupe.objects.get(procede="jet_eau", famille=self.acier, epaisseur_mm=5)
        self.assertEqual((p.poste, p.origine), (self.autre, "calcule"))
        self.assertTrue(p.vitesses.exists())  # vitesses estimées d'après l'usinabilité
        self.assertEqual(p.percage_stationnaire_hp_s, 5.0)  # copié du modèle de 3 mm, proportionnel à l'épaisseur
        r = self.poster(f"cell:f{self.acier.pk}:5", self.autre)  # déjà créé : rien de plus
        self.assertEqual(ParametreCoupe.objects.filter(famille=self.acier, epaisseur_mm=5).count(), 1)

    def test_creer_les_manquants_d_une_colonne_signale_ce_qui_ne_peut_pas_l_etre(self):
        r = self.poster("colonne:10", None)
        self.assertContains(r, "Marbre JE 10 mm")  # pas d'usinabilité : refusé avec le motif
        self.assertFalse(ParametreCoupe.objects.filter(famille=self.sans_usinabilite, epaisseur_mm=10).exists())
        r = self.poster("tout", self.poste)
        self.assertTrue(ParametreCoupe.objects.filter(famille=self.acier, epaisseur_mm=3).exists())

    def test_laser_refuse_et_lot_annulable(self):
        from comptes import lots
        from comptes.models import LotModification

        r = self.poster(f"cell:f{self.acier.pk}:5", self.autre, procede="laser")
        self.assertContains(r, "tableau du constructeur")
        self.assertFalse(ParametreCoupe.objects.filter(famille=self.acier, epaisseur_mm=5).exists())
        self.poster(f"cell:f{self.acier.pk}:5", self.autre)
        lots.annuler(LotModification.objects.get())
        self.assertFalse(ParametreCoupe.objects.filter(famille=self.acier, epaisseur_mm=5).exists())
