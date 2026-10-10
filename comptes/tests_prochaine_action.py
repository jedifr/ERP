"""Colonne « Prochaine action » des listes."""

import datetime

from django.urls import reverse
from django.utils import timezone

from chiffrage.models import Commande, Devis
from facturation.models import Facture, RelanceFacture
from facturation.tests_pdf import _Base

from .prochaine_action import RIEN, action_commande, action_devis, action_facture, action_livraison


class ProchaineActionTests(_Base):
    def devis(self, **kw):
        base = {"numero": "DEV-PA", "client": self.client_tiers, "date_creation": datetime.date(2026, 1, 1)}
        base.update(kw)
        return Devis.objects.create(**base)

    def test_devis_brouillon_sans_puis_avec_lignes(self):
        d = self.devis()
        self.assertEqual(action_devis(d).texte, "Ajouter des lignes")
        from chiffrage.models import DevisLigne

        DevisLigne.objects.create(devis=d, article=self.article, quantite=1)
        self.assertEqual(action_devis(d).texte, "Terminer et valider le devis")

    def test_devis_envoye_selon_la_validite(self):
        aujourdhui = timezone.localdate()
        valide = Devis.Statut.VALIDE
        expire = self.devis(statut=valide, date_validite=aujourdhui - datetime.timedelta(days=2))
        self.assertEqual((action_devis(expire).texte, action_devis(expire).ton), ("Offre expirée : relancer ou refuser", "ko"))
        proche = self.devis(numero="DEV-PA-2", statut=valide, date_validite=aujourdhui + datetime.timedelta(days=3))
        self.assertEqual(action_devis(proche).ton, "attention")
        self.assertIn("Relancer le client avant le", action_devis(proche).texte)
        loin = self.devis(numero="DEV-PA-3", statut=valide, date_validite=aujourdhui + datetime.timedelta(days=30))
        self.assertEqual(action_devis(loin).ton, "normal")
        sans_limite = self.devis(numero="DEV-PA-4", statut=valide)
        Devis.objects.filter(pk=sans_limite.pk).update(date_validite=None)  # la validation pose 30 jours par défaut : on les retire
        sans_limite.refresh_from_db()
        self.assertEqual(action_devis(sans_limite).texte, "Attendre la réponse du client")

    def test_devis_accepte_refuse_remplace(self):
        valide = Devis.Statut.VALIDE
        accepte = self.devis(numero="DEV-PA-5", statut=valide, issue=Devis.Issue.ACCEPTE)
        self.assertEqual(action_devis(accepte).texte, "Créer la commande")
        avec_commande = self.commande.devis  # le devis de la commande du décor
        Devis.objects.filter(pk=avec_commande.pk).update(statut=valide, issue=Devis.Issue.ACCEPTE)
        avec_commande.refresh_from_db()
        self.assertEqual(action_devis(avec_commande), ("Commande en cours", "calme"))
        for issue in (Devis.Issue.REFUSE, Devis.Issue.REMPLACE):
            self.assertEqual(action_devis(self.devis(numero=f"DEV-PA-{issue}", statut=valide, issue=issue)), RIEN)

    def test_facture_non_payee_en_retard_puis_payee(self):
        facture = self.facture("FAC-PA-1")
        action = action_facture(facture)
        self.assertEqual(action.ton, "ko" if facture.est_en_retard else "normal")
        facture.date_facturation = datetime.date(2025, 1, 1)  # échéance largement dépassée
        facture.save()
        facture.refresh_from_db()
        self.assertEqual(action_facture(facture).texte.split(" (")[0], "Envoyer un rappel")
        RelanceFacture.objects.create(facture=facture, niveau=1, destinataire="c@x.fr", objet="o", corps="c")
        self.assertEqual(action_facture(facture).texte.split(" (")[0], "Envoyer la relance")
        facture.statut_paiement = Facture.StatutPaiement.PAYE
        facture.save()
        self.assertEqual(action_facture(facture), RIEN)

    def test_avoir_sans_action(self):
        origine = self.facture("FAC-PA-2")
        avoir = Facture.objects.create(numero="AV-PA", commande=self.commande, date_facturation=datetime.date(2026, 2, 5), type_document=Facture.TypeDocument.AVOIR, facture_origine=origine, motif="x")
        self.assertEqual(action_facture(avoir), RIEN)

    def test_commande_et_livraison(self):
        self.assertNotEqual(action_commande(self.commande), RIEN)  # une commande en cours a toujours une suite
        self.commande.statut = Commande.Statut.SOLDEE
        self.commande.save()
        self.assertEqual(action_commande(self.commande), RIEN)

    def test_les_listes_affichent_la_colonne(self):
        from chiffrage.models import Livraison

        self.facture("FAC-PA-3")
        Livraison.objects.create(numero="BL-PA-1", commande=self.commande, date_livraison=datetime.date(2026, 1, 20))
        for nom in ("admin:chiffrage_devis_changelist", "admin:chiffrage_commande_changelist", "admin:chiffrage_livraison_changelist", "admin:facturation_facture_changelist"):
            reponse = self.client.get(reverse(nom))
            self.assertEqual(reponse.status_code, 200, nom)
            self.assertContains(reponse, "Prochaine action", msg_prefix=nom)
        self.assertContains(self.client.get(reverse("admin:facturation_facture_changelist")), 'class="pa pa-')
