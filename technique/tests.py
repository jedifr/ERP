from decimal import Decimal
import datetime

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from .models import Article, Gamme, Matiere, Nomenclature, PosteTravail, TarifPoste
from .services import DuplicationError, dupliquer_article, renommer_article


class ArticleTests(TestCase):
    def test_gere_en_stock_default_matiere_premiere(self):
        acier = Matiere.objects.create(nom="Acier", densite=7.85)
        article = Article.objects.create(
            reference="TOLE-S235-3MM",
            nature=Article.Nature.MATIERE_PREMIERE,
            matiere=acier,
            unite_cout=Article.UniteCout.POIDS,
            epaisseur=3,
            cout_unitaire=1.2,
        )
        self.assertTrue(article.gere_en_stock)

    def test_gere_en_stock_default_fabrique(self):
        article = Article.objects.create(
            reference="PIECE-001",
            nature=Article.Nature.FABRIQUE,
        )
        self.assertFalse(article.gere_en_stock)

    def test_gere_en_stock_peut_etre_force(self):
        article = Article.objects.create(
            reference="PIECE-002",
            nature=Article.Nature.FABRIQUE,
            gere_en_stock=True,
        )
        self.assertTrue(article.gere_en_stock)

    def test_article_fabrique_sans_cout_unitaire(self):
        article = Article(reference="PIECE-003", nature=Article.Nature.FABRIQUE, cout_unitaire=10)
        with self.assertRaises(ValidationError):
            article.full_clean()


class TarifPosteTests(TestCase):
    def setUp(self):
        self.poste = PosteTravail.objects.create(
            nom="Mazak", mode_calcul=PosteTravail.ModeCalcul.HORAIRE, nombre_machines=1
        )

    def test_chevauchement_refuse(self):
        TarifPoste.objects.create(
            poste=self.poste,
            cout_horaire=60,
            date_debut=datetime.date(2025, 1, 1),
            date_fin=datetime.date(2025, 12, 31),
        )
        chevauchant = TarifPoste(
            poste=self.poste,
            cout_horaire=65,
            date_debut=datetime.date(2025, 6, 1),
        )
        with self.assertRaises(ValidationError):
            chevauchant.full_clean()

    def test_periodes_consecutives_acceptees(self):
        TarifPoste.objects.create(
            poste=self.poste,
            cout_horaire=60,
            date_debut=datetime.date(2025, 1, 1),
            date_fin=datetime.date(2025, 12, 31),
        )
        suivant = TarifPoste(
            poste=self.poste,
            cout_horaire=65,
            date_debut=datetime.date(2026, 1, 1),
        )
        suivant.full_clean()  # ne doit pas lever d'exception


class NomenclatureTests(TestCase):
    def test_parent_doit_etre_fabrique(self):
        matiere_premiere = Article.objects.create(
            reference="TOLE-01", nature=Article.Nature.MATIERE_PREMIERE
        )
        composant = Article.objects.create(reference="TOLE-02", nature=Article.Nature.MATIERE_PREMIERE)
        ligne = Nomenclature(article_parent=matiere_premiere, article_composant=composant, quantite=1)
        with self.assertRaises(ValidationError):
            ligne.full_clean()

    def test_composant_ne_peut_pas_etre_le_parent(self):
        article = Article.objects.create(reference="PIECE-010", nature=Article.Nature.FABRIQUE)
        ligne = Nomenclature(article_parent=article, article_composant=article, quantite=1)
        with self.assertRaises(ValidationError):
            ligne.full_clean()

    def test_ligne_valide(self):
        parent = Article.objects.create(reference="PIECE-011", nature=Article.Nature.FABRIQUE)
        composant = Article.objects.create(reference="TOLE-03", nature=Article.Nature.MATIERE_PREMIERE)
        ligne = Nomenclature(
            article_parent=parent, article_composant=composant, quantite=2, longueur_mm=500, largeur_mm=300
        )
        ligne.full_clean()  # ne doit pas lever d'exception


class GammeTests(TestCase):
    def setUp(self):
        self.article = Article.objects.create(reference="PIECE-020", nature=Article.Nature.FABRIQUE)
        self.poste_horaire = PosteTravail.objects.create(
            nom="Tour", mode_calcul=PosteTravail.ModeCalcul.HORAIRE
        )
        self.poste_forfaitaire = PosteTravail.objects.create(
            nom="Sous-Traitance", mode_calcul=PosteTravail.ModeCalcul.FORFAITAIRE
        )

    def test_article_doit_etre_fabrique(self):
        matiere_premiere = Article.objects.create(reference="TOLE-04", nature=Article.Nature.MATIERE_PREMIERE)
        etape = Gamme(
            article=matiere_premiere,
            poste=self.poste_horaire,
            ordre=1,
            temps_fixe=10,
            temps_variable=2,
            date_debut=datetime.date(2025, 1, 1),
        )
        with self.assertRaises(ValidationError):
            etape.full_clean()

    def test_mode_horaire_requiert_temps(self):
        etape = Gamme(
            article=self.article,
            poste=self.poste_horaire,
            ordre=1,
            date_debut=datetime.date(2025, 1, 1),
        )
        with self.assertRaises(ValidationError):
            etape.full_clean()

    def test_mode_forfaitaire_requiert_cout(self):
        etape = Gamme(
            article=self.article,
            poste=self.poste_forfaitaire,
            ordre=2,
            date_debut=datetime.date(2025, 1, 1),
        )
        with self.assertRaises(ValidationError):
            etape.full_clean()

    def test_etape_horaire_valide(self):
        etape = Gamme(
            article=self.article,
            poste=self.poste_horaire,
            ordre=1,
            temps_fixe=15,
            temps_variable=3,
            date_debut=datetime.date(2025, 1, 1),
        )
        etape.full_clean()  # ne doit pas lever d'exception

    def test_revision_chevauchante_refusee(self):
        Gamme.objects.create(
            article=self.article,
            poste=self.poste_horaire,
            ordre=1,
            temps_fixe=15,
            temps_variable=3,
            date_debut=datetime.date(2025, 1, 1),
            date_fin=datetime.date(2025, 6, 30),
        )
        revision = Gamme(
            article=self.article,
            poste=self.poste_horaire,
            ordre=1,
            temps_fixe=18,
            temps_variable=4,
            date_debut=datetime.date(2025, 3, 1),
        )
        with self.assertRaises(ValidationError):
            revision.full_clean()


class DupliquerArticleTests(TestCase):
    def test_duplique_matiere_premiere_avec_reference_generee(self):
        acier = Matiere.objects.create(nom="Acier-Dup", densite=7.85)
        article = Article.objects.create(
            reference="TOLE-DUP",
            libelle="Tôle laminée à froid",
            nature=Article.Nature.MATIERE_PREMIERE,
            matiere=acier,
            unite_cout=Article.UniteCout.POIDS,
            epaisseur=3,
            cout_unitaire=1.2,
            stock_mini=10,
        )
        copie = dupliquer_article(article)
        self.assertEqual(copie.reference, "TOLE-DUP-COPIE")
        self.assertEqual(copie.libelle, "Tôle laminée à froid")
        self.assertEqual(copie.nature, article.nature)
        self.assertEqual(copie.matiere, acier)
        self.assertEqual(copie.cout_unitaire, Decimal("1.2"))
        self.assertEqual(copie.stock_mini, 10)
        self.assertNotEqual(copie.pk, article.pk)

    def test_references_successives_incrementees(self):
        Article.objects.create(reference="TOLE-DUP2", nature=Article.Nature.MATIERE_PREMIERE)
        Article.objects.create(reference="TOLE-DUP2-COPIE", nature=Article.Nature.MATIERE_PREMIERE)
        original = Article.objects.get(reference="TOLE-DUP2")
        copie = dupliquer_article(original)
        self.assertEqual(copie.reference, "TOLE-DUP2-COPIE-2")

    def test_duplique_nomenclature_et_gamme_article_fabrique(self):
        composant = Article.objects.create(
            reference="VIS-DUP",
            nature=Article.Nature.MATIERE_PREMIERE,
            unite_cout=Article.UniteCout.PIECE,
            cout_unitaire=0.1,
        )
        poste = PosteTravail.objects.create(
            nom="Poste-Dup", mode_calcul=PosteTravail.ModeCalcul.FORFAITAIRE
        )
        parent = Article.objects.create(
            reference="PIECE-DUP", nature=Article.Nature.FABRIQUE, taux_marge_defaut=15
        )
        Nomenclature.objects.create(
            article_parent=parent,
            article_composant=composant,
            quantite=3,
            longueur_mm=100,
            largeur_mm=50,
        )
        Gamme.objects.create(
            article=parent, poste=poste, ordre=1, cout_forfaitaire=25, date_debut=datetime.date(2025, 1, 1)
        )

        copie = dupliquer_article(parent)

        self.assertEqual(copie.composants.count(), 1)
        ligne = copie.composants.get()
        self.assertEqual(ligne.article_composant, composant)
        self.assertEqual(ligne.quantite, 3)
        self.assertEqual(ligne.longueur_mm, 100)
        self.assertEqual(ligne.largeur_mm, 50)

        self.assertEqual(copie.gamme_etapes.count(), 1)
        etape = copie.gamme_etapes.get()
        self.assertEqual(etape.poste, poste)
        self.assertEqual(etape.cout_forfaitaire, 25)

    def test_originale_non_modifiee(self):
        article = Article.objects.create(
            reference="TOLE-DUP3", nature=Article.Nature.MATIERE_PREMIERE, cout_unitaire=5
        )
        dupliquer_article(article)
        article.refresh_from_db()
        self.assertEqual(article.reference, "TOLE-DUP3")
        self.assertEqual(article.cout_unitaire, 5)


class DupliquerArticleViewTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        self.user = User.objects.create_superuser("dup-admin", "d@example.com", "pass1234")
        self.client.force_login(self.user)

    def test_post_duplique_et_redirige_vers_la_copie(self):
        Article.objects.create(
            reference="TOLE-DUP-VIEW", nature=Article.Nature.MATIERE_PREMIERE, cout_unitaire=2
        )
        response = self.client.post("/admin/technique/article/TOLE-DUP-VIEW/dupliquer/")
        self.assertRedirects(response, "/admin/technique/article/TOLE-DUP-VIEW-COPIE/change/")
        self.assertTrue(Article.objects.filter(pk="TOLE-DUP-VIEW-COPIE").exists())

    def test_article_introuvable_404(self):
        response = self.client.post("/admin/technique/article/INEXISTANT/dupliquer/")
        self.assertEqual(response.status_code, 404)

    def test_get_refuse(self):
        Article.objects.create(reference="TOLE-DUP-VIEW2", nature=Article.Nature.MATIERE_PREMIERE)
        response = self.client.get("/admin/technique/article/TOLE-DUP-VIEW2/dupliquer/")
        self.assertEqual(response.status_code, 405)

    def test_anonyme_refuse(self):
        self.client.logout()
        Article.objects.create(reference="TOLE-DUP-VIEW3", nature=Article.Nature.MATIERE_PREMIERE)
        response = self.client.post("/admin/technique/article/TOLE-DUP-VIEW3/dupliquer/")
        self.assertNotEqual(response.status_code, 200)


class FicheArticleDeuxColonnesTests(TestCase):
    """Fiche article : saisie à gauche ; composition, prix d'achat, stock, utilisation et activité à droite."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        self.client.force_login(get_user_model().objects.create_superuser("fiche-art", "f@example.com", "pass1234"))
        self.tole = Article.objects.create(
            reference="TOLE-RECAP", libelle="Tôle recap", nature=Article.Nature.MATIERE_PREMIERE,
            unite_cout=Article.UniteCout.PIECE, cout_unitaire=Decimal("12.5"), stock_mini=10, gere_en_stock=True,
        )
        self.piece = Article.objects.create(reference="PIECE-RECAP", libelle="Pièce recap", nature=Article.Nature.FABRIQUE)
        Nomenclature.objects.create(article_parent=self.piece, article_composant=self.tole, quantite=2)

    def url(self, article):
        return f"/admin/technique/article/{article.pk}/change/"

    def test_article_achete(self):
        from achats.models import ArticleFournisseur, TarifAchatArticle
        from commercial.models import Tiers
        from stock.models import AlerteStock, Emplacement, Lot

        fournisseur = Tiers.objects.create(code="FOUR-RECAP", raison_sociale="Aciers Martin", type_tiers=Tiers.TypeTiers.FOURNISSEUR)
        lien = ArticleFournisseur.objects.create(article=self.tole, fournisseur=fournisseur)
        TarifAchatArticle.objects.create(article_fournisseur=lien, prix_unitaire=Decimal("11.2"), date_debut=datetime.date(2020, 1, 1))
        Lot.objects.create(article=self.tole, emplacement=Emplacement.objects.create(code="R1"), quantite=4)
        AlerteStock.objects.create(article=self.tole)
        page = self.client.get(self.url(self.tole))
        for attendu in ("fiche-saisie", "fiche-recap", "Article acheté", "Coût retenu : 12,5000 €", "Aciers Martin : 11,2000 €",
                        "En stock : 4", "Seuil d&#x27;alerte : 10", "Alerte de réapprovisionnement active",
                        "PIECE-RECAP", "× 2", "0 ligne(s) de devis", "0 ligne(s) de commande"):
            self.assertContains(page, attendu)
        self.assertContains(page, "/admin/technique/article/PIECE-RECAP/change/")

    def test_article_fabrique(self):
        page = self.client.get(self.url(self.piece))
        for attendu in ("1 composant(s) en nomenclature", "0 étape(s) de gamme", "À compléter avant de pouvoir chiffrer",
                        "coût recalculé à chaque devis", "Article non géré en stock", "Aucune nomenclature",
                        "0 ordre(s) de fabrication"):
            self.assertContains(page, attendu)

    @override_settings(STOCK_ACTIF=False)
    def test_sans_gestion_de_stock_pas_de_bloc_stock(self):
        page = self.client.get(self.url(self.tole))
        self.assertNotContains(page, "En stock :")
        self.assertNotContains(page, "Seuil d")
        self.assertContains(page, "fiche-recap")

    def test_formulaire_d_ajout_et_enregistrement(self):
        self.assertContains(self.client.get("/admin/technique/article/add/"), "fiche-recap")
        page = self.client.get(self.url(self.tole))
        donnees = {
            "reference": "TOLE-RECAP", "libelle": "Tôle modifiée", "nature": "matiere_premiere", "unite_cout": "piece",
            "cout_unitaire": "12.5", "gere_en_stock": "on", "stock_mini": "10",
            "composants-TOTAL_FORMS": "0", "composants-INITIAL_FORMS": "0",
            "gamme_etapes-TOTAL_FORMS": "0", "gamme_etapes-INITIAL_FORMS": "0",
            "fournisseurs-TOTAL_FORMS": "0", "fournisseurs-INITIAL_FORMS": "0",
            "compte_vente_override-TOTAL_FORMS": "0", "compte_vente_override-INITIAL_FORMS": "0",
            "compte_achat_override-TOTAL_FORMS": "0", "compte_achat_override-INITIAL_FORMS": "0",
        }
        reponse = self.client.post(self.url(self.tole), donnees)
        self.assertEqual(reponse.status_code, 302)
        self.tole.refresh_from_db()
        self.assertEqual(self.tole.libelle, "Tôle modifiée")


class RenommerArticleTests(TestCase):
    """Renommer un article change sa clé : tout ce qui s'y rattache (et l'historique) suit ; la référence n'est plus modifiable dans le formulaire."""

    def setUp(self):
        import datetime

        from django.contrib.auth import get_user_model

        from chiffrage.models import Devis, DevisLigne
        from commercial.models import Tiers

        self.user = get_user_model().objects.create_superuser("renom", "r@example.com", "pass-mot-de-passe-41")
        self.client.force_login(self.user)
        self.matiere = Matiere.objects.create(nom="S235-R", densite=7.85)
        self.tole = Article.objects.create(reference="TOLE-A", libelle="Tôle", nature=Article.Nature.MATIERE_PREMIERE, matiere=self.matiere, epaisseur=3, unite_cout="poids", cout_unitaire=1)
        self.parent = Article.objects.create(reference="PIECE-R", nature=Article.Nature.FABRIQUE)
        Nomenclature.objects.create(article_parent=self.parent, article_composant=self.tole, longueur_mm=100, largeur_mm=50, quantite=1)
        tiers = Tiers.objects.create(code="CLI-R", raison_sociale="Client", type_tiers=Tiers.TypeTiers.CLIENT)
        devis = Devis.objects.create(numero="DEV-R", client=tiers, date_creation=datetime.date(2026, 1, 1))
        self.ligne = DevisLigne.objects.create(devis=devis, article=self.tole, quantite=2)

    def test_les_liens_et_l_historique_suivent(self):
        from chiffrage.models import DevisLigne

        nouveau = renommer_article(self.tole, "TOLE-B")
        self.assertEqual((nouveau.pk, nouveau.libelle, nouveau.epaisseur, nouveau.cout_unitaire), ("TOLE-B", "Tôle", 3, 1))
        self.assertFalse(Article.objects.filter(pk="TOLE-A").exists())
        self.assertEqual(Nomenclature.objects.get(article_parent=self.parent).article_composant_id, "TOLE-B")
        self.assertEqual(DevisLigne.objects.get(pk=self.ligne.pk).article_id, "TOLE-B")
        self.assertFalse(DevisLigne.history.filter(article_id="TOLE-A").exists())
        self.assertTrue(DevisLigne.history.filter(article_id="TOLE-B").exists())

    def test_refus_reference_vide_identique_ou_existante(self):
        for valeur, message in (("  ", "Indiquez"), ("TOLE-A", "identique"), ("PIECE-R", "existe déjà"), ("X" * 150, "caractères")):
            with self.assertRaisesMessage(DuplicationError, message):
                renommer_article(self.tole, valeur)
        self.assertTrue(Article.objects.filter(pk="TOLE-A").exists())

    def test_vue_et_formulaire(self):
        page = self.client.get("/admin/technique/article/TOLE-A/change/")
        self.assertContains(page, "Renommer")
        self.assertNotContains(page, 'name="reference"')  # référence en lecture seule : plus de second article par erreur
        r = self.client.post("/admin/technique/article/TOLE-A/renommer/", {"nouvelle_reference": "TOLE-C"})
        self.assertRedirects(r, "/admin/technique/article/TOLE-C/change/", fetch_redirect_response=False)
        r = self.client.post("/admin/technique/article/TOLE-C/renommer/", {"nouvelle_reference": "PIECE-R"}, follow=True)
        self.assertContains(r, "existe déjà")
        self.assertTrue(Article.objects.filter(pk="TOLE-C").exists())
