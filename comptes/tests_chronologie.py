"""Chronologie d'affaire : reconstitution depuis les documents et leur historique, affichage sur les fiches."""

import datetime

from django.urls import reverse
from django.utils import timezone

from chiffrage.models import Commande, Devis, Livraison
from facturation.models import Facture, RelanceFacture
from facturation.tests_pdf import _Base

from .chronologie import chronologie, evenements


class ChronologieTests(_Base):
    def setUp(self):
        super().setUp()
        self.devis = self.commande.devis
        self.devis.statut = Devis.Statut.VALIDE
        self.devis.save()
        self.devis.issue = Devis.Issue.ACCEPTE
        self.devis.save()

    def titres(self, objet):
        return [e.titre for e in evenements(objet)]

    def test_affaire_complete_dans_l_ordre(self):
        facture = self.facture("FAC-CH-1", quantite=4)
        Livraison.objects.create(numero="BL-CH-1", commande=self.commande, date_livraison=datetime.date(2026, 1, 20))
        titres = self.titres(self.devis)
        for attendu in ("Devis DEV-PDF créé", "Devis DEV-PDF validé", "Devis DEV-PDF accepté", "Commande CDE-PDF", "Livraison BL-CH-1", "Facture FAC-CH-1"):
            self.assertIn(attendu, titres)
        self.assertLess(titres.index("Devis DEV-PDF créé"), titres.index("Commande CDE-PDF"))
        self.assertLess(titres.index("Livraison BL-CH-1"), titres.index("Facture FAC-CH-1"))

    def test_meme_affaire_depuis_n_importe_quel_document(self):
        facture = self.facture("FAC-CH-2")
        self.assertEqual(self.titres(facture), self.titres(self.devis))
        self.assertEqual(self.titres(self.commande), self.titres(self.devis))

    def test_document_courant_repere(self):
        facture = self.facture("FAC-CH-3")
        courants = [e.titre for e in evenements(facture) if e.courant]
        self.assertTrue(courants and all("FAC-CH-3" in t for t in courants))

    def test_relance_paiement_et_echeance(self):
        facture = self.facture("FAC-CH-4")
        RelanceFacture.objects.create(facture=facture, niveau=1, destinataire="c@x.fr", objet="Rappel", corps="…")
        titres = self.titres(facture)
        self.assertIn("Relance 1 de FAC-CH-4", titres)
        self.assertIn("Échéance de FAC-CH-4", titres)  # facture non payée : l'échéance figure dans la chronologie
        facture.statut_paiement = Facture.StatutPaiement.PAYE
        facture.save()
        titres = self.titres(facture)
        self.assertIn("Facture FAC-CH-4 payée", titres)
        self.assertNotIn("Échéance de FAC-CH-4", titres)

    def test_evenements_futurs_separes_et_revisions(self):
        demain = timezone.localdate() + datetime.timedelta(days=5)
        autre = Devis.objects.create(numero="DEV-CH-OFFRE", client=self.client_tiers, date_creation=timezone.localdate(), statut=Devis.Statut.VALIDE, date_validite=demain)
        c = chronologie(autre)
        self.assertEqual([e.titre for e in c["a_venir"]], ["Fin de validité de l'offre DEV-CH-OFFRE"])
        self.assertTrue(all(not e.futur for e in c["passes"]))

    def test_objet_sans_affaire(self):
        self.assertEqual(evenements(self.client_tiers), [])


class ChronologieFichesTests(_Base):
    def test_affichee_sur_devis_commande_et_facture(self):
        facture = self.facture("FAC-CH-9")
        for nom, objet in (("admin:chiffrage_devis_change", self.commande.devis), ("admin:chiffrage_commande_change", self.commande), ("admin:facturation_facture_change", facture)):
            reponse = self.client.get(reverse(nom, args=[objet.pk]))
            self.assertContains(reponse, "Chronologie de l'affaire", msg_prefix=nom)
            self.assertContains(reponse, "vous êtes ici", msg_prefix=nom)
