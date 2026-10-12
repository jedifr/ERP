import datetime
import xml.etree.ElementTree as ET
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from achats.models import CommandeFournisseur, FactureFournisseur
from chiffrage.models import Commande, Devis
from commercial.models import Adresse, ConditionPaiement, Contact, Tiers
from comptes.models import Societe
from facturation.models import Facture

from . import sepa
from .models import LigneLotVirement, LotVirement, Reglement, reste_a_payer

NS = {"p": sepa.NAMESPACE}
IBAN_SOCIETE = "FR7630006000011234567890189"
IBAN_FOURNISSEUR = "DE89370400440532013000"


class _Fixture:
    def setUp(self):
        self.client_tiers = Tiers.objects.create(
            code="CLI-TRESO", raison_sociale="Client Trésorerie", type_tiers=Tiers.TypeTiers.CLIENT,
            conditions_paiement=ConditionPaiement.objects.create(libelle="30 jours tréso", nombre_jours=30),
        )
        adresse = Adresse.objects.create(
            tiers=self.client_tiers, est_facturation=True, adresse="1 rue", code_postal="75000", ville="Paris"
        )
        devis = Devis.objects.create(numero="DEV-TRESO", client=self.client_tiers, date_creation=datetime.date(2026, 1, 1))
        self.commande = Commande.objects.create(
            numero="CDE-TRESO", devis=devis, client=self.client_tiers, date_commande=datetime.date(2026, 1, 1),
            adresse_facturation=adresse, adresse_livraison=adresse,
        )
        self.facture = Facture.objects.create(
            numero="FAC-TRESO", commande=self.commande, date_facturation=datetime.date(2026, 1, 15),
            montant_ht=Decimal("100"), montant_ttc=Decimal("120"), reference_tiime="TIIME-TRESO",
        )
        self.fournisseur = Tiers.objects.create(
            code="FOU-TRESO", raison_sociale="Aciers Lévêque & Fils", type_tiers=Tiers.TypeTiers.FOURNISSEUR,
            iban=IBAN_FOURNISSEUR, bic="COBADEFFXXX",
        )
        cf = CommandeFournisseur.objects.create(numero="CF-TRESO", fournisseur=self.fournisseur, date_commande=datetime.date(2026, 1, 1))
        self.facture_f = FactureFournisseur.objects.create(
            numero="FF-TRESO", commande_fournisseur=cf, reference_fournisseur="F2026-001",
            date_facture=datetime.date(2026, 1, 20), montant_ht=Decimal("500"), montant_ttc=Decimal("600"),
        )
        self.facture_f2 = FactureFournisseur.objects.create(
            numero="FF-TRESO-2", commande_fournisseur=cf, reference_fournisseur="F2026-002",
            date_facture=datetime.date(2026, 1, 25), montant_ht=Decimal("100"), montant_ttc=Decimal("120.50"),
        )
        societe = Societe.charger()
        societe.raison_sociale = "Découpe H2O"
        societe.iban = IBAN_SOCIETE
        societe.bic = "BDFEFRPPCCT"
        societe.save()

    def _regler(self, montant, **kw):
        reglement = Reglement(montant=Decimal(montant), date_reglement=kw.pop("date", datetime.date(2026, 2, 1)), **kw)
        reglement.full_clean()
        reglement.save()
        return reglement


class ReglementTests(_Fixture, TestCase):
    def test_reglement_partiel_puis_total_d_une_facture_client(self):
        r1 = self._regler("50", facture=self.facture)
        self.facture.refresh_from_db()
        self.assertEqual(r1.sens, Reglement.Sens.ENCAISSEMENT)
        self.assertEqual(self.facture.statut_paiement, Facture.StatutPaiement.PARTIEL)
        self.assertEqual(reste_a_payer(self.facture), Decimal("70.00"))
        self._regler("70", facture=self.facture, date=datetime.date(2026, 2, 10))
        self.facture.refresh_from_db()
        self.assertEqual(self.facture.statut_paiement, Facture.StatutPaiement.PAYE)
        self.assertEqual(self.facture.date_paiement, datetime.date(2026, 2, 10))

    def test_suppression_d_un_reglement_rouvre_la_facture(self):
        reglement = self._regler("120", facture=self.facture)
        reglement.delete()
        self.facture.refresh_from_db()
        self.assertEqual(self.facture.statut_paiement, Facture.StatutPaiement.A_PAYER)
        self.assertIsNone(self.facture.date_paiement)

    def test_trop_percu_refuse(self):
        self._regler("100", facture=self.facture)
        with self.assertRaises(ValidationError) as cm:
            self._regler("20.01", facture=self.facture)
        self.assertIn("Dépasse le reste à payer", str(cm.exception))

    def test_une_seule_facture_par_reglement(self):
        with self.assertRaises(ValidationError):
            self._regler("10")
        with self.assertRaises(ValidationError):
            self._regler("10", facture=self.facture, facture_fournisseur=self.facture_f)

    def test_facture_fournisseur_datee_seulement_une_fois_soldee(self):
        self._regler("200", facture_fournisseur=self.facture_f)
        self.facture_f.refresh_from_db()
        self.assertEqual(self.facture_f.statut_paiement, "Partiellement payée")
        self.assertIsNone(self.facture_f.date_paiement)  # sinon le journal de banque prendrait tout le TTC
        self._regler("400", facture_fournisseur=self.facture_f, date=datetime.date(2026, 2, 5))
        self.facture_f.refresh_from_db()
        self.assertEqual((self.facture_f.statut_paiement, self.facture_f.date_paiement), ("Payée", datetime.date(2026, 2, 5)))

    def test_reglement_dans_le_futur_refuse(self):
        with self.assertRaises(ValidationError):
            self._regler("10", facture=self.facture, date=timezone.localdate() + datetime.timedelta(days=1))


class SepaTests(_Fixture, TestCase):
    def _lot(self):
        lot = LotVirement.objects.create(libelle="Fin janvier", date_execution=timezone.localdate())
        for facture in (self.facture_f, self.facture_f2):
            ligne = LigneLotVirement(lot=lot, facture_fournisseur=facture)
            ligne.full_clean()
            ligne.save()
        return lot

    def test_texte_sepa_retire_accents_et_caracteres_interdits(self):
        self.assertEqual(sepa.texte_sepa("Aciers Lévêque & Fils_ <SARL>", 70), "Aciers Leveque et Fils- SARL")
        self.assertEqual(len(sepa.texte_sepa("x" * 200, 140)), 140)

    def test_fichier_pain001_conforme(self):
        lot = self._lot()
        xml = sepa.generer_fichier(lot)
        lot.refresh_from_db()
        self.assertEqual(lot.statut, LotVirement.Statut.GENERE)
        racine = ET.fromstring(xml.encode())
        self.assertEqual(racine.tag, f"{{{sepa.NAMESPACE}}}Document")
        entete = racine.find("p:pain.001.001.02/p:GrpHdr", NS)
        self.assertEqual(entete.find("p:NbOfTxs", NS).text, "2")
        self.assertEqual(entete.find("p:CtrlSum", NS).text, "720.50")
        self.assertEqual(entete.find("p:MsgId", NS).text, lot.message_id)
        self.assertEqual(lot.message_id, f"LV{timezone.localdate():%y}-{lot.pk:04d}")
        self.assertEqual(entete.find("p:BtchBookg", NS).text, "false")
        self.assertEqual(entete.find("p:Grpg", NS).text, "MIXD")
        self.assertTrue(entete.find("p:CreDtTm", NS).text.endswith("Z"))
        ordre = racine.find("p:pain.001.001.02/p:PmtInf", NS)
        self.assertEqual(ordre.find("p:DbtrAcct/p:Id/p:IBAN", NS).text, IBAN_SOCIETE)
        self.assertEqual(ordre.find("p:Dbtr/p:Nm", NS).text, "DECOUPE H2O")
        self.assertEqual(ordre.find("p:UltmtDbtr/p:Nm", NS).text, "DECOUPE H2O")
        self.assertEqual(ordre.find("p:DbtrAgt/p:FinInstnId/p:BIC", NS).text, "BDFEFRPPCCT")
        self.assertIsNone(ordre.find("p:ChrgBr", NS))
        self.assertEqual(ordre.find("p:ReqdExctnDt", NS).text, timezone.localdate().isoformat())
        virements = ordre.findall("p:CdtTrfTxInf", NS)
        self.assertEqual(len(virements), 2)
        premier = virements[0]
        montant = premier.find("p:Amt/p:InstdAmt", NS)
        self.assertEqual((montant.text, montant.get("Ccy")), ("600.00", "EUR"))
        self.assertEqual(premier.find("p:Cdtr/p:Nm", NS).text, "ACIERS LEVEQUE ET FILS")
        self.assertTrue(premier.find("p:PmtId/p:InstrId", NS).text.startswith(lot.message_id + "/"))
        self.assertEqual(premier.find("p:CdtrAcct/p:Id/p:IBAN", NS).text, IBAN_FOURNISSEUR)
        self.assertEqual(premier.find("p:RmtInf/p:Ustrd", NS).text, "Facture F2026-001")

    def test_regeneration_change_l_identifiant(self):
        lot = self._lot()
        sepa.generer_fichier(lot)
        premier = lot.message_id
        sepa.generer_fichier(lot)
        self.assertEqual(lot.message_id, premier + "-2")
        sepa.generer_fichier(lot)
        self.assertEqual(lot.message_id, premier + "-3")

    def test_generation_refusee_sans_iban_societe(self):
        lot = self._lot()
        Societe.objects.filter(pk=1).update(iban="")
        with self.assertRaises(sepa.VirementError) as cm:
            sepa.generer_fichier(lot)
        self.assertIn("IBAN de la société", str(cm.exception))

    def test_ligne_refusee_si_fournisseur_sans_iban(self):
        Tiers.objects.filter(pk=self.fournisseur.pk).update(iban="")
        lot = LotVirement.objects.create()
        with self.assertRaises(ValidationError) as cm:
            LigneLotVirement(lot=lot, facture_fournisseur=FactureFournisseur.objects.get(pk="FF-TRESO")).full_clean()
        self.assertIn("pas d'IBAN", str(cm.exception))

    def test_facture_dans_un_seul_lot_ouvert(self):
        self._lot()
        autre = LotVirement.objects.create()
        with self.assertRaises(ValidationError):
            LigneLotVirement(lot=autre, facture_fournisseur=self.facture_f).full_clean()

    def test_execution_cree_les_reglements_et_solde_les_factures(self):
        lot = self._lot()
        with self.assertRaises(sepa.VirementError):
            sepa.marquer_execute(lot)  # fichier pas encore généré
        sepa.generer_fichier(lot)
        sepa.marquer_execute(lot)
        lot.refresh_from_db()
        self.assertEqual(lot.statut, LotVirement.Statut.EXECUTE)
        self.assertEqual(lot.reglements.count(), 2)
        self.facture_f.refresh_from_db()
        self.assertEqual(self.facture_f.statut_paiement, "Payée")
        self.assertEqual(self.facture_f.date_paiement, timezone.localdate())
        with self.assertRaises(ValidationError):
            lot.reglements.first().delete()
        with self.assertRaises(ValidationError):
            lot.delete()


class AdminTresorerieTests(_Fixture, TestCase):
    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_superuser("treso", "treso@example.com", "x")
        self.client.force_login(self.user)

    def test_ecrans_s_affichent(self):
        self._regler("20", facture=self.facture)
        for nom in ("tresorerie_echeanceclient", "tresorerie_echeancefournisseur", "tresorerie_reglement",
                    "tresorerie_lotvirement", "facturation_relancefacture"):
            reponse = self.client.get(reverse(f"admin:{nom}_changelist"))
            self.assertEqual(reponse.status_code, 200, nom)
        reponse = self.client.get(reverse("admin:tresorerie_echeanceclient_changelist"))
        self.assertContains(reponse, "FAC-TRESO")
        self.assertContains(reponse, "100,00 €")  # reste à encaisser
        self.assertContains(reponse, "Reste à encaisser")
        reponse = self.client.get(reverse("admin:tresorerie_reglement_add") + "?facture=FAC-TRESO")
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, 'name="montant" value="100"')

    def test_facture_soldee_quitte_l_echeancier(self):
        self._regler("120", facture=self.facture)
        reponse = self.client.get(reverse("admin:tresorerie_echeanceclient_changelist"))
        self.assertNotContains(reponse, "FAC-TRESO")

    def test_relance_depuis_l_echeancier(self):
        Contact.objects.create(tiers=self.client_tiers, nom="Martin", email="compta@client.fr", est_principal=True)
        self.client.post(reverse("admin:tresorerie_echeanceclient_changelist"),
                         {"action": "action_relancer", "_selected_action": ["FAC-TRESO"]})
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(self.facture.relances.count(), 1)

    def test_preparer_generer_executer_un_lot(self):
        reponse = self.client.post(reverse("admin:tresorerie_echeancefournisseur_changelist"),
                                   {"action": "action_preparer_lot", "_selected_action": ["FF-TRESO", "FF-TRESO-2"]})
        lot = LotVirement.objects.get()
        self.assertRedirects(reponse, reverse("admin:tresorerie_lotvirement_change", args=[lot.pk]), fetch_redirect_response=False)
        self.assertEqual(lot.total, Decimal("720.50"))
        self.assertEqual(self.client.get(reverse("admin:tresorerie_lotvirement_change", args=[lot.pk])).status_code, 200)
        reponse = self.client.get(reverse("admin:tresorerie_lotvirement_action_generer", args=[lot.pk]))
        self.assertEqual(reponse["Content-Type"], "application/xml")
        self.assertIn("attachment", reponse["Content-Disposition"])
        self.assertIn(b"pain.001.001.02", reponse.content)
        self.client.get(reverse("admin:tresorerie_lotvirement_action_executer", args=[lot.pk]))
        lot.refresh_from_db()
        self.assertEqual(lot.statut, LotVirement.Statut.EXECUTE)
        reponse = self.client.get(reverse("admin:tresorerie_echeancefournisseur_changelist"))
        self.assertNotContains(reponse, "FF-TRESO")

    def test_solder_depuis_l_echeancier_fournisseur(self):
        self.client.post(reverse("admin:tresorerie_echeancefournisseur_changelist"),
                         {"action": "action_solder", "_selected_action": ["FF-TRESO-2"]})
        self.facture_f2.refresh_from_db()
        self.assertEqual(self.facture_f2.statut_paiement, "Payée")
