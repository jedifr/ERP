import json
import re
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from chiffrage.documents import (
    DocumentError,
    generer_pdf_ar_commande,
    generer_pdf_bon_preparation,
    generer_pdf_devis,
    generer_pdf_ordres_fabrication,
)
from chiffrage.production import creer_ordres_fabrication
from chiffrage.tests import _FixtureOrdresCommande

from . import contextes, moteur
from .defaults import blocs_editeur, modele_par_defaut
from .models import ModeleDocument
from .rendu import rendre

T = ModeleDocument.Type


class MoteurTests(TestCase):
    def rendre(self, html, contexte=None):
        return moteur.appliquer(html, contexte or {})[0]

    def test_variables_echappees_et_blocs_html(self):
        ctx = {"nom": "<b>Dupont & Fils</b>", "bloc_html": "a<br>b"}
        self.assertEqual(self.rendre("<p>{{ nom }}</p><p>{{ bloc_html }}</p>", ctx), "<p>&lt;b&gt;Dupont &amp; Fils&lt;/b&gt;</p><p>a<br>b</p>")

    def test_variable_dans_un_attribut_est_echappee_meme_pour_un_bloc_html(self):
        html = self.rendre('<a title="{{ x_html }}">t</a>', {"x_html": '"><script>alert(1)</script>'})
        self.assertNotIn("<script", html)

    def test_chemins_imbriques_listes_et_inconnus(self):
        ctx = {"a": {"b": ["x", "y"]}}
        html, inconnues = moteur.appliquer("{{ a.b.1 }}|{{ a.c }}|{{ z }}", ctx)
        self.assertEqual(html, "y||")
        self.assertEqual(inconnues, ["a.c", "z"])

    def test_afficher_si_et_negation(self):
        ctx = {"plein": "oui", "vide": ""}
        html = self.rendre('<p data-if="plein">A</p><p data-if="vide">B</p><p data-if="!vide">C</p><p data-if="!plein">D</p>', ctx)
        self.assertEqual(html, "<p>A</p><p>C</p>")

    def test_repeter_une_ligne_de_tableau_html_valide_conserve(self):
        ctx = {"lignes": [{"n": "1"}, {"n": "2"}, {"n": "3"}]}
        html = self.rendre('<table><tbody><tr data-repeat="lignes" data-as="l" class="x"><td>{{ l.n }}</td><td>{{ rang }}</td></tr></tbody></table>', ctx)
        self.assertEqual(html.count("<tr"), 3)
        self.assertEqual(re.findall(r"<td>(\d)</td><td>(\d)</td>", html), [("1", "1"), ("2", "2"), ("3", "3")])
        self.assertNotIn("data-repeat", html)

    def test_repetition_imbriquee_et_liste_vide(self):
        ctx = {"t": [{"taux": "20 %"}], "vide": []}
        html = self.rendre('<ul><li data-repeat="t" data-as="x">{{ x.taux }}</li></ul><ul><li data-repeat="vide">z</li></ul>', ctx)
        self.assertEqual(html, "<ul><li>20 %</li></ul><ul></ul>")

    def test_rien_d_executable(self):
        ctx = {"a": "x"}
        html = self.rendre(
            '<p onclick="mal()">{% for i in range(9) %}X{% endfor %}{{ a.__class__ }}{{ a|upper }}</p>'
            '<script>alert(1)</script><iframe src="http://x"></iframe><object data="x"></object><form action="/"></form>', ctx,
        )
        self.assertNotIn("onclick", html)
        for interdit in ("<script", "<iframe", "<object", "<form", "XXXX"):
            self.assertNotIn(interdit, html)
        self.assertIn("{% for", html)  # affiché tel quel, jamais interprété

    def test_data_src_remplace_le_src_de_substitution_de_l_editeur(self):
        html = self.rendre('<img data-src="{{ logo }}" alt="L" src="data:image/svg+xml;base64,AAA">', {"logo": "data:image/png;base64,XYZ"})
        self.assertEqual(html, '<img src="data:image/png;base64,XYZ" alt="L">')

    def test_html_mal_forme_et_trop_gros(self):
        self.assertIn("ok", self.rendre("<div><p>ok</div></span>"))
        with self.assertRaises(moteur.ErreurModele):
            moteur.appliquer("x" * 2_000_001, {})

    def test_aucune_ressource_externe_n_est_chargee(self):
        """Fichiers locaux et réseau sont bloqués : seul `data:` est autorisé."""
        from urllib import request

        html = '<img src="file:///etc/hostname"><img src="http://exemple.invalid/x.png"><div style="background:url(file:///etc/passwd)">x</div>'
        css = '@import url("http://exemple.invalid/a.css"); body { background: url(http://exemple.invalid/b.png); }'
        with mock.patch.object(request.OpenerDirector, "open", side_effect=AssertionError("ressource externe chargée")):
            pdf = moteur.ecrire([moteur.vers_pdf(self.rendre(html), css, "")])
        self.assertTrue(pdf.startswith(b"%PDF"))


class ModelesParDefautTests(TestCase):
    def test_chaque_modele_par_defaut_se_rend_sans_variable_inconnue(self):
        for type_document in T.values:
            html, css = modele_par_defaut(type_document)
            document, inconnues = rendre(html, css, contextes.contexte_exemple(type_document))
            self.assertEqual(inconnues, [], type_document)
            self.assertTrue(moteur.ecrire([document]).startswith(b"%PDF"), type_document)

    def test_la_palette_de_variables_correspond_aux_donnees(self):
        """Chaque variable proposée dans l'éditeur existe vraiment dans les données du document."""
        alias = {"ligne": "lignes", "composant": "composants", "operation": "operations"}
        for type_document in T.values:
            ctx = contextes.contexte_exemple(type_document)
            for _, variables in contextes.VARIABLES[type_document]:
                for chemin, _libelle in variables:
                    racine, _, reste = chemin.partition(".")
                    if racine in alias:
                        chemin = f"{alias[racine]}.0.{reste}"
                    valeur = moteur._lire(ctx, chemin, inconnues := set())
                    self.assertFalse(inconnues, f"{type_document} : {chemin}")

    def test_blocs_de_l_editeur_utilisent_des_variables_connues(self):
        for type_document in T.values:
            ctx = contextes.contexte_exemple(type_document)
            for bloc in blocs_editeur(type_document):
                _, inconnues = moteur.appliquer(bloc["contenu"], ctx)
                self.assertEqual(inconnues, [], f"{type_document} / {bloc['id']}")


class PersonnalisationDesPdfTests(_FixtureOrdresCommande, TestCase):
    """Un modèle actif remplace le PDF d'origine ; sinon, ou s'il est cassé, le PDF d'origine sort."""

    def activer(self, type_document, html, css=""):
        ModeleDocument.objects.update_or_create(
            type_document=type_document, defaults={"html": html, "css": css, "actif": True}
        )

    def test_sans_modele_actif_le_pdf_d_origine(self):
        pdf = generer_pdf_ar_commande(self.commande)
        self.assertIn(b"ReportLab", pdf)
        ModeleDocument.pour(T.AR_COMMANDE)  # existe mais inactif
        self.assertIn(b"ReportLab", generer_pdf_ar_commande(self.commande))

    def test_modele_actif_utilise_pour_l_ar_et_le_bon_de_preparation(self):
        self.activer(T.AR_COMMANDE, "<p>AR perso {{ document.numero }}</p>")
        self.activer(T.BON_PREPARATION, "<p>PREP perso</p>")
        self.assertNotIn(b"ReportLab", generer_pdf_ar_commande(self.commande))
        self.assertNotIn(b"ReportLab", generer_pdf_bon_preparation(self.commande))

    def test_modele_par_defaut_actif_donne_le_meme_contenu_que_l_original(self):
        html, css = modele_par_defaut(T.AR_COMMANDE)
        self.activer(T.AR_COMMANDE, html, css)
        document, inconnues = rendre(html, css, contextes.contexte_ar_commande(self.commande))
        self.assertEqual(inconnues, [])
        contexte = contextes.contexte_ar_commande(self.commande)
        self.assertEqual(contexte["totaux"]["ht"].replace(" ", " ").replace("\xa0", " "), "800,00 €")
        self.assertEqual(contexte["totaux"]["ttc"].replace(" ", " ").replace("\xa0", " "), "960,00 €")
        self.assertEqual(contexte["facturation"]["nom"], "Client Fabrication")
        self.assertEqual(contexte["livraison"]["adresse"], "1 rue des Forges\n69000 Lyon")
        self.assertEqual([l["livraison_prevue"] for l in contexte["lignes"]], ["20/12/2026", "10/12/2026", "—", "05/12/2026"])
        self.assertTrue(moteur.ecrire([document]).startswith(b"%PDF"))

    def test_les_refus_metier_passent_meme_avec_un_modele_actif(self):
        from chiffrage.models import CommandeLigne

        self.activer(T.AR_COMMANDE, "<p>perso</p>")
        CommandeLigne.objects.filter(pk=self.l3.pk).update(prix_vente_unitaire=None)
        with self.assertRaises(DocumentError):
            generer_pdf_ar_commande(self.commande.__class__.objects.get(pk=self.commande.pk))

    def test_modele_inutilisable_repli_sur_le_pdf_d_origine_et_journal(self):
        self.activer(T.AR_COMMANDE, "<p>perso</p>")
        with mock.patch("documents.moteur.vers_pdf", side_effect=RuntimeError("panne")):
            with self.assertLogs("documents.rendu", level="ERROR"):
                pdf = generer_pdf_ar_commande(self.commande)
        self.assertIn(b"ReportLab", pdf)

    def test_impression_groupee_des_of_avec_un_modele(self):
        ordres = creer_ordres_fabrication(self.commande)
        self.activer(T.FICHE_FABRICATION, '<p class="n">Fiche {{ of.numero }}</p>')
        captures = []
        vrai = moteur.ecrire
        with mock.patch("documents.rendu.moteur.ecrire", side_effect=lambda docs: (captures.append(list(docs)), vrai(captures[-1]))[1]):
            pdf = generer_pdf_ordres_fabrication(ordres)
        self.assertNotIn(b"ReportLab", pdf)
        self.assertEqual([len(d.pages) for d in captures[0]], [1, 1, 1])  # un document, donc une page, par OF
        with self.assertRaises(DocumentError):
            generer_pdf_ordres_fabrication([])

    def test_devis_actif(self):
        from datetime import date

        from chiffrage.models import Devis, DevisLigne
        from chiffrage.moteur import calculer_devis

        devis = Devis.objects.create(numero="DEV-PERSO", client=self.tiers, date_creation=date(2026, 10, 1))
        DevisLigne.objects.create(devis=devis, article=self.vis, quantite=10)
        calculer_devis(devis)
        self.activer(T.DEVIS, "<h1>{{ document.titre }}</h1>")
        self.assertNotIn(b"ReportLab", generer_pdf_devis(devis))


class EditeurAdminTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser("doc-admin", "d@example.com", "pass-mot-de-passe-9")
        self.staff = User.objects.create_user("doc-staff", password="pass-mot-de-passe-9", is_staff=True)
        from django.contrib.auth.models import Permission

        self.staff.user_permissions.add(*Permission.objects.filter(content_type__app_label="documents"))
        self.client.force_login(self.admin)
        self.liste = "/admin/documents/modeledocument/"

    def _modele(self, type_document=T.DEVIS):
        self.client.get(self.liste)  # crée les cinq modèles
        return ModeleDocument.objects.get(type_document=type_document)

    def _json(self, url, donnees):
        return self.client.post(url, json.dumps(donnees), content_type="application/json")

    def test_la_liste_cree_les_cinq_modeles_inactifs(self):
        reponse = self.client.get(self.liste)
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(ModeleDocument.objects.count(), 5)
        self.assertFalse(ModeleDocument.objects.filter(actif=True).exists())

    def test_reserve_aux_superutilisateurs(self):
        modele = self._modele()
        self.client.force_login(self.staff)
        for url, methode in (
            (f"{self.liste}{modele.pk}/change/", "get"), (f"{self.liste}{modele.pk}/defaut/", "get"),
            (f"{self.liste}{modele.pk}/objets/", "get"),
        ):
            self.assertEqual(getattr(self.client, methode)(url).status_code, 403, url)
        for url in (f"{self.liste}{modele.pk}/enregistrer/", f"{self.liste}{modele.pk}/apercu/"):
            self.assertEqual(self._json(url, {"html": "<p>x</p>"}).status_code, 403, url)
        self.assertEqual(self.client.get(self.liste).status_code, 403)
        modele.refresh_from_db()
        self.assertEqual(modele.version, 1)

    def test_pas_d_ajout_ni_de_suppression(self):
        modele = self._modele()
        self.assertEqual(self.client.get(f"{self.liste}add/").status_code, 403)
        self.assertEqual(self.client.post(f"{self.liste}{modele.pk}/delete/", {"post": "yes"}).status_code, 403)

    def test_page_de_l_editeur(self):
        modele = self._modele(T.BON_LIVRAISON)
        page = self.client.get(f"{self.liste}{modele.pk}/change/")
        self.assertContains(page, 'id="doc-config"')
        self.assertContains(page, "vendor/grapes.min")
        self.assertContains(page, "ligne.coulees")  # palette de variables du bon de livraison

    def test_enregistrer_active_incremente_la_version_et_historise(self):
        modele = self._modele()
        url = f"{self.liste}{modele.pk}/enregistrer/"
        reponse = self._json(url, {"html": "<p>{{ document.titre }} {{ inconnue.truc }}</p>", "css": "p{color:red}", "projet": "{}", "actif": True})
        self.assertEqual(reponse.status_code, 200, reponse.content)
        donnees = reponse.json()
        self.assertEqual((donnees["version"], donnees["actif"], donnees["inconnues"]), (2, True, ["inconnue.truc"]))
        modele.refresh_from_db()
        self.assertTrue(modele.actif)
        self.assertEqual(modele.modifie_par, self.admin)
        self.assertEqual(modele.history.first().history_change_reason, "Modèle activé")
        self.assertEqual(modele.history.count(), 2)  # création + enregistrement

    def test_enregistrer_refuse_les_requetes_invalides(self):
        modele = self._modele()
        url = f"{self.liste}{modele.pk}/enregistrer/"
        self.assertEqual(self.client.post(url, "pas du json", content_type="application/json").status_code, 400)
        self.assertEqual(self._json(url, {"html": "x" * 1_600_000}).status_code, 400)
        self.assertEqual(self.client.get(url).status_code, 405)
        modele.refresh_from_db()
        self.assertEqual(modele.version, 1)

    def test_apercu_avec_donnees_d_exemple_et_avec_un_vrai_document(self):
        modele = self._modele(T.AR_COMMANDE)
        url = f"{self.liste}{modele.pk}/apercu/"
        reponse = self._json(url, {"html": "<p>{{ document.titre }}</p>", "css": ""})
        self.assertEqual((reponse.status_code, reponse["Content-Type"]), (200, "application/pdf"))
        self.assertTrue(reponse.content.startswith(b"%PDF"))
        self.assertEqual(ModeleDocument.objects.get(pk=modele.pk).version, 1)  # un aperçu n'enregistre rien
        self.assertEqual(self._json(url, {"html": "<p>x</p>", "objet": "CDE-INEXISTANTE"}).status_code, 404)

    def test_apercu_d_un_document_non_imprimable_explique_pourquoi(self):
        from chiffrage.models import Devis
        from datetime import date
        from commercial.models import Tiers

        client = Tiers.objects.create(code="CLI-APER", raison_sociale="Apercu", type_tiers=Tiers.TypeTiers.CLIENT)
        Devis.objects.create(numero="DEV-VIDE", client=client, date_creation=date(2026, 1, 1))
        modele = self._modele(T.DEVIS)
        reponse = self._json(f"{self.liste}{modele.pk}/apercu/", {"html": "<p>x</p>", "objet": "DEV-VIDE"})
        self.assertEqual(reponse.status_code, 400)
        self.assertIn("aucune ligne", reponse.json()["detail"])

    def test_modele_par_defaut_et_liste_des_objets(self):
        modele = self._modele(T.DEVIS)
        defaut = self.client.get(f"{self.liste}{modele.pk}/defaut/").json()
        self.assertIn("{{ document.titre }}", defaut["html"])
        self.assertIn(".doc-titre", defaut["css"])
        self.assertEqual(self.client.get(f"{self.liste}{modele.pk}/objets/").json()["objets"], [])
