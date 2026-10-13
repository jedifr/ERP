"""Commande client : reprise de lignes d'un ou plusieurs devis, et ordre des lignes (CommandeLigne.ordre)."""

import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from commercial.models import Adresse, Tiers
from technique.models import Article

from .models import Commande, CommandeLigne, Devis, DevisLigne
from .moteur import ChiffrageError
from .production import devis_importables, importer_lignes_devis, lancer_en_production


class ImportLignesDevisBase(TestCase):
    def setUp(self):
        self.client_tiers = Tiers.objects.create(
            code="CLI-IMPORT", raison_sociale="Client Import", type_tiers=Tiers.TypeTiers.CLIENT
        )
        self.adresse = Adresse.objects.create(
            tiers=self.client_tiers, est_facturation=True, est_livraison=True, est_principale=True,
            adresse="1 rue", code_postal="75000", ville="Paris",
        )
        self.article_a = Article.objects.create(
            reference="ART-IMP-A", nature=Article.Nature.MATIERE_PREMIERE,
            unite_cout=Article.UniteCout.PIECE, cout_unitaire=1.0,
        )
        self.article_b = Article.objects.create(
            reference="ART-IMP-B", nature=Article.Nature.MATIERE_PREMIERE,
            unite_cout=Article.UniteCout.PIECE, cout_unitaire=2.0,
        )
        self.devis_1 = self._devis("DEV-IMP-1")
        self.l1a = DevisLigne.objects.create(
            devis=self.devis_1, article=self.article_a, quantite=3, ordre=0, prix_vente_unitaire_force=Decimal("10")
        )
        self.l1b = DevisLigne.objects.create(
            devis=self.devis_1, article=self.article_b, quantite=5, ordre=1, designation="Libellé devis",
            prix_vente_unitaire_force=Decimal("20"),
        )
        self.devis_2 = self._devis("DEV-IMP-2")
        self.l2a = DevisLigne.objects.create(
            devis=self.devis_2, article=self.article_b, quantite=7, ordre=0, prix_vente_unitaire_force=Decimal("30")
        )
        self.commande = Commande.objects.create(
            numero="CDE-IMPORT", client=self.client_tiers, reference_client="PO-1",
            date_commande=datetime.date(2026, 10, 1), adresse_facturation=self.adresse, adresse_livraison=self.adresse,
        )
        self.existante = CommandeLigne.objects.create(
            commande=self.commande, article=self.article_a, quantite_commandee=1, ordre=0
        )

    def _devis(self, numero, client=None, **kwargs):
        valeurs = {
            "statut": Devis.Statut.VALIDE,
            "date_creation": timezone.localdate(),
            "date_validite": timezone.localdate() + datetime.timedelta(days=30),
        }
        valeurs.update(kwargs)
        return Devis.objects.create(numero=numero, client=client or self.client_tiers, **valeurs)


class ImporterLignesDevisTests(ImportLignesDevisBase):
    def test_lignes_de_plusieurs_devis_ajoutees_en_fin_dans_l_ordre_recu(self):
        creees = importer_lignes_devis(self.commande, [self.l2a, self.l1b, self.l1a])

        self.assertEqual(list(self.commande.lignes.all()), [self.existante, *creees])
        self.assertEqual([l.ordre for l in creees], [1, 2, 3])
        self.assertEqual([l.devis_ligne for l in creees], [self.l2a, self.l1b, self.l1a])
        ligne = creees[1]
        self.assertEqual(ligne.article, self.article_b)
        self.assertEqual(ligne.quantite_commandee, 5)
        self.assertEqual(ligne.designation, "Libellé devis")
        self.assertEqual(ligne.prix_vente_unitaire, self.l1b.prix_vente_unitaire)

    def test_devis_en_attente_passe_a_accepte(self):
        importer_lignes_devis(self.commande, [self.l1a])
        self.devis_1.refresh_from_db()
        self.devis_2.refresh_from_db()
        self.assertEqual(self.devis_1.issue, Devis.Issue.ACCEPTE)
        self.assertEqual(self.devis_2.issue, Devis.Issue.EN_ATTENTE)

    def test_devis_importables_exclut_autre_client_brouillon_refuse(self):
        autre = Tiers.objects.create(code="CLI-AUTRE-IMP", raison_sociale="Autre", type_tiers=Tiers.TypeTiers.CLIENT)
        self._devis("DEV-AUTRE", client=autre)
        self._devis("DEV-BROUILLON", statut=Devis.Statut.BROUILLON)
        self._devis("DEV-REFUSE", issue=Devis.Issue.REFUSE, motif_refus="Trop cher")
        self.assertEqual({d.numero for d in devis_importables(self.commande)}, {"DEV-IMP-1", "DEV-IMP-2"})

    def test_ligne_d_un_devis_d_un_autre_client_refusee(self):
        autre = Tiers.objects.create(code="CLI-AUTRE-IMP", raison_sociale="Autre", type_tiers=Tiers.TypeTiers.CLIENT)
        ligne = DevisLigne.objects.create(devis=self._devis("DEV-AUTRE", client=autre), article=self.article_a, quantite=1)
        with self.assertRaises(ChiffrageError):
            importer_lignes_devis(self.commande, [self.l1a, ligne])
        self.assertEqual(self.commande.lignes.count(), 1)

    def test_offre_expiree_refusee(self):
        expire = self._devis("DEV-EXPIRE", date_validite=timezone.localdate() - datetime.timedelta(days=1))
        ligne = DevisLigne.objects.create(devis=expire, article=self.article_a, quantite=1)
        with self.assertRaisesMessage(ChiffrageError, "expiré"):
            importer_lignes_devis(self.commande, [ligne])

    def test_commande_annulee_refusee(self):
        self.commande.statut = Commande.Statut.ANNULEE
        self.commande.save()
        with self.assertRaises(ChiffrageError):
            importer_lignes_devis(self.commande, [self.l1a])

    def test_lancement_en_production_numerote_les_lignes_comme_le_devis(self):
        DevisLigne.objects.filter(pk=self.l1a.pk).update(ordre=5)
        commande = lancer_en_production(self.devis_1)
        self.assertEqual(
            [(l.devis_ligne, l.ordre) for l in commande.lignes.all()], [(self.l1b, 0), (self.l1a, 1)]
        )


class ImporterLignesDevisAdminTests(ImportLignesDevisBase):
    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_superuser("import-admin", "import@example.com", "pass1234")
        self.client.force_login(self.user)
        self.url = reverse("admin:chiffrage_commande_action_importer_devis", args=[self.commande.pk])

    def test_page_liste_les_devis_du_client(self):
        reponse = self.client.get(self.url)
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, "DEV-IMP-1")
        self.assertContains(reponse, "DEV-IMP-2")
        self.assertContains(reponse, f'value="{self.l1b.pk}"')

    def test_post_ajoute_les_lignes_cochees_dans_l_ordre_affiche(self):
        # Affichage : devis les plus récents d'abord, à date égale par numéro décroissant (DEV-IMP-2 puis DEV-IMP-1).
        reponse = self.client.post(self.url, {"lignes": [str(self.l1a.pk), str(self.l2a.pk)]})
        self.assertRedirects(
            reponse, reverse("admin:chiffrage_commande_change", args=[self.commande.pk]), fetch_redirect_response=False
        )
        self.assertEqual(
            [l.devis_ligne for l in self.commande.lignes.all()], [None, self.l2a, self.l1a]
        )

    def test_post_sans_ligne_cochee_n_ajoute_rien(self):
        reponse = self.client.post(self.url, {})
        self.assertEqual(reponse.status_code, 302)
        self.assertEqual(self.commande.lignes.count(), 1)

    def test_bouton_sur_la_fiche_commande(self):
        reponse = self.client.get(reverse("admin:chiffrage_commande_change", args=[self.commande.pk]))
        self.assertContains(reponse, self.url)
        self.assertContains(reponse, "chiffrage/devisligne_reorder.")


class ReordonnerLignesCommandeAdminTests(ImportLignesDevisBase):
    """Le glisser-déposer (devisligne_reorder.js) ne fait que remplir le champ caché `ordre` de chaque ligne :
    on rejoue ici le POST de la fiche commande après inversion de deux lignes."""

    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_superuser("reorder-cde", "reorder-cde@example.com", "pass1234")
        self.client.force_login(self.user)
        self.seconde = CommandeLigne.objects.create(
            commande=self.commande, article=self.article_b, quantite_commandee=2, ordre=1
        )
        self.url = reverse("admin:chiffrage_commande_change", args=[self.commande.pk])

    def _donnees_formulaire(self):
        reponse = self.client.get(self.url)
        donnees = {}
        formulaires = [reponse.context["adminform"].form]
        for inline in reponse.context["inline_admin_formsets"]:
            formset = inline.formset
            donnees.update({f"{formset.prefix}-{k}": v for k, v in formset.management_form.initial.items()})
            formulaires += list(formset.forms)
        for form in formulaires:
            for nom, champ in form.fields.items():
                valeur = form[nom].value()
                if valeur is None or valeur is False:
                    continue
                if hasattr(valeur, "strftime"):
                    valeur = valeur.strftime("%d/%m/%Y")
                donnees[form.add_prefix(nom)] = valeur
        return donnees

    def test_inversion_de_deux_lignes_persiste(self):
        donnees = self._donnees_formulaire()
        self.assertEqual(donnees["lignes-0-id"], self.existante.pk)
        donnees["lignes-0-ordre"], donnees["lignes-1-ordre"] = "1", "0"
        donnees["_continue"] = "Enregistrer"
        reponse = self.client.post(self.url, donnees)
        self.assertEqual(reponse.status_code, 302, getattr(reponse, "context", None) and reponse.context.get("errors"))
        self.assertEqual(list(self.commande.lignes.all()), [self.seconde, self.existante])

    def test_ligne_ajoutee_sans_ordre_va_en_fin(self):
        donnees = self._donnees_formulaire()
        donnees.update({
            "lignes-TOTAL_FORMS": "3",
            "lignes-2-commande": self.commande.pk,
            "lignes-2-article": self.article_a.pk,
            "lignes-2-quantite_commandee": "4",
            "lignes-2-ordre": "",
            "_continue": "Enregistrer",
        })
        reponse = self.client.post(self.url, donnees)
        self.assertEqual(reponse.status_code, 302, getattr(reponse, "context", None) and reponse.context.get("errors"))
        derniere = list(self.commande.lignes.all())[-1]
        self.assertEqual((derniere.quantite_commandee, derniere.ordre), (4, 2))


class OutilsLignesCommandeAdminTests(ImportLignesDevisBase):
    """Boutons « Importer des lignes de devis » / « Composer un article » sous les lignes de la commande, et les
    boutons « Enregistrer puis… » du formulaire d'ajout (avant, l'import n'était qu'une action du menu « Actions »,
    absent du formulaire d'ajout)."""

    def setUp(self):
        super().setUp()
        from commercial.models import TauxTVA
        from technique.models import PosteTravail

        TauxTVA.objects.get_or_create(taux=20, defaults={"nom": "Taux normal", "est_defaut": True})
        TauxTVA.objects.filter(taux=20).update(est_defaut=True)
        PosteTravail.objects.create(nom="Poste-Cde", mode_calcul=PosteTravail.ModeCalcul.FORFAITAIRE)
        self.user = get_user_model().objects.create_superuser("outils-cde", "outils@example.com", "pass1234")
        self.client.force_login(self.user)
        self.url_fiche = reverse("admin:chiffrage_commande_change", args=[self.commande.pk])
        self.url_composer = reverse("admin:chiffrage_commande_constructeur", args=[self.commande.pk])

    def test_fiche_porte_les_deux_boutons(self):
        reponse = self.client.get(self.url_fiche)
        self.assertContains(reponse, reverse("admin:chiffrage_commande_action_importer_devis", args=[self.commande.pk]))
        self.assertContains(reponse, 'id="lignes-constructeur"')
        self.assertContains(reponse, "Composer un article")
        self.assertContains(reponse, "/lignes/composer/")

    def test_commande_annulee_sans_outils(self):
        Commande.objects.filter(pk=self.commande.pk).update(statut=Commande.Statut.ANNULEE)
        self.assertNotContains(self.client.get(self.url_fiche), 'id="lignes-constructeur"')

    def test_formulaire_d_ajout_propose_enregistrer_puis(self):
        reponse = self.client.get(reverse("admin:chiffrage_commande_add"))
        self.assertContains(reponse, 'name="_puis" value="importer_devis"')
        self.assertContains(reponse, 'name="_puis" value="composer"')
        self.assertNotContains(reponse, 'id="lignes-constructeur"')

    def test_enregistrer_puis_importer_redirige_vers_l_import_avec_lignes_du_devis_precochees(self):
        donnees = {
            "numero": "CDE-NOUVELLE", "devis": self.devis_1.pk, "client": self.client_tiers.pk, "reference_client": "PO-2",
            "date_commande": "01/10/2026", "adresse_facturation": self.adresse.pk, "adresse_livraison": self.adresse.pk,
            "lignes-TOTAL_FORMS": "0", "lignes-INITIAL_FORMS": "0", "lignes-MIN_NUM_FORMS": "0", "lignes-MAX_NUM_FORMS": "1000",
            "_puis": "importer_devis",
        }
        reponse = self.client.post(reverse("admin:chiffrage_commande_add"), donnees)
        self.assertEqual(reponse.status_code, 302, getattr(reponse, "context", None) and reponse.context.get("errors"))
        commande = Commande.objects.get(reference_client="PO-2")
        self.assertEqual(reponse.url, reverse("admin:chiffrage_commande_action_importer_devis", args=[commande.pk]))
        page = self.client.get(reponse.url)
        self.assertContains(page, f'id="ligne-{self.l1a.pk}" checked')
        self.assertNotContains(page, f'id="ligne-{self.l2a.pk}" checked')

    def test_composer_article_existant_ajoute_une_ligne_chiffree(self):
        reponse = self.client.post(
            self.url_composer, {"quantite": 4, "article_existant": "ART-IMP-B", "designation": "Tôle"}, content_type="application/json"
        )
        self.assertEqual(reponse.status_code, 200, reponse.content)
        ligne = self.commande.lignes.get(pk=reponse.json()["ligne_id"])
        self.assertEqual((ligne.article_id, ligne.quantite_commandee, ligne.designation, ligne.ordre), ("ART-IMP-B", 4, "Tôle", 1))
        self.assertIsNone(ligne.devis_ligne)
        self.assertEqual(ligne.prix_vente_unitaire, Decimal("2"))  # coût unitaire, sans marge par défaut
        self.assertIsNotNone(ligne.taux_tva)

    def test_composer_nouvel_article_et_apercu(self):
        payload = {
            "quantite": 3,
            "nouvel_article": {
                "reference": "PIECE-CDE", "taux_marge_defaut": 10,
                "composants": [{"article_composant": "ART-IMP-A", "quantite": 5}],
                "etapes": [{"poste": "Poste-Cde", "ordre": 1, "cout_forfaitaire": 50}],
            },
        }
        apercu = self.client.post(self.url_composer, {**payload, "apercu": True}, content_type="application/json")
        self.assertEqual(apercu.status_code, 200, apercu.content)
        self.assertFalse(Article.objects.filter(pk="PIECE-CDE").exists())
        self.assertEqual(self.commande.lignes.count(), 1)
        # matière 3 × 5 × 1 = 15, +10 % = 16,5 ; opération forfaitaire 3 × 50 = 150
        self.assertAlmostEqual(float(apercu.json()["prix_vente_total"]), 166.5, places=2)

        reponse = self.client.post(self.url_composer, payload, content_type="application/json")
        self.assertEqual(reponse.status_code, 200, reponse.content)
        ligne = self.commande.lignes.get(pk=reponse.json()["ligne_id"])
        self.assertEqual(ligne.article_id, "PIECE-CDE")
        self.assertAlmostEqual(float(ligne.prix_vente_unitaire), 55.5, places=2)

    def test_composer_refuse_sur_commande_annulee(self):
        Commande.objects.filter(pk=self.commande.pk).update(statut=Commande.Statut.ANNULEE)
        reponse = self.client.post(self.url_composer, {"quantite": 1, "article_existant": "ART-IMP-A"}, content_type="application/json")
        self.assertEqual(reponse.status_code, 400)
        self.assertEqual(self.commande.lignes.count(), 1)
