import datetime

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from codification.models import RegleCodification
from technique.models import Article

from .models import AlerteStock, Emplacement, Lot, MouvementStock, stock_total


class LotTests(TestCase):
    def test_article_non_gere_en_stock_refuse(self):
        article = Article.objects.create(
            reference="PIECE-STOCK-01", nature=Article.Nature.FABRIQUE, gere_en_stock=False
        )
        emplacement = Emplacement.objects.create(code="A1")
        lot = Lot(article=article, emplacement=emplacement)
        with self.assertRaises(ValidationError):
            lot.full_clean()

    def test_article_gere_en_stock_accepte(self):
        article = Article.objects.create(
            reference="TOLE-STOCK-01", nature=Article.Nature.MATIERE_PREMIERE
        )
        emplacement = Emplacement.objects.create(code="A2")
        lot = Lot(article=article, emplacement=emplacement)
        lot.full_clean()  # ne doit pas lever d'exception


class MouvementStockTests(TestCase):
    def setUp(self):
        self.article = Article.objects.create(
            reference="TOLE-STOCK-02",
            nature=Article.Nature.MATIERE_PREMIERE,
            stock_mini=10,
        )
        self.emplacement = Emplacement.objects.create(code="B1")
        self.lot = Lot.objects.create(article=self.article, emplacement=self.emplacement, quantite=0)

    def test_entree_augmente_la_quantite_du_lot(self):
        MouvementStock.objects.create(
            lot=self.lot,
            type_mouvement=MouvementStock.TypeMouvement.ENTREE,
            quantite=50,
            date_mouvement=datetime.date(2026, 1, 1),
        )
        self.lot.refresh_from_db()
        self.assertEqual(self.lot.quantite, 50)

    def test_sortie_diminue_la_quantite_du_lot(self):
        MouvementStock.objects.create(
            lot=self.lot, type_mouvement=MouvementStock.TypeMouvement.ENTREE, quantite=50
        )
        MouvementStock.objects.create(
            lot=self.lot, type_mouvement=MouvementStock.TypeMouvement.SORTIE, quantite=20
        )
        self.lot.refresh_from_db()
        self.assertEqual(self.lot.quantite, 30)

    def test_quantite_negative_ou_nulle_refusee(self):
        mouvement = MouvementStock(
            lot=self.lot, type_mouvement=MouvementStock.TypeMouvement.ENTREE, quantite=0
        )
        with self.assertRaises(ValidationError):
            mouvement.full_clean()

    def test_edition_dun_mouvement_est_refusee(self):
        from .models import MouvementImmuableError

        mouvement = MouvementStock.objects.create(
            lot=self.lot, type_mouvement=MouvementStock.TypeMouvement.ENTREE, quantite=50, motif="Stock initial"
        )
        mouvement.quantite = 500
        with self.assertRaises(MouvementImmuableError):
            mouvement.save()
        self.lot.refresh_from_db()
        self.assertEqual(self.lot.quantite, 50)


class AlerteStockTests(TestCase):
    def setUp(self):
        self.article = Article.objects.create(
            reference="TOLE-STOCK-03",
            nature=Article.Nature.MATIERE_PREMIERE,
            stock_mini=10,
        )
        self.emplacement = Emplacement.objects.create(code="C1")
        self.lot = Lot.objects.create(article=self.article, emplacement=self.emplacement, quantite=0)

    def _mouvement(self, type_mouvement, quantite):
        return MouvementStock.objects.create(lot=self.lot, type_mouvement=type_mouvement, quantite=quantite)

    def test_alerte_declenchee_sous_le_seuil(self):
        self._mouvement(MouvementStock.TypeMouvement.ENTREE, 5)  # stock=5 <= stock_mini=10
        self.assertEqual(
            AlerteStock.objects.filter(article=self.article, statut=AlerteStock.Statut.ACTIVE).count(), 1
        )

    def test_pas_de_doublon_alerte_active(self):
        self._mouvement(MouvementStock.TypeMouvement.ENTREE, 5)
        self._mouvement(MouvementStock.TypeMouvement.SORTIE, 1)  # stock=4, toujours sous le seuil
        self.assertEqual(
            AlerteStock.objects.filter(article=self.article, statut=AlerteStock.Statut.ACTIVE).count(), 1
        )

    def test_alerte_cloturee_automatiquement_au_retour_au_dessus_du_seuil(self):
        self._mouvement(MouvementStock.TypeMouvement.ENTREE, 5)
        self._mouvement(MouvementStock.TypeMouvement.ENTREE, 20)  # stock=25 > stock_mini=10
        alerte = AlerteStock.objects.get(article=self.article)
        self.assertEqual(alerte.statut, AlerteStock.Statut.TRAITEE)
        self.assertIsNotNone(alerte.date_traitement)

    def test_pas_dalerte_sans_stock_mini(self):
        article = Article.objects.create(reference="TOLE-STOCK-04", nature=Article.Nature.MATIERE_PREMIERE)
        emplacement = Emplacement.objects.create(code="C2")
        lot = Lot.objects.create(article=article, emplacement=emplacement, quantite=0)
        MouvementStock.objects.create(lot=lot, type_mouvement=MouvementStock.TypeMouvement.ENTREE, quantite=1)
        self.assertFalse(AlerteStock.objects.filter(article=article).exists())

    def test_stock_total_agrege_plusieurs_lots(self):
        autre_emplacement = Emplacement.objects.create(code="C3")
        autre_lot = Lot.objects.create(article=self.article, emplacement=autre_emplacement, quantite=0)
        self._mouvement(MouvementStock.TypeMouvement.ENTREE, 5)
        MouvementStock.objects.create(
            lot=autre_lot, type_mouvement=MouvementStock.TypeMouvement.ENTREE, quantite=7
        )
        self.assertEqual(stock_total(self.article), 12)


class EmplacementAdminCodificationTests(TestCase):
    """Emplacement : entité codifiée la plus simple (pas d'inline sur sa
    fiche admin), utilisée pour vérifier de bout en bout le comportement de
    CodificationInitialeMixin sur un vrai cycle requête/réponse — voir
    codification/tests.py pour les tests unitaires du service sous-jacent.
    Reproduit le scénario signalé par l'utilisateur : un formulaire d'ajout
    consulté puis abandonné sans être enregistré ne doit plus « sauter » de
    numéro."""

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser("emplacement-codif", "e@example.com", "pass1234")
        self.client.force_login(self.user)
        RegleCodification.objects.filter(pk=RegleCodification.Entite.EMPLACEMENT).update(
            prefixe="EMP-", nombre_chiffres=3, compteur_actuel=0
        )

    def test_visites_successives_sans_enregistrer_ne_sautent_aucun_numero(self):
        self.client.get("/admin/stock/emplacement/add/")
        self.client.get("/admin/stock/emplacement/add/")
        response = self.client.get("/admin/stock/emplacement/add/")
        self.assertContains(response, "EMP-001")

    def test_enregistrer_fait_avancer_le_compteur_pour_le_prochain_apercu(self):
        self.client.get("/admin/stock/emplacement/add/")  # aperçu seul, ne doit rien consommer
        response = self.client.post(
            "/admin/stock/emplacement/add/",
            data={"code": "EMP-001", "libelle": "Zone test", "_save": "Enregistrer"},
        )
        self.assertEqual(response.status_code, 302, getattr(response, "context", None))
        self.assertTrue(Emplacement.objects.filter(pk="EMP-001").exists())

        response = self.client.get("/admin/stock/emplacement/add/")
        self.assertContains(response, "EMP-002")


class _FixtureStock:
    def setUp(self):
        from django.contrib.auth import get_user_model

        self.user = get_user_model().objects.create_superuser("stock-admin", "s@example.com", "pass1234")
        self.client.force_login(self.user)
        self.article = Article.objects.create(
            reference="TOLE-INT", nature=Article.Nature.MATIERE_PREMIERE, stock_mini=5, gere_en_stock=True
        )
        self.emplacement = Emplacement.objects.create(code="INT-1")
        self.lot = Lot.objects.create(article=self.article, emplacement=self.emplacement)

    def _mouvement(self, type_mouvement, quantite, **kwargs):
        kwargs.setdefault("motif", "test")
        return MouvementStock.objects.create(lot=self.lot, type_mouvement=type_mouvement, quantite=quantite, **kwargs)

    def _entree(self, quantite, **kw):
        return self._mouvement(MouvementStock.TypeMouvement.ENTREE, quantite, **kw)

    def _sortie(self, quantite, **kw):
        return self._mouvement(MouvementStock.TypeMouvement.SORTIE, quantite, **kw)


class SortieSuperieureAuDisponibleTests(_FixtureStock, TestCase):
    """B-OP-02 : on ne sort pas plus que ce que le lot contient."""

    def test_sortie_excedentaire_refusee_et_stock_inchange(self):
        from .models import StockInsuffisantError

        self._entree(5)
        with self.assertRaises(StockInsuffisantError):
            self._sortie(8)
        self.lot.refresh_from_db()
        self.assertEqual(self.lot.quantite, 5)
        self.assertEqual(self.lot.mouvements.count(), 1)

    def test_sortie_exacte_du_stock_autorisee(self):
        self._entree(5)
        self._sortie(5)
        self.lot.refresh_from_db()
        self.assertEqual(self.lot.quantite, 0)

    def test_validation_de_formulaire_signale_le_stock_insuffisant(self):
        self._entree(5)
        mouvement = MouvementStock(
            lot=self.lot, type_mouvement=MouvementStock.TypeMouvement.SORTIE, quantite=8, motif="x"
        )
        with self.assertRaises(ValidationError) as cm:
            mouvement.full_clean()
        self.assertIn("quantite", cm.exception.message_dict)
        self.assertIn("Stock insuffisant", cm.exception.message_dict["quantite"][0])

    def test_api_refuse_une_sortie_excedentaire(self):
        self._entree(5)
        response = self.client.post(
            "/api/v1/mouvements-stock/",
            data={"lot": self.lot.pk, "type_mouvement": "sortie", "quantite": 8, "motif": "x"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.lot.refresh_from_db()
        self.assertEqual(self.lot.quantite, 5)

    def test_admin_refuse_une_sortie_excedentaire(self):
        self._entree(5)
        response = self.client.post(
            "/admin/stock/mouvementstock/add/",
            {"lot": self.lot.pk, "type_mouvement": "sortie", "quantite": "8", "date_mouvement": "2026-01-01",
             "reference_origine": "", "motif": "x"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Stock insuffisant")
        self.lot.refresh_from_db()
        self.assertEqual(self.lot.quantite, 5)

    def test_livraison_bloquee_si_le_stock_ne_suffit_pas(self):
        import datetime

        from chiffrage.models import Commande, CommandeLigne, Devis, DevisLigne, Livraison, LivraisonError, LivraisonLigne
        from chiffrage.production import lancer_en_production
        from commercial.models import Adresse, Tiers

        tiers = Tiers.objects.create(code="CLI-STK", raison_sociale="Client Stock", type_tiers=Tiers.TypeTiers.CLIENT)
        for champ in ("est_facturation", "est_livraison"):
            Adresse.objects.create(tiers=tiers, adresse="1 rue", code_postal="75000", ville="Paris", est_principale=True, **{champ: True})
        self.article.cout_unitaire = 1
        self.article.unite_cout = Article.UniteCout.PIECE
        self.article.save()
        devis = Devis.objects.create(numero="DEV-STK", client=tiers, date_creation=datetime.date(2026, 1, 1), statut="valide")
        DevisLigne.objects.create(devis=devis, article=self.article, quantite=10)
        commande = lancer_en_production(devis)
        ligne = commande.lignes.get()
        self._entree(4)
        livraison = Livraison.objects.create(numero="LIV-STK", commande=commande, date_livraison=datetime.date(2026, 2, 1))
        with self.assertRaises(LivraisonError):
            LivraisonLigne.objects.create(livraison=livraison, commande_ligne=ligne, quantite_livree=10)
        ligne.refresh_from_db()
        self.assertEqual(ligne.quantite_livree, 0)
        self.lot.refresh_from_db()
        self.assertEqual(self.lot.quantite, 4)


class JournalImmuableTests(_FixtureStock, TestCase):
    """B-OP-03 : un mouvement ne se modifie ni ne se supprime ; le solde du lot reste
    toujours égal à la somme de ses mouvements."""

    def test_suppression_refusee(self):
        from .models import MouvementImmuableError

        mouvement = self._entree(10)
        with self.assertRaises(MouvementImmuableError):
            mouvement.delete()
        with self.assertRaises(MouvementImmuableError):
            MouvementStock.objects.filter(pk=mouvement.pk).delete()
        with self.assertRaises(MouvementImmuableError):
            MouvementStock.objects.filter(pk=mouvement.pk).update(quantite=1)
        self.assertEqual(MouvementStock.objects.count(), 1)

    def test_admin_sans_modification_ni_suppression(self):
        mouvement = self._entree(10)
        change = self.client.post(f"/admin/stock/mouvementstock/{mouvement.pk}/change/", {"quantite": "999"})
        self.assertIn(change.status_code, (200, 302, 403))
        mouvement.refresh_from_db()
        self.assertEqual(mouvement.quantite, 10)
        self.assertEqual(self.client.post(f"/admin/stock/mouvementstock/{mouvement.pk}/delete/", {"post": "yes"}).status_code, 403)
        self.assertTrue(MouvementStock.objects.filter(pk=mouvement.pk).exists())

    def test_api_sans_modification_ni_suppression(self):
        import json

        mouvement = self._entree(10)
        url = f"/api/v1/mouvements-stock/{mouvement.pk}/"
        self.assertEqual(self.client.patch(url, json.dumps({"quantite": 99}), content_type="application/json").status_code, 405)
        self.assertEqual(self.client.delete(url).status_code, 405)

    def test_solde_du_lot_egal_a_la_somme_des_mouvements(self):
        from django.db.models import Sum

        self._entree(10)
        self._sortie(3)
        self._entree(7)
        self.lot.refresh_from_db()
        total = sum(m.quantite if m.type_mouvement == "entree" else -m.quantite for m in self.lot.mouvements.all())
        self.assertEqual(self.lot.quantite, total)

    def test_pas_de_residu_flottant(self):
        self._entree(0.1)
        self._entree(0.2)
        self._sortie(0.3)
        self.lot.refresh_from_db()
        self.assertEqual(self.lot.quantite, 0.0)

    def test_alerte_sans_faux_declenchement_sur_residu(self):
        self.article.stock_mini = 0
        self.article.save()
        self._entree(0.1)
        self._entree(0.2)
        self._sortie(0.3)
        self.assertEqual(AlerteStock.objects.filter(article=self.article, statut="active").count(), 1)  # 0 <= 0 : seuil atteint, pas de doublon


class TracabiliteMouvementTests(_FixtureStock, TestCase):
    """B-AD-02 : qui, quand, pourquoi."""

    def test_motif_ou_reference_obligatoire_pour_un_mouvement_manuel(self):
        mouvement = MouvementStock(lot=self.lot, type_mouvement="entree", quantite=1)
        with self.assertRaises(ValidationError) as cm:
            mouvement.full_clean()
        self.assertIn("motif", cm.exception.message_dict)
        MouvementStock(lot=self.lot, type_mouvement="entree", quantite=1, reference_origine="OF-1").full_clean()

    def test_admin_enregistre_utilisateur_et_horodatage(self):
        self.client.post(
            "/admin/stock/mouvementstock/add/",
            {"lot": self.lot.pk, "type_mouvement": "entree", "quantite": "12", "date_mouvement": "2026-01-01",
             "reference_origine": "", "motif": "Réception hors commande"},
        )
        mouvement = MouvementStock.objects.get()
        self.assertEqual(mouvement.utilisateur, self.user)
        self.assertIsNotNone(mouvement.date_creation)
        self.assertEqual(mouvement.motif, "Réception hors commande")

    def test_api_enregistre_utilisateur_et_ignore_celui_fourni(self):
        from django.contrib.auth import get_user_model

        autre = get_user_model().objects.create_user("autre", "a@example.com", "pass1234")
        response = self.client.post(
            "/api/v1/mouvements-stock/",
            data={"lot": self.lot.pk, "type_mouvement": "entree", "quantite": 3, "motif": "x", "utilisateur": autre.pk},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(MouvementStock.objects.get().utilisateur, self.user)

    def test_saisie_depuis_la_fiche_du_lot(self):
        response = self.client.post(
            f"/admin/stock/lot/{self.lot.pk}/change/",
            {"article": self.article.pk, "emplacement": self.emplacement.pk, "statut": "",
             "mouvements-TOTAL_FORMS": "1", "mouvements-INITIAL_FORMS": "0", "mouvements-MIN_NUM_FORMS": "0",
             "mouvements-MAX_NUM_FORMS": "1000", "mouvements-0-type_mouvement": "entree", "mouvements-0-quantite": "9",
             "mouvements-0-date_mouvement": "2026-01-01", "mouvements-0-reference_origine": "",
             "mouvements-0-motif": "Inventaire initial", "mouvements-0-lot": self.lot.pk, "_save": "Enregistrer"},
        )
        self.assertEqual(response.status_code, 302, getattr(response, "context_data", ""))
        self.lot.refresh_from_db()
        self.assertEqual(self.lot.quantite, 9)
        self.assertEqual(self.lot.mouvements.get().utilisateur, self.user)


class ContrePassationTests(_FixtureStock, TestCase):
    """B-OP-03 : la correction passe par un mouvement inverse, tracé et non répétable."""

    def test_annulation_dune_entree_cree_une_sortie_inverse(self):
        entree = self._entree(10)
        inverse = entree.annuler(utilisateur=self.user, motif="Saisie en double")
        self.assertEqual(inverse.type_mouvement, MouvementStock.TypeMouvement.SORTIE)
        self.assertEqual(inverse.quantite, 10)
        self.assertEqual(inverse.annule_mouvement, entree)
        self.assertEqual(inverse.utilisateur, self.user)
        self.assertEqual(inverse.motif, "Saisie en double")
        self.lot.refresh_from_db()
        self.assertEqual(self.lot.quantite, 0)
        self.assertTrue(MouvementStock.objects.filter(pk=entree.pk).exists())

    def test_annulation_dune_sortie_remet_le_stock(self):
        self._entree(10)
        sortie = self._sortie(4)
        sortie.annuler()
        self.lot.refresh_from_db()
        self.assertEqual(self.lot.quantite, 10)

    def test_double_annulation_refusee(self):
        from .models import MouvementImmuableError

        entree = self._entree(10)
        entree.annuler()
        with self.assertRaises(MouvementImmuableError):
            entree.annuler()

    def test_on_nannule_pas_une_annulation(self):
        from .models import MouvementImmuableError

        inverse = self._entree(10).annuler()
        with self.assertRaises(MouvementImmuableError):
            inverse.annuler()

    def test_annuler_une_entree_deja_consommee_est_refuse(self):
        from .models import StockInsuffisantError

        entree = self._entree(10)
        self._sortie(8)
        with self.assertRaises(StockInsuffisantError):
            entree.annuler()
        self.lot.refresh_from_db()
        self.assertEqual(self.lot.quantite, 2)

    def test_action_admin_et_page_de_confirmation(self):
        entree = self._entree(10)
        page = self.client.get(f"/admin/stock/mouvementstock/{entree.pk}/annuler/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Motif")
        self.client.post(f"/admin/stock/mouvementstock/{entree.pk}/annuler/", {"motif": "Erreur de saisie"})
        self.lot.refresh_from_db()
        self.assertEqual(self.lot.quantite, 0)
        self.assertEqual(MouvementStock.objects.get(annule_mouvement=entree).motif, "Erreur de saisie")

    def test_page_de_confirmation_avertit_pour_un_mouvement_issu_dun_document(self):
        entree = self._entree(10, reference_origine="RECEPTION-R1", motif="")
        page = self.client.get(f"/admin/stock/mouvementstock/{entree.pk}/annuler/")
        self.assertContains(page, "généré par un document")

    def test_api_annuler(self):
        entree = self._entree(10)
        response = self.client.post(
            f"/api/v1/mouvements-stock/{entree.pk}/annuler/", data={"motif": "doublon"}, content_type="application/json"
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(self.client.post(f"/api/v1/mouvements-stock/{entree.pk}/annuler/").status_code, 400)

    def test_annulation_exige_la_permission(self):
        from django.contrib.auth import get_user_model

        entree = self._entree(10)
        simple = get_user_model().objects.create_user("sans-droit-stock", "z@example.com", "pass1234", is_staff=True)
        self.client.force_login(simple)
        self.assertEqual(self.client.post(f"/api/v1/mouvements-stock/{entree.pk}/annuler/").status_code, 403)
        self.client.post(f"/admin/stock/mouvementstock/{entree.pk}/annuler/", {"motif": "x"})
        self.lot.refresh_from_db()
        self.assertEqual(self.lot.quantite, 10)


class TransfertTests(_FixtureStock, TestCase):
    """B-OP-04 : déplacer du stock = une sortie + une entrée liées, atomiquement."""

    def setUp(self):
        super().setUp()
        self.cible = Emplacement.objects.create(code="INT-2")
        self._entree(10, cout_unitaire=4.0)

    def _transferer(self, quantite, **kw):
        from .models import Transfert

        return Transfert.objects.create(
            lot_source=self.lot, emplacement_cible=self.cible, quantite=quantite, motif="Réorganisation",
            utilisateur=self.user, **kw,
        )

    def test_transfert_cree_le_lot_cible_et_deux_mouvements_lies(self):
        transfert = self._transferer(6)
        self.lot.refresh_from_db()
        self.assertEqual(self.lot.quantite, 4)
        cible = transfert.lot_cible
        cible.refresh_from_db()
        self.assertEqual((cible.article, cible.emplacement, cible.quantite), (self.article, self.cible, 6))
        mouvements = MouvementStock.objects.filter(reference_origine=f"TRANSFERT-{transfert.pk}")
        self.assertEqual({m.type_mouvement for m in mouvements}, {"sortie", "entree"})
        self.assertTrue(all(m.utilisateur == self.user for m in mouvements))

    def test_transfert_conserve_la_valorisation(self):
        transfert = self._transferer(6)
        transfert.lot_cible.refresh_from_db()
        self.assertEqual(transfert.lot_cible.cout_unitaire_moyen, 4.0)

    def test_deuxieme_transfert_reutilise_le_lot_cible(self):
        premier = self._transferer(3)
        second = self._transferer(2)
        self.assertEqual(premier.lot_cible, second.lot_cible)
        second.lot_cible.refresh_from_db()
        self.assertEqual(second.lot_cible.quantite, 5)

    def test_transfert_superieur_au_stock_refuse_sans_rien_creer(self):
        from .models import StockInsuffisantError, Transfert

        with self.assertRaises(StockInsuffisantError):
            self._transferer(11)
        self.assertEqual(Transfert.objects.count(), 0)
        self.assertEqual(Lot.objects.count(), 1)
        self.lot.refresh_from_db()
        self.assertEqual(self.lot.quantite, 10)

    def test_validation_refuse_meme_emplacement_et_quantite_nulle(self):
        from .models import Transfert

        meme = Transfert(lot_source=self.lot, emplacement_cible=self.emplacement, quantite=1)
        with self.assertRaises(ValidationError) as cm:
            meme.full_clean()
        self.assertIn("emplacement_cible", cm.exception.message_dict)
        nul = Transfert(lot_source=self.lot, emplacement_cible=self.cible, quantite=0)
        with self.assertRaises(ValidationError):
            nul.full_clean()

    def test_transfert_immuable(self):
        from .models import MouvementImmuableError

        transfert = self._transferer(2)
        transfert.quantite = 9
        with self.assertRaises(MouvementImmuableError):
            transfert.save()
        with self.assertRaises(MouvementImmuableError):
            transfert.delete()

    def test_saisie_depuis_ladmin(self):
        self.client.post(
            "/admin/stock/transfert/add/",
            {"lot_source": self.lot.pk, "emplacement_cible": self.cible.pk, "quantite": "4",
             "date_transfert": "2026-01-01", "motif": "Rangement"},
        )
        self.lot.refresh_from_db()
        self.assertEqual(self.lot.quantite, 6)

    def test_api(self):
        response = self.client.post(
            "/api/v1/transferts-stock/",
            data={"lot_source": self.lot.pk, "emplacement_cible": self.cible.pk, "quantite": 3},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        refus = self.client.post(
            "/api/v1/transferts-stock/",
            data={"lot_source": self.lot.pk, "emplacement_cible": self.cible.pk, "quantite": 50},
            content_type="application/json",
        )
        self.assertEqual(refus.status_code, 400)


class InventaireTests(_FixtureStock, TestCase):
    """B-OP-05 : comptage physique, écarts transformés en mouvements d'ajustement."""

    def setUp(self):
        super().setUp()
        from .models import Inventaire, InventaireLigne

        self._entree(10, cout_unitaire=2.0)
        self.inventaire = Inventaire.objects.create(commentaire="Fin de trimestre")
        self.ligne = InventaireLigne.objects.create(inventaire=self.inventaire, lot=self.lot, quantite_comptee=7)

    def test_validation_cree_une_sortie_pour_un_manque(self):
        self.inventaire.valider(self.user)
        self.lot.refresh_from_db()
        self.assertEqual(self.lot.quantite, 7)
        ajustement = MouvementStock.objects.get(reference_origine=f"INVENTAIRE-{self.inventaire.pk}")
        self.assertEqual((ajustement.type_mouvement, ajustement.quantite), ("sortie", 3))
        self.assertEqual(ajustement.utilisateur, self.user)
        self.ligne.refresh_from_db()
        self.assertEqual((self.ligne.quantite_theorique, self.ligne.ecart), (10, -3))
        self.assertEqual(self.inventaire.statut, "valide")
        self.assertEqual(self.inventaire.utilisateur_validation, self.user)

    def test_validation_cree_une_entree_pour_un_surplus_valorisee_au_cout_moyen(self):
        self.ligne.quantite_comptee = 12
        self.ligne.save()
        self.inventaire.valider(self.user)
        self.lot.refresh_from_db()
        self.assertEqual((self.lot.quantite, self.lot.cout_unitaire_moyen), (12, 2.0))

    def test_aucun_mouvement_si_pas_decart(self):
        self.ligne.quantite_comptee = 10
        self.ligne.save()
        self.inventaire.valider(self.user)
        self.assertFalse(MouvementStock.objects.filter(reference_origine__startswith="INVENTAIRE-").exists())

    def test_inventaire_valide_est_fige(self):
        from .models import InventaireError, InventaireLigne

        self.inventaire.valider(self.user)
        with self.assertRaises(InventaireError):
            self.inventaire.valider(self.user)
        autre = Lot.objects.create(article=self.article, emplacement=Emplacement.objects.create(code="INT-9"))
        ligne = InventaireLigne(inventaire=self.inventaire, lot=autre, quantite_comptee=1)
        with self.assertRaises(ValidationError):
            ligne.full_clean()

    def test_inventaire_vide_refuse(self):
        from .models import Inventaire, InventaireError

        with self.assertRaises(InventaireError):
            Inventaire.objects.create().valider(self.user)

    def test_un_seul_comptage_par_lot(self):
        from django.db import IntegrityError, transaction

        from .models import InventaireLigne

        with self.assertRaises(IntegrityError), transaction.atomic():
            InventaireLigne.objects.create(inventaire=self.inventaire, lot=self.lot, quantite_comptee=1)

    def test_action_admin_valider_et_prerempli(self):
        self.client.post(
            "/admin/stock/inventaire/",
            {"action": "action_ajouter_tous_les_lots", "_selected_action": [self.inventaire.pk]}, follow=True,
        )
        self.client.post(
            "/admin/stock/inventaire/",
            {"action": "action_valider", "_selected_action": [self.inventaire.pk]}, follow=True,
        )
        self.inventaire.refresh_from_db()
        self.assertEqual(self.inventaire.statut, "valide")
        self.lot.refresh_from_db()
        self.assertEqual(self.lot.quantite, 7)

    def test_validation_exige_la_permission(self):
        from django.contrib.auth import get_user_model

        simple = get_user_model().objects.create_user("inv-simple", "i@example.com", "pass1234", is_staff=True)
        self.client.force_login(simple)
        self.assertEqual(self.client.post(f"/api/v1/inventaires/{self.inventaire.pk}/valider/").status_code, 403)
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(f"/api/v1/inventaires/{self.inventaire.pk}/valider/").status_code, 200)

    def test_api_refuse_de_modifier_un_inventaire_valide(self):
        import json

        self.inventaire.valider(self.user)
        r = self.client.patch(f"/api/v1/inventaires/{self.inventaire.pk}/", json.dumps({"commentaire": "x"}), content_type="application/json")
        self.assertEqual(r.status_code, 400)
        r = self.client.patch(f"/api/v1/inventaire-lignes/{self.ligne.pk}/", json.dumps({"quantite_comptee": 1}), content_type="application/json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.client.delete(f"/api/v1/inventaire-lignes/{self.ligne.pk}/").status_code, 400)


class ValorisationTests(_FixtureStock, TestCase):
    """B-MG-02 : coût moyen pondéré par lot, valeur du stock."""

    def test_cout_moyen_pondere_sur_entrees_successives(self):
        self._entree(10, cout_unitaire=2.0)
        self._entree(30, cout_unitaire=4.0)
        self.lot.refresh_from_db()
        self.assertAlmostEqual(self.lot.cout_unitaire_moyen, 3.5)
        self.assertEqual(self.lot.valeur_stock, 140.0)

    def test_une_sortie_ne_change_pas_le_cout_moyen(self):
        self._entree(10, cout_unitaire=2.0)
        self._entree(10, cout_unitaire=4.0)
        self._sortie(5)
        self.lot.refresh_from_db()
        self.assertAlmostEqual(self.lot.cout_unitaire_moyen, 3.0)
        self.assertEqual(self.lot.valeur_stock, 45.0)

    def test_entree_sans_cout_ne_modifie_pas_la_valorisation(self):
        self._entree(10, cout_unitaire=2.0)
        self._entree(10)
        self.lot.refresh_from_db()
        self.assertEqual(self.lot.cout_unitaire_moyen, 2.0)

    def test_premiere_entree_apres_stock_vide_repart_du_nouveau_cout(self):
        self._entree(10, cout_unitaire=2.0)
        self._sortie(10)
        self._entree(5, cout_unitaire=6.0)
        self.lot.refresh_from_db()
        self.assertEqual(self.lot.cout_unitaire_moyen, 6.0)

    def test_cout_negatif_refuse(self):
        mouvement = MouvementStock(lot=self.lot, type_mouvement="entree", quantite=1, motif="x", cout_unitaire=-1)
        with self.assertRaises(ValidationError):
            mouvement.full_clean()

    def test_reception_fournisseur_valorise_le_lot_au_prix_dachat(self):
        from achats.models import CommandeFournisseur, LigneCommandeFournisseur, Reception, ReceptionLigne
        from commercial.models import Tiers

        fournisseur = Tiers.objects.create(code="FOU-VAL", raison_sociale="Fournisseur", type_tiers=Tiers.TypeTiers.FOURNISSEUR)
        commande = CommandeFournisseur.objects.create(
            numero="CF-VAL", fournisseur=fournisseur, date_commande=datetime.date(2026, 1, 1)
        )
        ligne = LigneCommandeFournisseur.objects.create(
            commande_fournisseur=commande, article=self.article, quantite_commandee=20, prix_unitaire_achat=3.25
        )
        reception = Reception.objects.create(numero="REC-VAL", commande_fournisseur=commande, date_reception=datetime.date(2026, 1, 5))
        ReceptionLigne.objects.create(reception=reception, ligne_commande_fournisseur=ligne, quantite_recue=20)
        self.lot.refresh_from_db()
        self.assertEqual((self.lot.quantite, self.lot.cout_unitaire_moyen), (20, 3.25))


class VerrouLotEtHistoriqueTests(_FixtureStock, TestCase):
    def test_article_et_emplacement_figes_apres_un_mouvement(self):
        autre_emplacement = Emplacement.objects.create(code="INT-X")
        self.lot.emplacement = autre_emplacement
        self.lot.full_clean()  # pas encore de mouvement : autorisé
        self.lot.save()
        self._entree(5)
        self.lot.emplacement = self.emplacement
        with self.assertRaises(ValidationError):
            self.lot.full_clean()

    def test_admin_rend_article_et_emplacement_en_lecture_seule_apres_mouvement(self):
        self._entree(5)
        response = self.client.get(f"/admin/stock/lot/{self.lot.pk}/change/")
        self.assertNotContains(response, 'name="article"')
        self.assertNotContains(response, 'name="emplacement"')

    def test_api_refuse_de_changer_lemplacement_dun_lot_mouvemente(self):
        import json

        self._entree(5)
        autre = Emplacement.objects.create(code="INT-Y")
        r = self.client.patch(f"/api/v1/lots/{self.lot.pk}/", json.dumps({"emplacement": autre.pk}), content_type="application/json")
        self.assertEqual(r.status_code, 400, r.content)

    def test_historique_du_lot_trace_les_changements(self):
        self.lot.statut = "quarantaine"
        self.lot.save()
        derniere, precedente = self.lot.history.all()[0], self.lot.history.all()[1]
        changements = {c.field: (c.old, c.new) for c in derniere.diff_against(precedente).changes}
        self.assertEqual(changements["statut"], ("", "quarantaine"))

    def test_historique_en_lecture_seule(self):
        ancienne = self.lot.history.last()
        r = self.client.post(f"/admin/stock/lot/{self.lot.pk}/history/{ancienne.pk}/", {})
        self.assertEqual(r.status_code, 403)
