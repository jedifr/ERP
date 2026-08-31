from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Sum
from django.utils import timezone

from technique.models import Article


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


class MouvementStock(models.Model):
    class TypeMouvement(models.TextChoices):
        ENTREE = "entree", "Entrée"
        SORTIE = "sortie", "Sortie"

    lot = models.ForeignKey(Lot, verbose_name="lot", on_delete=models.PROTECT, related_name="mouvements")
    type_mouvement = models.CharField("type de mouvement", max_length=20, choices=TypeMouvement.choices)
    quantite = models.FloatField("quantité")
    date_mouvement = models.DateField("date de mouvement", default=timezone.now)
    reference_origine = models.CharField(
        "référence d'origine",
        max_length=100,
        blank=True,
        help_text="Pointe vers l'OF, la commande fournisseur, etc.",
    )

    class Meta:
        verbose_name = "Mouvement de stock"
        verbose_name_plural = "Mouvements de stock"
        ordering = ["-date_mouvement", "-id"]

    def __str__(self):
        return f"{self.get_type_mouvement_display()} {self.quantite} — {self.lot}"

    def clean(self):
        super().clean()
        if self.quantite is not None and self.quantite <= 0:
            raise ValidationError({"quantite": "La quantité d'un mouvement doit être positive."})

    def save(self, *args, **kwargs):
        creation = self.pk is None
        super().save(*args, **kwargs)
        if creation:
            self._appliquer_au_lot()

    def _appliquer_au_lot(self):
        delta = self.quantite if self.type_mouvement == self.TypeMouvement.ENTREE else -self.quantite
        Lot.objects.filter(pk=self.lot_id).update(quantite=models.F("quantite") + delta)
        self.lot.refresh_from_db(fields=["quantite"])
        evaluer_alerte_stock(self.lot.article)


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
    return Lot.objects.filter(article=article).aggregate(total=Sum("quantite"))["total"] or 0


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
