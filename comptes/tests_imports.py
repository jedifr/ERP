import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from achats.imports import IMPORT_TARIFS
from achats.models import ArticleFournisseur, TarifAchatArticle
from commercial.imports import IMPORT_CONTACTS, IMPORT_TIERS
from commercial.models import Contact, Tiers
from comptes import lots
from comptes.imports import executer, lire, normaliser, rapprocher
from comptes.models import ProfilImportColonnes
from technique.imports import IMPORT_ARTICLES, IMPORT_NOMENCLATURES
from technique.models import Article, Matiere, Nomenclature


def champs(spec, texte, profil=None):
    entetes, lignes = lire(texte)
    return [(p["champ"], p["niveau"]) for p in rapprocher(entetes, lignes, spec.champs, profil)]


class RapprochementTests(TestCase):
    def test_normalisation(self):
        self.assertEqual(normaliser(" N° SIRET "), "n siret")
        self.assertEqual(normaliser("Raison_Sociale"), "raison sociale")

    def test_export_csv_de_l_erp_relu(self):
        # En-têtes de l'export CSV de la liste des tiers : libellés des champs.
        self.assertEqual(champs(IMPORT_TIERS, "Code;Raison sociale;Type de tiers;Régime fiscal;Devise;SIRET\nC1;A;Client;France;EUR;1"), [
            ("code", "exact"), ("raison_sociale", "exact"), ("type_tiers", "exact"), ("regime_fiscal", "exact"), ("devise", "exact"), ("siret", "exact"),
        ])

    def test_synonymes_approchants_et_colonnes_inconnues(self):
        texte = "Compte\tLibellé\tIdentifiant TVA\tMode de règlement\tSolde\tN° Siret\n411A\tDupont\tFR12345678901\t30 jours\t1,00\t12345678900011"
        self.assertEqual(champs(IMPORT_TIERS, texte), [
            ("code", "synonyme"), ("raison_sociale", "synonyme"), ("numero_tva", "synonyme"), ("conditions_paiement", "synonyme"), ("", ""), ("siret", "synonyme"),
        ])
        self.assertEqual(champs(IMPORT_TIERS, "Raison socile;Cde\nDupont;X")[0], ("raison_sociale", "approchant"))

    def test_reconnaissance_d_apres_le_contenu(self):
        texte = "A;B;C;D\nCLI-1;Soudure Martin;12345678900011;FR55441236987\nCLI-2;Bennes;80245117300027;FR09552100554"
        self.assertEqual(champs(IMPORT_TIERS, texte)[2:], [("siret", "contenu"), ("numero_tva", "contenu")])

    def test_un_champ_par_colonne(self):
        # Deux colonnes « Nom » et « Société » visent la raison sociale : la mieux notée la garde, l'autre est ignorée.
        resultat = champs(IMPORT_TIERS, "Code;Raison sociale;Nom\nC;A;B")
        self.assertEqual(resultat, [("code", "exact"), ("raison_sociale", "exact"), ("", "")])

    def test_profil_memorise_prioritaire(self):
        profil = ProfilImportColonnes(nom="Compta", cible="tiers", correspondance={"libelle": "code", "compte": ""})
        self.assertEqual(champs(IMPORT_TIERS, "Compte;Libellé;Nom\n1;2;3", profil), [("", ""), ("code", "profil"), ("raison_sociale", "synonyme")])


class ImportsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("imp", "i@example.com", "pass-mot-de-passe-51")
        self.client.force_login(self.user)
        self.fournisseur = Tiers.objects.create(code="FOU-1", raison_sociale="Aciers du Rhône", type_tiers="fournisseur")
        Matiere.objects.create(nom="S235-I", densite=7.85)

    def importer(self, spec, texte, defauts=None, essai=False):
        entetes, lignes = lire(texte)
        correspondance = [p["champ"] for p in rapprocher(entetes, lignes, spec.champs)]
        return executer(spec, lignes, correspondance, defauts or {}, self.user, essai=essai)

    def test_essai_a_blanc_n_ecrit_rien(self):
        rapport = self.importer(IMPORT_TIERS, "code;nom\nCLI-9;Neuf", essai=True)
        self.assertEqual(rapport["crees"], 1)
        self.assertFalse(Tiers.objects.filter(pk="CLI-9").exists())
        self.assertIsNone(rapport["lot"])

    def test_articles_avec_nature_imposee(self):
        texte = "Référence;Désignation;Matière;Ép.;Unité;Prix achat\nT-3;Tôle 3;S235-I;3;kg;0,95\nT-X;Tôle X;Inconnue;3;kg;1"
        rapport = self.importer(IMPORT_ARTICLES, texte, {"nature": "matiere_premiere"})
        self.assertEqual((rapport["crees"], rapport["ignores"]), (1, 1))
        article = Article.objects.get(pk="T-3")
        self.assertEqual((article.unite_cout, article.cout_unitaire, article.epaisseur), ("poids", Decimal("0.95"), 3))
        self.assertIn("matière", rapport["lignes"][1][3])
        rapport = self.importer(IMPORT_ARTICLES, "ref;libelle\nT-4;Sans nature")
        self.assertIn("nature manquante", rapport["lignes"][0][3])

    def test_contacts_et_telephones_annulables(self):
        rapport = self.importer(IMPORT_CONTACTS, "Client;Nom;Prénom;Mobile;E-mail\nAciers du Rhône;Martin;Julie;06 12 34 56 78;j@a.fr\nFOU-1;Martin;Julie;;")
        self.assertEqual((rapport["crees"], rapport["ignores"]), (1, 1))
        contact = Contact.objects.get(nom="Martin")
        self.assertEqual(contact.telephones.get().numero, "06 12 34 56 78")
        lots.annuler(rapport["lot"])
        self.assertFalse(Contact.objects.exists())

    def test_nomenclature(self):
        Article.objects.create(reference="CH-1", nature="fabrique")
        Article.objects.create(reference="T-1", nature="matiere_premiere")
        rapport = self.importer(IMPORT_NOMENCLATURES, "Parent;Composant;Qté;Longueur\nCH-1;T-1;2;500\nCH-1;T-1;1;100")
        self.assertEqual((rapport["crees"], rapport["ignores"]), (1, 1))
        self.assertEqual(Nomenclature.objects.get().longueur_mm, 500)

    def test_tarif_fournisseur_clot_l_ancien_et_s_annule(self):
        article = Article.objects.create(reference="T-1", nature="matiere_premiere")
        lien = ArticleFournisseur.objects.create(article=article, fournisseur=self.fournisseur)
        ancien = TarifAchatArticle.objects.create(article_fournisseur=lien, prix_unitaire=Decimal("1"), date_debut=datetime.date(2026, 1, 1))
        rapport = self.importer(IMPORT_TARIFS, "Article;Fournisseur;Prix;Date\nT-1;FOU-1;1,10;01/11/2026")
        self.assertEqual(rapport["crees"], 1)
        ancien.refresh_from_db()
        self.assertEqual(ancien.date_fin, datetime.date(2026, 10, 31))
        lots.annuler(rapport["lot"])
        ancien.refresh_from_db()
        self.assertIsNone(ancien.date_fin)
        self.assertEqual(TarifAchatArticle.objects.count(), 1)

    def test_ecran_complet_et_profil(self):
        url = "/admin/commercial/tiers/importer/"
        texte = "N° client;Société;Monnaie\nCLI-7;Sept;EUR"
        page = self.client.post(url, {"tableau": texte, "etape": "donnees"})
        self.assertContains(page, "Correspondance des colonnes")
        self.assertContains(page, "Synonyme")
        donnees = {"tableau": texte, "col_0": "code", "col_1": "raison_sociale", "col_2": "devise", "memoriser": "1", "nom_profil": "Fichier X"}
        page = self.client.post(url, {**donnees, "etape": "importer"})
        self.assertContains(page, "1 ligne(s) importée(s)")
        self.assertTrue(ProfilImportColonnes.objects.filter(cible="tiers", nom="Fichier X").exists())
        page = self.client.post(url, {"tableau": texte, "etape": "donnees"})
        self.assertContains(page, "Profil « <strong>Fichier X</strong> » appliqué", html=False)
        for chemin in ("commercial/adresse", "commercial/contact", "technique/article", "technique/matiere", "technique/nomenclature",
                       "achats/articlefournisseur", "achats/tarifachatarticle"):
            self.assertContains(self.client.get(f"/admin/{chemin}/importer/"), "Analyser les colonnes")

    def test_colonne_obligatoire_manquante_bloque(self):
        page = self.client.post("/admin/commercial/tiers/importer/", {"tableau": "Code;Solde\nC;1", "etape": "verifier", "col_0": "code", "col_1": ""})
        self.assertContains(page, "Champ obligatoire sans colonne")
        self.assertFalse(Tiers.objects.filter(pk="C").exists())
