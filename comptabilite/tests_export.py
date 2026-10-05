"""Export vers le comptable (format ISACOMPTA) — données entièrement fictives."""

import datetime
import io
import zipfile
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from achats.generation import generer_ecriture_achat
from achats.models import CommandeFournisseur, FactureFournisseur, LigneCommandeFournisseur
from chiffrage.models import Commande, CommandeLigne, Devis
from commercial.models import Adresse, TauxTVA, Tiers
from facturation.models import Facture
from technique.models import Article

from . import export_comptable, isacompta
from .generation import generer_ecriture_facture
from .models import CompteComptable, ParametresExportComptable, TiersCompteComptable
from .pcg import importer_pcg

EXPORT = datetime.date(2026, 2, 20)


def lignes(texte):
    return texte.split("\r\n")[:-1]


class FormatIsacomptaTests(TestCase):
    def ecriture(self, **extra):
        e = isacompta.Ecriture(journal="VT", date=datetime.date(2025, 12, 1), piece="C25-0001", libelle="FC25-0001 Société Équerre", type_piece="fa", **extra)
        e.mouvements = [
            isacompta.Mouvement("70110", e.libelle, credit=Decimal("100")),
            isacompta.Mouvement("445720", e.libelle, credit=Decimal("20")),
            isacompta.Mouvement("411EQUER", e.libelle, debit=Decimal("120"), tiers=True, echeance=datetime.date(2026, 1, 31)),
        ]
        return e

    def test_largeurs_et_positions(self):
        texte = isacompta.fichier([self.ecriture()], EXPORT)
        self.assertTrue(texte.endswith("\r\n"))
        l = lignes(texte)
        self.assertEqual([len(x) for x in l], [17, 44, 6, 296, 133, 133, 133, 97])
        self.assertEqual(l[0], "VER   02000008550")
        ecr = l[3]
        self.assertEqual((ecr[:3], ecr[6:8], ecr[8:16], ecr[16:24], ecr[24:54].rstrip(), ecr[186:188]), ("ECR", "VT", "01122025", "C25-0001", "FC25-0001 Societe Equerre", "fa"))
        self.assertEqual(ecr[91:109], "0" + "20022026" + "20022026" + "0")
        self.assertEqual((ecr[119:123], ecr[76]), ("0EUR", "1"))
        produit, tva, tiers = l[4], l[5], l[6]
        self.assertEqual(produit[8:16], "  701100"[-8:])  # compte général complété à 6 chiffres, aligné à droite
        self.assertEqual(produit[16:46], "FC25-0001 Societe Equerre".rjust(30))  # libellé aligné à droite
        self.assertEqual((produit[59:72].strip(), produit[46:59].strip(), produit[72:83].strip(), produit[122]), ("100.00", "", "0.00", "0"))
        self.assertEqual((tiers[8:16], tiers[46:59].strip(), tiers[59:72].strip(), tiers[122]), ("411EQUER", "120.00", "", "1"))
        ech = l[7]
        self.assertEqual((ech[:6], ech[6:19].strip(), ech[20:29], ech[29:37]), ("ECHMVT", "120.00", "100.00000", "31012026"))

    def test_ecriture_desequilibree_refusee(self):
        e = self.ecriture()
        e.mouvements[0].credit = Decimal("90")
        with self.assertRaises(isacompta.ErreurFormat):
            isacompta.fichier([e], EXPORT)

    def test_compte_trop_long_refuse(self):
        with self.assertRaises(isacompta.ErreurFormat):
            isacompta.compte("411ABCDEFGH")

    def test_texte_ascii(self):
        self.assertEqual(isacompta.ascii_simple("Équerre – Ø’ç"), "Equerre - O'c".replace("O", " "))


class ExportComptableTests(TestCase):
    def setUp(self):
        importer_pcg()
        self.client_tiers = Tiers.objects.create(code="CLI-EXP", raison_sociale="Société Équerre", type_tiers=Tiers.TypeTiers.CLIENT)
        TiersCompteComptable.objects.create(tiers=self.client_tiers, code_client="EQUER")
        adresse = Adresse.objects.create(tiers=self.client_tiers, est_facturation=True, adresse="1 rue", code_postal="75000", ville="Paris")
        self.article = Article.objects.create(reference="ART-EXP", nature=Article.Nature.MATIERE_PREMIERE)
        self.taux20 = TauxTVA.objects.create(nom="Taux normal export", taux=20)
        devis = Devis.objects.create(numero="DEV-EXP", client=self.client_tiers, date_creation=datetime.date(2026, 1, 1))
        self.commande = Commande.objects.create(
            numero="CDE-EXP", devis=devis, client=self.client_tiers, date_commande=datetime.date(2026, 1, 1),
            adresse_facturation=adresse, adresse_livraison=adresse,
        )
        CommandeLigne.objects.create(commande=self.commande, article=self.article, quantite_commandee=10, prix_vente_unitaire=100, taux_tva=self.taux20)
        self.facture = Facture.objects.create(
            numero="FC26-0042", commande=self.commande, date_facturation=datetime.date(2026, 1, 12), montant_ht=Decimal("1000"), montant_ttc=Decimal("1200"),
        )
        generer_ecriture_facture(self.facture)

        self.fournisseur = Tiers.objects.create(code="FOU-EXP", raison_sociale="Aciers du Nord", type_tiers=Tiers.TypeTiers.FOURNISSEUR)
        TiersCompteComptable.objects.create(tiers=self.fournisseur, code_fournisseur="ACNOR")
        cf = CommandeFournisseur.objects.create(numero="CF-EXP", fournisseur=self.fournisseur, date_commande=datetime.date(2026, 1, 2))
        LigneCommandeFournisseur.objects.create(commande_fournisseur=cf, article=self.article, quantite_commandee=5, prix_unitaire_achat=100, taux_tva=self.taux20)
        self.facture_f = FactureFournisseur.objects.create(
            numero="FF26-0007", commande_fournisseur=cf, date_facture=datetime.date(2026, 1, 20), reference_fournisseur="25/1776 AN",
            montant_ht=Decimal("500"), montant_ttc=Decimal("600"),
        )
        generer_ecriture_achat(self.facture_f)
        self.debut, self.fin = export_comptable.bornes_du_mois(2026, 1)

    def test_bornes_du_mois(self):
        self.assertEqual(export_comptable.bornes_du_mois(2026, 2), (datetime.date(2026, 2, 1), datetime.date(2026, 2, 28)))
        self.assertEqual(export_comptable.bornes_du_mois(2026, 12)[1], datetime.date(2026, 12, 31))

    def test_ventes(self):
        r = export_comptable.exporter(self.debut, self.fin, EXPORT)
        self.assertEqual(r["comptes"], {"ventes": 1, "achats": 1, "banque": 0})
        l = lignes(r["ventes"])
        self.assertEqual([x[:3] for x in l[3:]], ["ECR", "MVT", "MVT", "MVT", "ECH"])  # Produits / TVA / Tiers puis échéance
        ecr = l[3]
        self.assertEqual((ecr[6:8], ecr[8:16], ecr[16:24], ecr[24:54].rstrip()), ("VT", "12012026", "C26-0042", "FC26-0042 Societe Equerre"))
        comptes = [x[8:16].strip() for x in l[4:7]]
        self.assertEqual(comptes, ["706000", "445710", "411EQUER"])
        self.assertEqual((l[6][46:59].strip(), l[6][122]), ("1200.00", "1"))
        self.assertEqual(l[7][29:37], "12012026")  # sans conditions de paiement : échéance = date de facture

    def test_achats(self):
        r = export_comptable.exporter(self.debut, self.fin, EXPORT)
        l = lignes(r["achats"])
        ecr = l[3]
        self.assertEqual((ecr[6:8], ecr[16:24], ecr[24:54].rstrip(), ecr[186:188], ecr[271:286].rstrip()), ("AC", "F26-0007", "FF26-0007 Aciers du Nord", "fa", "25/1776 AN"))
        self.assertEqual([x[8:16].strip() for x in l[4:7]], ["601000", "445660", "401ACNOR"])
        self.assertEqual((l[4][46:59].strip(), l[6][59:72].strip()), ("500.00", "600.00"))

    def test_reglages_de_libelle_et_de_piece(self):
        ParametresExportComptable.objects.update_or_create(pk=1, defaults={
            "libelle_achats": "nom_tiers,code_facture", "piece_achats": "reference_fournisseur", "code_journal_achats": "HA",
        })
        l = lignes(export_comptable.exporter(self.debut, self.fin, EXPORT)["achats"])
        self.assertEqual((l[3][6:8], l[3][16:24], l[3][24:54].rstrip()), ("HA", "/1776 AN", "Aciers du Nord FF26-0007"))

    def test_banque_encaissement_et_reglement(self):
        self.facture.statut_paiement = Facture.StatutPaiement.PAYE
        self.facture.date_paiement = datetime.date(2026, 1, 30)
        self.facture.save()
        self.facture_f.date_paiement = datetime.date(2026, 1, 25)
        self.facture_f.save()
        l = lignes(export_comptable.exporter(self.debut, self.fin, EXPORT)["banque"])
        self.assertEqual([x[:3] for x in l[3:]], ["ECR", "MVT", "MVT", "ECR", "MVT", "MVT", "ECH"])
        # règlement fournisseur du 25/01 puis encaissement client du 30/01
        ecritures = [i for i, x in enumerate(l) if x.startswith("ECR")]
        reglement, encaissement = l[ecritures[0]:ecritures[1]], l[ecritures[1]:]
        self.assertEqual((reglement[0][6:8], reglement[0][8:16], reglement[0][16:24].strip(), reglement[0][24:54].rstrip(), reglement[0][186:188]), ("B2", "25012026", "", "Aciers du Nord", "re"))
        self.assertEqual((reglement[1][8:16].strip(), reglement[1][59:72].strip()), ("512100", "600.00"))  # banque au crédit
        self.assertEqual((reglement[2][8:16].strip(), reglement[2][46:59].strip(), reglement[2][122]), ("401ACNOR", "600.00", "1"))
        self.assertEqual(len(reglement), 3)  # pas d'échéance sur un règlement fournisseur
        self.assertEqual((encaissement[1][8:16].strip(), encaissement[1][46:59].strip()), ("512100", "1200.00"))  # banque au débit
        self.assertEqual((encaissement[2][8:16].strip(), encaissement[2][59:72].strip()), ("411EQUER", "1200.00"))
        self.assertEqual((encaissement[3][:6], encaissement[3][29:37]), ("ECHMVT", "30012026"))

    def test_hors_periode_et_non_regle(self):
        r = export_comptable.exporter(*export_comptable.bornes_du_mois(2026, 2), EXPORT)
        self.assertEqual(r["comptes"], {"ventes": 0, "achats": 0, "banque": 0})
        self.assertEqual(len(lignes(r["ventes"])), 3)  # en-têtes seulement

    def test_compte_trop_long_signale(self):
        compte = CompteComptable.objects.create(code="4110ABCDEF", libelle="Trop long")
        TiersCompteComptable.objects.filter(tiers=self.client_tiers).update(compte_client=compte)
        from .models import LigneEcriture

        LigneEcriture.objects.filter(ecriture__facture=self.facture, compte__code="411EQUER").update(compte=compte)
        with self.assertRaises(export_comptable.ErreurExport):
            export_comptable.exporter(self.debut, self.fin, EXPORT)

    def test_admin_apercu_et_zip(self):
        user = get_user_model().objects.create_superuser("export-admin", "e@example.com", "pass-mot-de-passe-19")
        self.client.force_login(user)
        url = "/admin/comptabilite/ecriturecomptable/export-comptable/"
        page = self.client.get(url + "?mois=2026-01")
        self.assertContains(page, "vente_01-2026.txt")
        self.assertContains(page, "Télécharger")
        reponse = self.client.post(url, {"mois": "2026-01"})
        self.assertEqual(reponse["Content-Type"], "application/zip")
        z = zipfile.ZipFile(io.BytesIO(reponse.content))
        self.assertEqual(sorted(z.namelist()), ["achat_01-2026.txt", "banque_01-2026.txt", "vente_01-2026.txt"])
        self.assertTrue(z.read("vente_01-2026.txt").startswith(b"VER   02000008550\r\nDOS"))
        self.assertContains(self.client.get("/admin/comptabilite/ecriturecomptable/export-comptable/?mois=pas-un-mois", follow=True), "Choisissez un mois")
        self.assertContains(self.client.get("/admin/comptabilite/parametresexportcomptable/", follow=True), "Code du journal des ventes")

    def test_commande_de_gestion(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as dossier:
            call_command("export_comptable", "2026-01", dossier, stdout=io.StringIO())
            self.assertEqual(sorted(p.name for p in Path(dossier).iterdir()), ["achat_01-2026.txt", "banque_01-2026.txt", "vente_01-2026.txt"])
