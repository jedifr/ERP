"""Référence du devis : obligatoire à la saisie, reprise par les révisions et imprimée sur l'offre."""

import datetime

from django.contrib.admin.sites import site
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

from documents.contextes import contexte_devis
from commercial.models import Adresse, Tiers
from technique.models import Article

from .admin import DevisAdminForm
from .models import Devis, DevisLigne
from .moteur import calculer_devis
from .production import reviser_devis


class ReferenceDevisTests(TestCase):
    def setUp(self):
        self.tiers = Tiers.objects.create(code="CLI-REF", raison_sociale="Client réf.", type_tiers=Tiers.TypeTiers.CLIENT)
        self.adresse = Adresse.objects.create(
            tiers=self.tiers, est_facturation=True, est_livraison=True, est_principale=True,
            adresse="1 rue", code_postal="75000", ville="Paris",
        )

    def _formulaire(self, **donnees):
        data = {"numero": "DEV-REF", "client": self.tiers.pk, "date_creation": "2026-01-01", "statut": "brouillon",
                "issue": "en_attente", "remise_globale_type": "pct", **donnees}
        return DevisAdminForm(data=data)

    def _devis(self, **kwargs):
        return Devis.objects.create(
            numero="DEV-REF", client=self.tiers, date_creation=datetime.date(2026, 1, 1),
            adresse_facturation=self.adresse, adresse_livraison=self.adresse, **kwargs,
        )

    def test_reference_obligatoire(self):
        form = self._formulaire()
        self.assertFalse(form.is_valid())
        self.assertIn("reference", form.errors)
        self.assertTrue(self._formulaire(reference="Garde-corps escalier").is_valid())

    def test_reprise_par_la_revision(self):
        devis = self._devis(reference="Platines", statut=Devis.Statut.VALIDE)
        self.assertEqual(reviser_devis(devis, "Quantités").reference, "Platines")

    def test_imprimee_sur_l_offre(self):
        devis = self._devis(reference="Garde-corps escalier")
        article = Article.objects.create(
            reference="MP-REF", nature=Article.Nature.MATIERE_PREMIERE,
            unite_cout=Article.UniteCout.PIECE, cout_unitaire=10, taux_marge_defaut=20,
        )
        DevisLigne.objects.create(devis=devis, article=article, quantite=1)
        calculer_devis(devis)
        contexte = contexte_devis(devis)
        self.assertEqual(contexte["reference"], "Garde-corps escalier")
        self.assertIn("Référence : Garde-corps escalier", contexte["document"]["references_html"])

    def test_tva_sur_la_ligne_de_l_adresse_de_facturation(self):
        admin = site._registry[Devis]
        request = RequestFactory().get("/")
        request.user = get_user_model().objects.create_superuser("ref", "ref@example.com", "x")
        saisie = admin.get_fieldsets(request, None)[0][1]["fields"]
        self.assertIn(("adresse_facturation", "taux_tva"), saisie)
        self.assertEqual(saisie[saisie.index(("adresse_facturation", "taux_tva")) + 1], "reference")
        # Devis validé (lecture seule) : chaque champ garde sa ligne.
        saisie = admin.get_fieldsets(request, self._devis(statut=Devis.Statut.VALIDE))[0][1]["fields"]
        self.assertNotIn(("adresse_facturation", "taux_tva"), saisie)
