"""PDF de la facturation (facture, avoir, relance), bon de commande fournisseur, liens des messages d'erreur de production."""

import datetime
import unittest
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from achats.models import CommandeFournisseur, LigneCommandeFournisseur
from chiffrage.models import Commande, CommandeLigne, Devis
from commercial.models import Adresse, ConditionPaiement, Pays, TauxTVA, Tiers
from comptes.models import Societe
from technique.models import Article

from .models import Facture, FactureLigne


try:
    import pymupdf  # lecture du texte des PDF : outil de test seulement (pas dans requirements.txt)
except ImportError:  # pragma: no cover
    pymupdf = None

lecture_pdf = unittest.skipUnless(pymupdf, "pymupdf absent : le contenu des PDF n'est pas vérifié")


def texte_pdf(contenu):
    import io

    document = pymupdf.open(stream=io.BytesIO(bytes(contenu)), filetype="pdf")
    return "\n".join(page.get_text() for page in document)


class _Base(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("pdf-fact", "p@example.com", "pass-mot-de-passe-31")
        self.client.force_login(self.user)
        Societe.objects.update_or_create(pk=1, defaults={"raison_sociale": "Mon Atelier SAS", "siret": "123 456 789 00011", "iban": "FR76 3000 1000 0000 0000 0000 000"})
        self.pays = Pays.objects.first() or Pays.objects.create(nom="France")
        self.client_tiers = Tiers.objects.create(
            code="CLI-PDF", raison_sociale="Client PDF", type_tiers=Tiers.TypeTiers.CLIENT, siret="98765432100012", numero_tva="FR12345678901",
            conditions_paiement=ConditionPaiement.objects.create(libelle="30 jours net", nombre_jours=30),
        )
        self.adresse = Adresse.objects.create(tiers=self.client_tiers, est_facturation=True, est_livraison=True, adresse="1 rue", code_postal="75000", ville="Paris", pays=self.pays, est_principale=True)
        devis = Devis.objects.create(numero="DEV-PDF", client=self.client_tiers, date_creation=datetime.date(2026, 1, 1))
        self.commande = Commande.objects.create(
            numero="CDE-PDF", devis=devis, client=self.client_tiers, date_commande=datetime.date(2026, 1, 2),
            adresse_facturation=self.adresse, adresse_livraison=self.adresse, reference_client="REF-CLIENT-7",
        )
        self.article = Article.objects.create(reference="PIECE-PDF", libelle="Pièce découpée", nature=Article.Nature.FABRIQUE)
        self.tva = TauxTVA.objects.create(nom="Normal PDF", taux=20)
        self.ligne = CommandeLigne.objects.create(commande=self.commande, article=self.article, quantite_commandee=10, prix_vente_unitaire=100, taux_tva=self.tva)

    def facture(self, numero="FAC-PDF-1", quantite=4, **kw):
        facture = Facture.objects.create(numero=numero, commande=self.commande, date_facturation=datetime.date(2026, 2, 1), **kw)
        FactureLigne.objects.create(facture=facture, commande_ligne=self.ligne, quantite=quantite)
        return facture


@lecture_pdf
class FacturePdfTests(_Base):
    def test_facture_porte_les_mentions_legales(self):
        from .documents import generer_pdf_facture

        texte = texte_pdf(generer_pdf_facture(self.facture()))
        for attendu in ("FACTURE FAC-PDF-1", "01/02/2026", "Échéance : 03/03/2026", "REF-CLIENT-7", "PIECE-PDF", "Pièce découpée", "400,00", "TVA 20 %", "80,00", "480,00",
                        "SIRET 98765432100012", "TVA FR12345678901", "30 jours net", "trois fois le taux d'intérêt légal", "40 €", "IBAN FR76", "Date de livraison"):
            self.assertIn(attendu, texte, attendu)

    def test_avoir_montants_negatifs_et_origine(self):
        from .documents import generer_pdf_facture

        origine = self.facture("FAC-PDF-O")
        avoir = Facture.objects.create(
            numero="AV-PDF-1", commande=self.commande, date_facturation=datetime.date(2026, 2, 5), type_document=Facture.TypeDocument.AVOIR,
            facture_origine=origine, motif="Pièces non conformes",
        )
        FactureLigne.objects.create(facture=avoir, commande_ligne=self.ligne, quantite=1)
        texte = texte_pdf(generer_pdf_facture(avoir))
        for attendu in ("AVOIR AV-PDF-1", "Avoir sur la facture FAC-PDF-O", "Pièces non conformes", "-100,00", "-120,00"):
            self.assertIn(attendu, texte, attendu)
        self.assertNotIn("Échéance", texte)

    def test_mention_d_exoneration_selon_le_regime(self):
        from .documents import generer_pdf_facture

        self.client_tiers.regime_fiscal = Tiers.RegimeFiscal.INTRA_UE
        self.client_tiers.save()
        self.ligne.taux_tva = None
        self.ligne.save()
        texte = texte_pdf(generer_pdf_facture(self.facture("FAC-PDF-UE")))
        self.assertIn("article 262 ter I du CGI", texte)

    def test_refus_sans_ligne_ou_montant_incoherent(self):
        from chiffrage.documents import DocumentError

        from .documents import generer_pdf_facture

        vide = Facture.objects.create(numero="FAC-VIDE", commande=self.commande, date_facturation=datetime.date(2026, 2, 1))
        with self.assertRaisesMessage(DocumentError, "aucune ligne"):
            generer_pdf_facture(vide)
        facture = self.facture("FAC-ECART")
        Facture.objects.filter(pk=facture.pk).update(montant_ht=Decimal("350"))
        facture.refresh_from_db()
        with self.assertRaisesMessage(DocumentError, "diffère"):
            generer_pdf_facture(facture)

    def test_mentions_personnalisees(self):
        from .documents import generer_pdf_facture

        Societe.objects.filter(pk=1).update(mentions_facture="Escompte 2 % sous 8 jours.")
        texte = texte_pdf(generer_pdf_facture(self.facture()))
        self.assertIn("Escompte 2 % sous 8 jours.", texte)
        self.assertNotIn("trois fois", texte)

    def test_actions_pdf_de_la_fiche(self):
        facture = self.facture()
        reponse = self.client.get(f"/admin/facturation/facture/{facture.pk}/pdf/")
        self.assertEqual((reponse.status_code, reponse["Content-Type"]), (200, "application/pdf"))
        fiche = self.client.get(f"/admin/facturation/facture/{facture.pk}/change/")
        self.assertContains(fiche, "PDF de la facture")
        self.assertContains(fiche, "Lettre de relance (PDF)")
        vide = Facture.objects.create(numero="FAC-VIDE2", commande=self.commande, date_facturation=datetime.date(2026, 2, 1))
        refus = self.client.get(f"/admin/facturation/facture/{vide.pk}/pdf/", follow=True)
        self.assertContains(refus, "aucune ligne")


@lecture_pdf
class RelancePdfTests(_Base):
    def test_lettre_de_relance(self):
        from .documents import generer_pdf_relance

        facture = self.facture()
        texte = texte_pdf(generer_pdf_relance(facture))
        self.assertIn("Objet : Rappel : facture FAC-PDF-1", texte)
        self.assertIn("Client PDF", texte)
        self.assertIn("Sauf erreur de notre part", texte)
        self.assertIn("Dernière relance", texte_pdf(generer_pdf_relance(facture, 3)))
        reponse = self.client.get(f"/admin/facturation/facture/{facture.pk}/relance-pdf/?niveau=2")
        self.assertEqual(reponse["Content-Type"], "application/pdf")

    def test_pas_de_relance_pour_un_avoir(self):
        from chiffrage.documents import DocumentError

        from .documents import generer_pdf_relance

        origine = self.facture("FAC-PDF-O2")
        avoir = Facture.objects.create(numero="AV-PDF-2", commande=self.commande, date_facturation=datetime.date(2026, 2, 5), type_document=Facture.TypeDocument.AVOIR, facture_origine=origine, motif="x")
        with self.assertRaises(DocumentError):
            generer_pdf_relance(avoir)


@lecture_pdf
class BonCommandeFournisseurTests(_Base):
    def test_bon_de_commande(self):
        from achats.documents import generer_pdf_commande_fournisseur

        fournisseur = Tiers.objects.create(code="FOU-PDF", raison_sociale="Aciers du Rhône", type_tiers=Tiers.TypeTiers.FOURNISSEUR)
        Adresse.objects.create(tiers=fournisseur, est_livraison=True, adresse="9 quai", code_postal="69000", ville="Lyon", pays=self.pays, est_principale=True)
        commande = CommandeFournisseur.objects.create(numero="CF-PDF-1", fournisseur=fournisseur, date_commande=datetime.date(2026, 3, 1), date_livraison_prevue=datetime.date(2026, 3, 15))
        LigneCommandeFournisseur.objects.create(commande_fournisseur=commande, article=self.article, quantite_commandee=5, prix_unitaire_achat=12)
        LigneCommandeFournisseur.objects.create(commande_fournisseur=commande, designation="Transport", quantite_commandee=1, prix_unitaire_achat=30, poste_gestion=None) if False else None
        texte = texte_pdf(generer_pdf_commande_fournisseur(commande))
        for attendu in ("BON DE COMMANDE CF-PDF-1", "Aciers du Rhône", "Lyon", "Livraison souhaitée le 15/03/2026", "PIECE-PDF", "60,00", "Livrer à", "Mon Atelier SAS"):
            self.assertIn(attendu, texte, attendu)
        reponse = self.client.get(f"/admin/achats/commandefournisseur/{commande.pk}/pdf/")
        self.assertEqual(reponse["Content-Type"], "application/pdf")
        self.assertContains(self.client.get(f"/admin/achats/commandefournisseur/{commande.pk}/change/"), "Bon de commande (PDF)")

    def test_sans_ligne(self):
        from achats.documents import generer_pdf_commande_fournisseur
        from chiffrage.documents import DocumentError

        fournisseur = Tiers.objects.create(code="FOU-PDF2", raison_sociale="F2", type_tiers=Tiers.TypeTiers.FOURNISSEUR)
        commande = CommandeFournisseur.objects.create(numero="CF-VIDE", fournisseur=fournisseur, date_commande=datetime.date(2026, 3, 1))
        with self.assertRaises(DocumentError):
            generer_pdf_commande_fournisseur(commande)


class LiensMessagesErreurTests(_Base):
    def test_adresse_de_facturation_manquante_propose_la_fiche_du_client(self):
        from chiffrage.admin import message_erreur
        from chiffrage.moteur import ChiffrageError
        from chiffrage.production import _adresse_principale

        tiers = Tiers.objects.create(code="CLI-SANS-ADR", raison_sociale="Sans adresse", type_tiers=Tiers.TypeTiers.CLIENT)
        with self.assertRaises(ChiffrageError) as cm:
            _adresse_principale(tiers, "est_facturation", "facturation")
        self.assertEqual(cm.exception.lien[0], f"/admin/commercial/tiers/{tiers.pk}/change/")
        html = str(message_erreur("DEV-1 : ", cm.exception))
        self.assertIn('href="/admin/commercial/tiers/CLI-SANS-ADR/change/"', html)
        self.assertIn("Ajouter l&#x27;adresse de facturation du client", html)

    def test_article_sans_cout_propose_sa_fiche(self):
        from chiffrage.moteur import ChiffrageError, cout_matiere_article

        article = Article.objects.create(reference="ACH-SANS-COUT", nature=Article.Nature.MATIERE_PREMIERE)
        with self.assertRaises(ChiffrageError) as cm:
            cout_matiere_article(article, 1)
        self.assertEqual(cm.exception.lien[0], "/admin/technique/article/ACH-SANS-COUT/change/")

    def test_message_sans_lien_reste_un_texte(self):
        from chiffrage.admin import message_erreur
        from chiffrage.moteur import ChiffrageError

        self.assertEqual(message_erreur("X : ", ChiffrageError("oups")), "X : oups")
