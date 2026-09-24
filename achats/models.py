from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from chiffrage.models import CommandeLigne
from commercial.models import TauxTVA, Tiers
from comptabilite.models import PosteGestion
from stock.models import AlerteStock, Lot, MouvementStock
from technique.models import Article, DateRangeHistoriqueMixin


class AchatsError(Exception):
    """Donnée de référence manquante ou incohérente empêchant la réception."""


def _taux_tva_par_defaut():
    """Valeur par défaut du champ LigneCommandeFournisseur.taux_tva : le taux
    coché comme « taux par défaut » dans le référentiel, ou aucun s'il n'y
    en a pas — même principe que chiffrage.models._taux_tva_par_defaut."""
    defaut = TauxTVA.objects.filter(est_defaut=True).first()
    return defaut.pk if defaut else None


class ArticleFournisseur(models.Model):
    """Un fournisseur pouvant approvisionner un article acheté (matière
    première, service acheté, consommable ou composant — jamais un
    fabriqué, qui n'a pas de fournisseur). Porte la référence et la
    désignation propres à CE fournisseur (distinctes de celles de
    l'article en interne), et sert de point d'ancrage à l'historique de
    tarifs (TarifAchatArticle) : plusieurs fournisseurs peuvent proposer
    le même article, chacun avec sa propre référence et son propre
    historique de prix."""

    article = models.ForeignKey(
        Article, verbose_name="article", on_delete=models.CASCADE, related_name="fournisseurs"
    )
    fournisseur = models.ForeignKey(
        Tiers, verbose_name="fournisseur", on_delete=models.CASCADE, related_name="articles_fournis"
    )
    reference_fournisseur = models.CharField("référence fournisseur", max_length=100, blank=True)
    designation_fournisseur = models.CharField("désignation fournisseur", max_length=200, blank=True)

    class Meta:
        verbose_name = "Fournisseur d'article"
        verbose_name_plural = "Fournisseurs d'article"
        ordering = ["article", "fournisseur"]
        constraints = [
            models.UniqueConstraint(fields=["article", "fournisseur"], name="unique_article_fournisseur")
        ]

    def __str__(self):
        return f"{self.article} — {self.fournisseur}"

    def clean(self):
        super().clean()
        if self.article_id and self.article.nature == Article.Nature.FABRIQUE:
            raise ValidationError(
                {"article": "Un article fabriqué est produit en interne : il n'a pas de fournisseur."}
            )

    @property
    def tarif_actuel(self):
        aujourdhui = timezone.now().date()
        return (
            self.tarifs.filter(date_debut__lte=aujourdhui)
            .filter(Q(date_fin__isnull=True) | Q(date_fin__gte=aujourdhui))
            .order_by("-date_debut")
            .first()
        )


class TarifAchatArticle(DateRangeHistoriqueMixin, models.Model):
    """Historise le prix d'achat d'un ArticleFournisseur — même principe que
    TarifPoste (technique.models) : consulter/recalculer le prix à une date
    donnée, tracer les évolutions tarifaires fournisseur par fournisseur."""

    historique_scope_fields = ("article_fournisseur",)

    article_fournisseur = models.ForeignKey(
        ArticleFournisseur, verbose_name="fournisseur de l'article", on_delete=models.CASCADE, related_name="tarifs"
    )
    prix_unitaire = models.FloatField("prix unitaire", help_text="€, prix d'achat proposé par ce fournisseur")
    frais_port = models.FloatField(
        "frais de port", null=True, blank=True, help_text="€, forfait de livraison associé à ce tarif (facultatif)"
    )
    date_debut = models.DateField("date de début")
    date_fin = models.DateField("date de fin", null=True, blank=True)

    class Meta:
        verbose_name = "Tarif d'achat"
        verbose_name_plural = "Tarifs d'achat"
        ordering = ["article_fournisseur", "-date_debut"]

    def __str__(self):
        return f"{self.article_fournisseur} : {self.prix_unitaire} € ({self.date_debut} → {self.date_fin or '…'})"


class CommandeFournisseur(models.Model):
    numero = models.CharField("numéro", max_length=50, primary_key=True)
    fournisseur = models.ForeignKey(
        Tiers, verbose_name="fournisseur", on_delete=models.PROTECT, related_name="commandes_fournisseur"
    )
    date_commande = models.DateField("date de commande")
    date_livraison_prevue = models.DateField("date de livraison prévue", null=True, blank=True)
    statut = models.CharField("statut", max_length=50, blank=True)

    class Meta:
        verbose_name = "Commande fournisseur"
        verbose_name_plural = "Commandes fournisseur"
        ordering = ["-date_commande", "numero"]

    def __str__(self):
        return self.numero


class LigneCommandeFournisseur(models.Model):
    """Une ligne peut être rattachée à un article stocké (`article`), ou à
    une charge générale non stockée — assurance, abonnement, carburant...
    — via un `poste_gestion` seul (voir comptabilite.PosteGestion) : ces
    charges n'ont jamais d'équivalent Article dans l'app. clean() impose
    l'un ou l'autre, jamais les deux ni aucun des deux."""

    commande_fournisseur = models.ForeignKey(
        CommandeFournisseur, verbose_name="commande fournisseur", on_delete=models.CASCADE, related_name="lignes"
    )
    article = models.ForeignKey(
        Article,
        verbose_name="article",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="lignes_commande_fournisseur",
    )
    poste_gestion = models.ForeignKey(
        PosteGestion,
        verbose_name="poste de gestion",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="lignes_commande_fournisseur",
        help_text="Pour une charge générale sans article (assurance, abonnement...).",
    )
    designation = models.CharField(
        "désignation",
        max_length=255,
        blank=True,
        help_text="Libellé libre pour une ligne sans article (ex. « Assurance RC Pro — T1 2026 »).",
    )
    alerte_stock_origine = models.ForeignKey(
        AlerteStock,
        verbose_name="alerte de stock d'origine",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lignes_commande_fournisseur",
        help_text="Nullable — clôture l'alerte à la commande",
    )
    commande_ligne_client = models.ForeignKey(
        CommandeLigne,
        verbose_name="ligne de commande client",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approvisionnements",
        help_text=(
            "Commande client que cet achat sert à approvisionner (facultatif). "
            "Plusieurs lignes de commande fournisseur peuvent pointer vers la "
            "même ligne de commande client (réappro en plusieurs fois, ou "
            "auprès de plusieurs fournisseurs)."
        ),
    )
    quantite_commandee = models.FloatField("quantité commandée")
    prix_unitaire_achat = models.FloatField("prix unitaire d'achat")
    taux_tva = models.ForeignKey(
        TauxTVA,
        verbose_name="taux de TVA",
        on_delete=models.PROTECT,
        related_name="lignes_commande_fournisseur",
        null=True,
        blank=True,
        default=_taux_tva_par_defaut,
    )
    quantite_recue = models.FloatField(
        "quantité reçue", default=0, editable=False, help_text="Cumul recalculé depuis les réceptions"
    )

    class Meta:
        verbose_name = "Ligne de commande fournisseur"
        verbose_name_plural = "Lignes de commande fournisseur"
        ordering = ["commande_fournisseur", "id"]

    def __str__(self):
        objet = self.article or self.poste_gestion or self.designation or "?"
        return f"{self.commande_fournisseur} — {objet} × {self.quantite_commandee}"

    @property
    def montant_ht(self):
        return self.prix_unitaire_achat * self.quantite_commandee

    montant_ht.fget.short_description = "Montant HT"

    @property
    def montant_ttc(self):
        taux = self.taux_tva.taux if self.taux_tva_id else 0
        return self.montant_ht * (1 + taux / 100)

    montant_ttc.fget.short_description = "Montant TTC"

    def clean(self):
        super().clean()
        if not self.article_id and not self.poste_gestion_id:
            raise ValidationError("Renseignez soit un article, soit un poste de gestion (charge générale).")
        if self.article_id and self.poste_gestion_id:
            raise ValidationError(
                "Une ligne ne peut pas porter à la fois un article et un poste de gestion — "
                "le poste de gestion sert aux charges sans article."
            )

    def save(self, *args, **kwargs):
        creation = self.pk is None
        if creation and self.article_id and self.commande_ligne_client_id and not self.alerte_stock_origine_id:
            # Rattacher cette ligne à une commande client montre qu'elle
            # répond à un besoin identifié : si une alerte de stock active
            # existe déjà pour le même article, on la clôture avec cette
            # ligne au lieu d'attendre une sélection manuelle.
            self.alerte_stock_origine = AlerteStock.objects.filter(
                article=self.article, statut=AlerteStock.Statut.ACTIVE
            ).first()
        super().save(*args, **kwargs)
        if creation and self.alerte_stock_origine_id and self.alerte_stock_origine.statut == AlerteStock.Statut.ACTIVE:
            alerte = self.alerte_stock_origine
            alerte.statut = AlerteStock.Statut.TRAITEE
            alerte.date_traitement = timezone.now().date()
            alerte.save()


class Reception(models.Model):
    numero = models.CharField("numéro", max_length=50, primary_key=True)
    commande_fournisseur = models.ForeignKey(
        CommandeFournisseur,
        verbose_name="commande fournisseur",
        on_delete=models.PROTECT,
        related_name="receptions",
    )
    date_reception = models.DateField("date de réception", default=timezone.now)

    class Meta:
        verbose_name = "Réception"
        verbose_name_plural = "Réceptions"
        ordering = ["-date_reception", "numero"]

    def __str__(self):
        return self.numero


class ReceptionLigne(models.Model):
    reception = models.ForeignKey(
        Reception, verbose_name="réception", on_delete=models.CASCADE, related_name="lignes"
    )
    ligne_commande_fournisseur = models.ForeignKey(
        LigneCommandeFournisseur,
        verbose_name="ligne de commande fournisseur",
        on_delete=models.PROTECT,
        related_name="receptions_lignes",
    )
    quantite_recue = models.FloatField("quantité reçue")

    class Meta:
        verbose_name = "Ligne de réception"
        verbose_name_plural = "Lignes de réception"
        ordering = ["reception", "id"]

    def __str__(self):
        return f"{self.reception} — {self.ligne_commande_fournisseur} × {self.quantite_recue}"

    def clean(self):
        super().clean()
        if self.quantite_recue is not None and self.quantite_recue <= 0:
            raise ValidationError({"quantite_recue": "La quantité reçue doit être positive."})
        if self.pk is None and self.ligne_commande_fournisseur_id:
            deja_recu = self.ligne_commande_fournisseur.quantite_recue
            commande = self.ligne_commande_fournisseur.quantite_commandee
            if deja_recu + (self.quantite_recue or 0) > commande:
                raise ValidationError(
                    {"quantite_recue": f"Dépasse la quantité commandée ({commande}, déjà reçu {deja_recu})."}
                )

    def save(self, *args, **kwargs):
        creation = self.pk is None
        super().save(*args, **kwargs)
        if creation:
            self._appliquer()

    def _appliquer(self):
        ligne = self.ligne_commande_fournisseur
        LigneCommandeFournisseur.objects.filter(pk=ligne.pk).update(
            quantite_recue=models.F("quantite_recue") + self.quantite_recue
        )

        if ligne.article_id is None:
            # Ligne "poste de gestion" (charge générale sans article, ex.
            # assurance) : pas de stock à mouvementer, seul le cumul
            # quantite_recue (mis à jour ci-dessus) a un sens ici.
            return

        lot = self._lot_unique_pour_article(ligne.article)
        MouvementStock.objects.create(
            lot=lot,
            type_mouvement=MouvementStock.TypeMouvement.ENTREE,
            quantite=self.quantite_recue,
            date_mouvement=self.reception.date_reception,
            reference_origine=f"RECEPTION-{self.reception.numero}",
        )

    @staticmethod
    def _lot_unique_pour_article(article):
        lots = list(Lot.objects.filter(article=article))
        if len(lots) == 0:
            raise AchatsError(
                f"Aucun lot existant pour l'article « {article} ». Créez-en un (module Stock) avant de réceptionner."
            )
        if len(lots) > 1:
            raise AchatsError(
                f"Plusieurs lots existent pour l'article « {article} » : réception automatique non "
                "applicable, mettez à jour le stock manuellement."
            )
        return lots[0]


class FactureFournisseur(models.Model):
    """Facture fournisseur : document de référence côté achat, symétrique de
    facturation.Facture côté vente — la facture "légale" est reçue du
    fournisseur (papier/email/PDF), en dehors de l'ERP ; ce modèle en garde
    la trace (numéro interne + référence fournisseur) et sert de point de
    départ à la génération de l'écriture comptable d'achat (voir
    achats.generation.generer_ecriture_achat), maintenant qu'ArticleCompteAchat
    et PosteGestion.compte_achat_* ont un document pour s'y accrocher."""

    numero = models.CharField("numéro", max_length=50, primary_key=True)
    commande_fournisseur = models.ForeignKey(
        CommandeFournisseur, verbose_name="commande fournisseur", on_delete=models.PROTECT, related_name="factures"
    )
    reference_fournisseur = models.CharField(
        "référence fournisseur", max_length=100, blank=True,
        help_text="Numéro de facture tel qu'indiqué par le fournisseur (distinct du numéro interne ci-dessus)",
    )
    date_facture = models.DateField("date de facture")
    montant_ht = models.FloatField("montant HT", null=True, blank=True)
    montant_ttc = models.FloatField("montant TTC", null=True, blank=True)
    statut_paiement = models.CharField("statut de paiement", max_length=50, blank=True)

    class Meta:
        verbose_name = "Facture fournisseur"
        verbose_name_plural = "Factures fournisseur"
        ordering = ["-date_facture", "numero"]

    def __str__(self):
        return self.numero
