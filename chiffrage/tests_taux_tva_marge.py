"""Taux de TVA choisi sur le devis / la commande (onglet Général) et colonne « Taux de marge » des lignes de commande, qui
remplace la colonne « Taux de TVA ». L'assistant « Composer un article » ne demande plus la marge."""
import datetime
from decimal import Decimal
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import TestCase

from commercial.models import Adresse, TauxTVA, Tiers
from technique.models import Article, Gamme, Nomenclature, PosteTravail, TarifPoste

from .admin import TauxTvaDocumentMixin
from .builder_views import _creer_article_depuis_payload
from .models import Commande, CommandeLigne, Devis, DevisLigne
from .moteur import previsualiser_ligne_commande, taux_tva_document
from .production import ajouter_ligne_commande, lancer_en_production


class Base(TestCase):
    def setUp(self):
        # Le référentiel peut déjà contenir des taux (données initiales des migrations).
        TauxTVA.objects.update(est_defaut=False)
        self.normal = TauxTVA.objects.create(nom="Normal (test)", taux=20, est_defaut=True)
        self.reduit = TauxTVA.objects.create(nom="Réduit (test)", taux=10)
        self.zero = TauxTVA.objects.filter(taux=0).first() or TauxTVA.objects.create(nom="Exonéré (test)", taux=0)
        self.tiers = Tiers.objects.create(code="CLI-TVA-DOC", raison_sociale="Client TVA", type_tiers=Tiers.TypeTiers.CLIENT)
        self.adresse = Adresse.objects.create(
            tiers=self.tiers, est_facturation=True, est_livraison=True, est_principale=True,
            adresse="1 rue", code_postal="75000", ville="Paris",
        )
        self.matiere = Article.objects.create(
            reference="MP-TVA-DOC", nature=Article.Nature.MATIERE_PREMIERE,
            unite_cout=Article.UniteCout.PIECE, cout_unitaire=10, taux_marge_defaut=20,
        )
        self.fabrique = Article.objects.create(reference="FAB-TVA-DOC", nature=Article.Nature.FABRIQUE, taux_marge_defaut=25)
        Nomenclature.objects.create(article_parent=self.fabrique, article_composant=self.matiere, quantite=2)
        self.poste = poste = PosteTravail.objects.create(nom="Poste-TVA-DOC", mode_calcul=PosteTravail.ModeCalcul.HORAIRE, taux_marge_defaut=10)
        TarifPoste.objects.create(poste=poste, cout_horaire=60, date_debut=datetime.date(2020, 1, 1))
        Gamme.objects.create(
            article=self.fabrique, poste=poste, ordre=1, temps_fixe=0, temps_variable=6, date_debut=datetime.date(2020, 1, 1),
        )

    def commande(self, **kwargs):
        return Commande.objects.create(
            numero="CDE-TVA-DOC", client=self.tiers, date_commande=datetime.date(2026, 1, 1),
            adresse_facturation=self.adresse, adresse_livraison=self.adresse, **kwargs,
        )


class TauxTvaDocumentTests(Base):
    def test_taux_propose_selon_regime_fiscal(self):
        self.assertEqual(taux_tva_document(self.tiers), self.normal)
        self.tiers.regime_fiscal = Tiers.RegimeFiscal.INTRA_UE
        self.assertEqual(taux_tva_document(self.tiers), self.zero)
        self.assertIsNone(taux_tva_document(None))

    def test_taux_du_devis_recopie_sur_toutes_les_lignes(self):
        devis = Devis.objects.create(numero="DEV-TVA-DOC", client=self.tiers, date_creation=datetime.date(2026, 1, 1))
        ligne = DevisLigne.objects.create(devis=devis, article=self.matiere, quantite=1)
        self.assertEqual(ligne.taux_tva, self.normal)  # sans taux sur le devis : la ligne garde le sien

        devis.taux_tva = self.reduit
        devis.save()
        ligne.refresh_from_db()
        self.assertEqual(ligne.taux_tva, self.reduit)
        nouvelle = DevisLigne.objects.create(devis=devis, article=self.matiere, quantite=2, taux_tva=self.normal)
        self.assertEqual(nouvelle.taux_tva, self.reduit)

    def test_taux_de_la_commande_recopie_sur_ses_lignes(self):
        commande = self.commande(taux_tva=self.zero)
        ligne = ajouter_ligne_commande(commande, self.matiere, 3)
        self.assertEqual(ligne.taux_tva, self.zero)
        commande.taux_tva = self.normal
        commande.save()
        ligne.refresh_from_db()
        self.assertEqual(ligne.taux_tva, self.normal)

    def test_commande_creee_depuis_le_devis_reprend_son_taux_et_sa_marge(self):
        devis = Devis.objects.create(
            numero="DEV-TVA-CDE", client=self.tiers, date_creation=datetime.date(2026, 1, 1),
            statut=Devis.Statut.VALIDE, taux_tva=self.reduit,
        )
        DevisLigne.objects.create(devis=devis, article=self.matiere, quantite=1, taux_marge_matiere_applique=35)
        commande = lancer_en_production(devis)
        self.assertEqual(commande.taux_tva, self.reduit)
        ligne = commande.lignes.get()
        self.assertEqual(ligne.taux_tva, self.reduit)
        self.assertEqual(ligne.taux_marge, Decimal("35"))

    def test_admin_remplit_le_taux_laisse_vide_selon_le_client(self):
        class Parent:
            def save_model(self, request, obj, form, change):
                obj.save()

        class Admin(TauxTvaDocumentMixin, Parent):
            pass

        self.tiers.regime_fiscal = Tiers.RegimeFiscal.HORS_UE
        self.tiers.save()
        devis = Devis(numero="DEV-TVA-ADM", client=self.tiers, date_creation=datetime.date(2026, 1, 1))
        form = SimpleNamespace(fields={"taux_tva": None, "client": None}, changed_data=["client"])
        Admin().save_model(None, devis, form, False)
        self.assertEqual(devis.taux_tva, self.zero)

        # Taux choisi à la main : gardé.
        devis.taux_tva = self.reduit
        Admin().save_model(None, devis, SimpleNamespace(fields={"taux_tva": None}, changed_data=["taux_tva"]), True)
        self.assertEqual(devis.taux_tva, self.reduit)


class MargeLigneCommandeTests(Base):
    def test_marge_remplace_celle_de_l_article(self):
        # coût matière 10 × 3 = 30 ; marge 50 % au lieu de 20 % : 45 €
        resultat = previsualiser_ligne_commande(self.matiere, 3, datetime.date(2026, 1, 1), taux_marge=50)
        self.assertAlmostEqual(float(resultat["montant_ht"]), 45)
        self.assertEqual(resultat["taux_marge"], 50)
        self.assertEqual(previsualiser_ligne_commande(self.matiere, 3, datetime.date(2026, 1, 1))["taux_marge"], 20)

    def test_ligne_ajoutee_porte_la_marge_de_l_article(self):
        ligne = ajouter_ligne_commande(self.commande(), self.fabrique, 2)
        self.assertEqual(ligne.taux_marge, Decimal("25"))

    def _poster(self, ligne, donnees):
        user = get_user_model().objects.create_superuser("marge-admin", "m@example.com", "pass1234")
        self.client.force_login(user)
        return self.client.post(
            f"/admin/chiffrage/commande/{ligne.commande_id}/lignes/{ligne.id}/recalculer/",
            data=donnees, content_type="application/json",
        )

    def test_marge_modifiee_recalcule_le_prix_meme_sur_une_ligne_de_devis(self):
        devis = Devis.objects.create(numero="DEV-MARGE", client=self.tiers, date_creation=datetime.date(2026, 1, 1))
        ligne_devis = DevisLigne.objects.create(devis=devis, article=self.matiere, quantite=3)
        ligne = CommandeLigne.objects.create(
            commande=self.commande(devis=devis), article=self.matiere, quantite_commandee=3,
            devis_ligne=ligne_devis, prix_vente_unitaire=99, taux_marge=20,
        )
        response = self._poster(ligne, {"quantite_commandee": 3, "taux_marge": 50})
        self.assertEqual(response.status_code, 200, response.content)
        ligne.refresh_from_db()
        self.assertEqual(ligne.taux_marge, Decimal("50"))
        self.assertEqual(ligne.prix_vente_unitaire, Decimal("15"))  # 10 × 1,5

    def test_quantite_seule_ne_touche_pas_le_prix_d_une_ligne_de_devis(self):
        devis = Devis.objects.create(numero="DEV-MARGE-2", client=self.tiers, date_creation=datetime.date(2026, 1, 1))
        ligne_devis = DevisLigne.objects.create(devis=devis, article=self.matiere, quantite=3)
        ligne = CommandeLigne.objects.create(
            commande=self.commande(devis=devis), article=self.matiere, quantite_commandee=3,
            devis_ligne=ligne_devis, prix_vente_unitaire=99, taux_marge=20,
        )
        self._poster(ligne, {"quantite_commandee": 4})
        ligne.refresh_from_db()
        self.assertEqual(ligne.prix_vente_unitaire, Decimal("99"))


class ComposerArticleSansMargeTests(Base):
    def test_nouvel_article_reprend_la_marge_de_son_premier_composant(self):
        article = _creer_article_depuis_payload(
            {"reference": "NOUV-MARGE", "composants": [{"article_composant": "MP-TVA-DOC", "quantite": 1}],
             "etapes": [{"poste": self.poste.pk, "ordre": 1, "temps_fixe": 0, "temps_variable": 1}]},
            datetime.date(2026, 1, 1),
        )
        self.assertEqual(article.taux_marge_defaut, Decimal("20"))

    def test_marge_envoyee_reste_prioritaire(self):
        article = _creer_article_depuis_payload(
            {"reference": "NOUV-MARGE-2", "taux_marge_defaut": 40, "composants": [{"article_composant": "MP-TVA-DOC", "quantite": 1}],
             "etapes": [{"poste": self.poste.pk, "ordre": 1, "temps_fixe": 0, "temps_variable": 1}]},
            datetime.date(2026, 1, 1),
        )
        self.assertEqual(article.taux_marge_defaut, Decimal("40"))


class MargeAssistantTests(Base):
    """Le taux de marge saisi dans l'assistant (pré-rempli avec la marge par défaut) s'applique à la ligne créée."""

    def setUp(self):
        super().setUp()
        self.client.force_login(get_user_model().objects.create_superuser("assistant", "a@example.com", "pass1234"))

    def test_ligne_de_devis(self):
        devis = Devis.objects.create(numero="DEV-ASS-MARGE", client=self.tiers, date_creation=datetime.date(2026, 1, 1))
        response = self.client.post(
            f"/admin/chiffrage/devis/{devis.pk}/constructeur/",
            data={"quantite": 3, "article_existant": "MP-TVA-DOC", "taux_marge": 50}, content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        ligne = devis.lignes.get()
        self.assertEqual(ligne.taux_marge_matiere_applique, Decimal("50"))
        self.assertEqual(ligne.prix_vente_matiere, Decimal("45"))  # 30 € de matière + 50 %

    def test_ligne_de_commande(self):
        commande = self.commande()
        response = self.client.post(
            f"/admin/chiffrage/commande/{commande.pk}/lignes/composer/",
            data={"quantite": 3, "article_existant": "MP-TVA-DOC", "taux_marge": 50}, content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        ligne = commande.lignes.get()
        self.assertEqual(ligne.taux_marge, Decimal("50"))
        self.assertEqual(ligne.prix_vente_unitaire, Decimal("15"))

    def test_sans_marge_saisie_la_marge_de_l_article_s_applique(self):
        commande = self.commande()
        self.client.post(
            f"/admin/chiffrage/commande/{commande.pk}/lignes/composer/",
            data={"quantite": 3, "article_existant": "MP-TVA-DOC"}, content_type="application/json",
        )
        self.assertEqual(commande.lignes.get().taux_marge, Decimal("20"))
