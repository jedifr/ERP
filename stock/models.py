from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Sum
from django.utils import timezone

from technique.models import Article


# Tolérance de comparaison des quantités (les quantités sont des flottants) et
# précision à laquelle elles sont conservées : évite les résidus du type
# 0.1 + 0.2 - 0.3 = 5e-17, qui faussaient seuils d'alerte et soldes.
EPSILON_QUANTITE = 1e-9
DECIMALES_QUANTITE = 6


def arrondir_quantite(valeur):
    return round(valeur, DECIMALES_QUANTITE)


class StockInsuffisantError(Exception):
    """Sortie supérieure à la quantité disponible dans le lot."""


class MouvementImmuableError(Exception):
    """Un mouvement de stock ne se modifie ni ne se supprime : on le contre-passe."""


class Emplacement(models.Model):
    code = models.CharField("code", max_length=50, primary_key=True)
    libelle = models.CharField("libellé", max_length=200, blank=True)

    class Meta:
        verbose_name = "Emplacement"
        verbose_name_plural = "Emplacements"
        ordering = ["code"]

    def __str__(self):
        return self.libelle and f"{self.code} — {self.libelle}" or self.code


class Lot(models.Model):
    """Un article a aujourd'hui un lot unique. Passer à plusieurs lots par
    article (chutes, longueurs restantes) ne demande aucune refonte du
    modèle, juste la création de lots supplémentaires — voir `longueur_restante`.
    """

    article = models.ForeignKey(
        Article, verbose_name="article", on_delete=models.PROTECT, related_name="lots"
    )
    emplacement = models.ForeignKey(
        Emplacement, verbose_name="emplacement", on_delete=models.PROTECT, related_name="lots"
    )
    quantite = models.FloatField("quantité", default=0)
    longueur_restante = models.FloatField(
        "longueur restante", null=True, blank=True, help_text="Inutilisé en v1"
    )
    statut = models.CharField("statut", max_length=50, blank=True)

    class Meta:
        verbose_name = "Lot"
        verbose_name_plural = "Lots"
        ordering = ["article", "emplacement"]

    def __str__(self):
        return f"{self.article} @ {self.emplacement} ({self.quantite})"

    def clean(self):
        super().clean()
        if self.article_id and not self.article.gere_en_stock:
            raise ValidationError(
                {"article": "Cet article n'est pas géré en stock (gere_en_stock=faux)."}
            )


class MouvementStockQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise MouvementImmuableError("Les mouvements de stock ne se modifient pas : contre-passez-les.")

    def delete(self):
        raise MouvementImmuableError("Les mouvements de stock ne se suppriment pas : contre-passez-les.")


class MouvementStock(models.Model):
    """Journal de stock : une fois créé, un mouvement est immuable. Une erreur
    se corrige par un mouvement inverse (voir `annuler`), jamais en éditant ou
    supprimant l'original — sinon le solde du lot ne correspondrait plus à la
    somme de ses mouvements."""

    class TypeMouvement(models.TextChoices):
        ENTREE = "entree", "Entrée"
        SORTIE = "sortie", "Sortie"

    lot = models.ForeignKey(Lot, verbose_name="lot", on_delete=models.PROTECT, related_name="mouvements")
    type_mouvement = models.CharField("type de mouvement", max_length=20, choices=TypeMouvement.choices)
    quantite = models.FloatField("quantité")
    date_mouvement = models.DateField("date de mouvement", default=timezone.localdate)
    reference_origine = models.CharField(
        "référence d'origine",
        max_length=100,
        blank=True,
        help_text="Pointe vers l'OF, la commande fournisseur, etc.",
    )
    motif = models.CharField(
        "motif",
        max_length=200,
        blank=True,
        help_text="Obligatoire pour un mouvement saisi à la main (sans référence d'origine).",
    )
    utilisateur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="saisi par",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
        related_name="mouvements_stock",
    )
    date_creation = models.DateTimeField("enregistré le", auto_now_add=True, null=True, editable=False)
    annule_mouvement = models.OneToOneField(
        "self",
        verbose_name="mouvement annulé",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        editable=False,
        related_name="contre_passation",
        help_text="Renseigné sur un mouvement de contre-passation : pointe vers l'original qu'il annule.",
    )

    objects = MouvementStockQuerySet.as_manager()

    class Meta:
        verbose_name = "Mouvement de stock"
        verbose_name_plural = "Mouvements de stock"
        ordering = ["-date_mouvement", "-id"]
        permissions = [("annuler_mouvement", "Peut annuler (contre-passer) un mouvement de stock")]

    def __str__(self):
        return f"{self.get_type_mouvement_display()} {self.quantite} — {self.lot}"

    def clean(self):
        super().clean()
        if self.quantite is not None and self.quantite <= 0:
            raise ValidationError({"quantite": "La quantité d'un mouvement doit être positive."})
        if self.pk is not None:
            return
        if not self.reference_origine and not self.motif:
            raise ValidationError({"motif": "Indiquez un motif (ou une référence d'origine) pour ce mouvement."})
        if (
            self.lot_id
            and self.quantite
            and self.type_mouvement == self.TypeMouvement.SORTIE
            and self.quantite > self.lot.quantite + EPSILON_QUANTITE
        ):
            raise ValidationError(
                {"quantite": f"Stock insuffisant : {self.lot.quantite:g} disponible dans ce lot, sortie de {self.quantite:g}."}
            )

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise MouvementImmuableError("Un mouvement de stock ne se modifie pas : contre-passez-le.")
        with transaction.atomic():
            # Verrou de ligne sur le lot : deux sorties simultanées ne peuvent plus
            # toutes deux passer le contrôle de disponibilité.
            lot = Lot.objects.select_for_update().get(pk=self.lot_id)
            delta = self.quantite if self.type_mouvement == self.TypeMouvement.ENTREE else -self.quantite
            nouveau_solde = arrondir_quantite(lot.quantite + delta)
            if nouveau_solde < -EPSILON_QUANTITE:
                raise StockInsuffisantError(
                    f"Stock insuffisant pour « {lot.article} » ({lot.emplacement}) : "
                    f"{lot.quantite:g} disponible, sortie de {self.quantite:g}."
                )
            super().save(*args, **kwargs)
            if abs(nouveau_solde) < EPSILON_QUANTITE:
                nouveau_solde = 0.0
            Lot.objects.filter(pk=lot.pk).update(quantite=nouveau_solde)
            lot.refresh_from_db(fields=["quantite"])
            self.lot = lot
        evaluer_alerte_stock(lot.article)

    def delete(self, *args, **kwargs):
        raise MouvementImmuableError("Un mouvement de stock ne se supprime pas : contre-passez-le.")

    def annuler(self, utilisateur=None, motif=""):
        """Crée le mouvement inverse. Refusé si ce mouvement est déjà annulé, ou
        s'il est lui-même une contre-passation (on n'annule pas une annulation) ;
        l'inversion d'une entrée déjà consommée échoue comme toute sortie
        excédentaire (StockInsuffisantError)."""
        if self.annule_mouvement_id is not None:
            raise MouvementImmuableError("Ce mouvement est déjà une annulation : on ne l'annule pas.")
        if MouvementStock.objects.filter(annule_mouvement=self).exists():
            raise MouvementImmuableError("Ce mouvement a déjà été annulé.")
        inverse = (
            self.TypeMouvement.SORTIE if self.type_mouvement == self.TypeMouvement.ENTREE else self.TypeMouvement.ENTREE
        )
        return MouvementStock.objects.create(
            lot=self.lot,
            type_mouvement=inverse,
            quantite=self.quantite,
            reference_origine=f"ANNULATION-#{self.pk}",
            motif=motif or "Annulation",
            utilisateur=utilisateur,
            annule_mouvement=self,
        )


class AlerteStock(models.Model):
    """Une seule alerte active à la fois par article."""

    class Statut(models.TextChoices):
        ACTIVE = "active", "Active"
        TRAITEE = "traitee", "Traitée"

    article = models.ForeignKey(
        Article, verbose_name="article", on_delete=models.CASCADE, related_name="alertes_stock"
    )
    date_declenchement = models.DateField("date de déclenchement", default=timezone.now)
    statut = models.CharField("statut", max_length=20, choices=Statut.choices, default=Statut.ACTIVE)
    date_traitement = models.DateField(
        "date de traitement",
        null=True,
        blank=True,
        help_text="Clôture auto (stock remonté) ou manuelle (commande fournisseur)",
    )

    class Meta:
        verbose_name = "Alerte de stock"
        verbose_name_plural = "Alertes de stock"
        ordering = ["-date_declenchement"]
        constraints = [
            models.UniqueConstraint(
                fields=["article"],
                condition=models.Q(statut="active"),
                name="une_seule_alerte_active_par_article",
            )
        ]

    def __str__(self):
        return f"{self.article} — {self.get_statut_display()} ({self.date_declenchement})"


def stock_total(article):
    return arrondir_quantite(Lot.objects.filter(article=article).aggregate(total=Sum("quantite"))["total"] or 0)


def evaluer_alerte_stock(article):
    """Ouvre ou clôture automatiquement l'alerte de seuil d'un article, selon
    son stock total actuel comparé à `Article.stock_mini`."""
    if not article.gere_en_stock or article.stock_mini is None:
        return

    total = stock_total(article)
    alerte_active = AlerteStock.objects.filter(article=article, statut=AlerteStock.Statut.ACTIVE).first()

    if total <= article.stock_mini:
        if alerte_active is None:
            AlerteStock.objects.create(article=article, date_declenchement=timezone.now().date())
    elif alerte_active is not None:
        alerte_active.statut = AlerteStock.Statut.TRAITEE
        alerte_active.date_traitement = timezone.now().date()
        alerte_active.save()
