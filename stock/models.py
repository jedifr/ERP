from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.db.models import Sum
from django.utils import timezone
from simple_history.models import HistoricalRecords

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

    history = HistoricalRecords()

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
    numero_coulee = models.CharField(
        "n° de coulée / de lot fournisseur", max_length=100, blank=True, db_index=True,
        help_text="Traçabilité matière : repris sur le bon de livraison et recherchable.",
    )
    certificat = models.FileField(
        "certificat matière (3.1)", upload_to="stock/certificats/%Y/", blank=True,
        help_text="Certificat de la coulée (PDF ou image).",
    )
    cout_unitaire_moyen = models.FloatField(
        "coût unitaire moyen pondéré",
        default=0,
        editable=False,
        help_text="Recalculé à chaque entrée valorisée (réception, transfert) ; inchangé par les sorties.",
    )

    history = HistoricalRecords()

    class Meta:
        verbose_name = "Lot"
        verbose_name_plural = "Lots"
        ordering = ["article", "emplacement"]

    def __str__(self):
        return f"{self.article} @ {self.emplacement} ({self.quantite})"

    @property
    def valeur_stock(self):
        """Valeur du lot au coût moyen pondéré, arrondie au centime."""
        return round(max(self.quantite, 0) * self.cout_unitaire_moyen, 2)

    valeur_stock.fget.short_description = "Valeur du stock"

    def clean(self):
        super().clean()
        if self.article_id and not self.article.gere_en_stock:
            raise ValidationError(
                {"article": "Cet article n'est pas géré en stock (gere_en_stock=faux)."}
            )
        if self.pk is not None and self.mouvements.exists():
            # Relu en base : un formulaire ou un serializer a déjà appliqué les
            # valeurs soumises sur l'instance.
            en_base = Lot.objects.filter(pk=self.pk).values("article_id", "emplacement_id").first()
            if en_base and (
                en_base["article_id"] != self.article_id or en_base["emplacement_id"] != self.emplacement_id
            ):
                raise ValidationError(
                    "L'article et l'emplacement d'un lot qui a des mouvements ne se changent pas : "
                    "utilisez un transfert (changement d'emplacement) ou créez un autre lot."
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
    cout_unitaire = models.FloatField(
        "coût unitaire",
        null=True,
        blank=True,
        validators=[MinValueValidator(0, message="Ne peut pas être négatif.")],
        help_text="Valorisation d'une entrée (prix d'achat, coût de transfert) ; sert au coût moyen pondéré du lot.",
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
            champs = {"quantite": nouveau_solde}
            if self.type_mouvement == self.TypeMouvement.ENTREE and self.cout_unitaire is not None:
                # Coût moyen pondéré : (stock existant × coût moyen + entrée × son coût) / nouveau stock.
                stock_avant = max(lot.quantite, 0)
                champs["cout_unitaire_moyen"] = (
                    stock_avant * lot.cout_unitaire_moyen + self.quantite * self.cout_unitaire
                ) / (stock_avant + self.quantite)
            Lot.objects.filter(pk=lot.pk).update(**champs)
            lot.refresh_from_db(fields=["quantite", "cout_unitaire_moyen"])
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


class Transfert(models.Model):
    """Déplacement d'une quantité d'un lot vers un autre emplacement : une sortie
    du lot source et une entrée dans le lot cible (créé au besoin), liées par la
    même référence, atomiquement — jamais l'un sans l'autre. Immuable ; pour
    défaire un transfert, en faire un second en sens inverse."""

    lot_source = models.ForeignKey(Lot, verbose_name="lot source", on_delete=models.PROTECT, related_name="transferts_sortants")
    emplacement_cible = models.ForeignKey(
        Emplacement, verbose_name="emplacement cible", on_delete=models.PROTECT, related_name="transferts_entrants"
    )
    lot_cible = models.ForeignKey(
        Lot, verbose_name="lot cible", on_delete=models.PROTECT, null=True, blank=True, editable=False,
        related_name="transferts_entrants",
    )
    quantite = models.FloatField("quantité", validators=[MinValueValidator(0, message="Ne peut pas être négatif.")])
    date_transfert = models.DateField("date du transfert", default=timezone.localdate)
    motif = models.CharField("motif", max_length=200, blank=True)
    utilisateur = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="saisi par", on_delete=models.SET_NULL, null=True, blank=True,
        editable=False, related_name="transferts_stock",
    )
    date_creation = models.DateTimeField("enregistré le", auto_now_add=True, null=True, editable=False)

    class Meta:
        verbose_name = "Transfert de stock"
        verbose_name_plural = "Transferts de stock"
        ordering = ["-date_transfert", "-id"]

    def __str__(self):
        return f"Transfert #{self.pk or '—'} : {self.quantite:g} de {self.lot_source} vers {self.emplacement_cible}"

    def clean(self):
        super().clean()
        if self.pk is not None or not self.lot_source_id or not self.emplacement_cible_id:
            return
        if self.quantite is not None and self.quantite <= 0:
            raise ValidationError({"quantite": "La quantité doit être strictement positive."})
        if self.emplacement_cible_id == self.lot_source.emplacement_id:
            raise ValidationError({"emplacement_cible": "Le lot est déjà à cet emplacement."})
        if self.quantite is not None and self.quantite > self.lot_source.quantite + EPSILON_QUANTITE:
            raise ValidationError(
                {"quantite": f"Stock insuffisant : {self.lot_source.quantite:g} disponible dans le lot source."}
            )

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise MouvementImmuableError("Un transfert ne se modifie pas : faites un transfert inverse.")
        with transaction.atomic():
            source = Lot.objects.select_for_update().get(pk=self.lot_source_id)
            cible = (
                Lot.objects.filter(article=source.article, emplacement=self.emplacement_cible, statut=source.statut)
                .order_by("id")
                .first()
                or Lot.objects.create(article=source.article, emplacement=self.emplacement_cible, statut=source.statut)
            )
            self.lot_cible = cible
            super().save(*args, **kwargs)
            reference = f"TRANSFERT-{self.pk}"
            cout = source.cout_unitaire_moyen or None
            MouvementStock.objects.create(
                lot=source, type_mouvement=MouvementStock.TypeMouvement.SORTIE, quantite=self.quantite,
                date_mouvement=self.date_transfert, reference_origine=reference, motif=self.motif,
                utilisateur=self.utilisateur,
            )
            MouvementStock.objects.create(
                lot=cible, type_mouvement=MouvementStock.TypeMouvement.ENTREE, quantite=self.quantite,
                date_mouvement=self.date_transfert, reference_origine=reference, motif=self.motif,
                utilisateur=self.utilisateur, cout_unitaire=cout,
            )

    def delete(self, *args, **kwargs):
        raise MouvementImmuableError("Un transfert ne se supprime pas : faites un transfert inverse.")


class InventaireError(Exception):
    """Inventaire non validable dans son état actuel."""


class Inventaire(models.Model):
    """Comptage physique : on saisit la quantité comptée par lot ; à la validation,
    chaque écart avec le stock théorique devient un mouvement d'ajustement tracé
    (entrée ou sortie, référence INVENTAIRE-n) — jamais une réécriture du solde."""

    class Statut(models.TextChoices):
        BROUILLON = "brouillon", "Brouillon"
        VALIDE = "valide", "Validé"

    date_inventaire = models.DateField("date du comptage", default=timezone.localdate)
    commentaire = models.CharField("commentaire", max_length=200, blank=True)
    statut = models.CharField("statut", max_length=20, choices=Statut.choices, default=Statut.BROUILLON, editable=False)
    utilisateur_validation = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="validé par", on_delete=models.SET_NULL, null=True, blank=True,
        editable=False, related_name="inventaires_valides",
    )
    date_validation = models.DateTimeField("validé le", null=True, blank=True, editable=False)

    class Meta:
        verbose_name = "Inventaire"
        verbose_name_plural = "Inventaires"
        ordering = ["-date_inventaire", "-id"]
        permissions = [("valider_inventaire", "Peut valider un inventaire (ajuste le stock)")]

    def __str__(self):
        return f"Inventaire #{self.pk or '—'} du {self.date_inventaire:%d/%m/%Y}"

    def valider(self, utilisateur=None):
        with transaction.atomic():
            inventaire = Inventaire.objects.select_for_update().get(pk=self.pk)
            if inventaire.statut == self.Statut.VALIDE:
                raise InventaireError("Cet inventaire est déjà validé.")
            lignes = list(inventaire.lignes.select_related("lot__article"))
            if not lignes:
                raise InventaireError("Cet inventaire ne contient aucune ligne.")
            for ligne in lignes:
                ligne.appliquer(utilisateur)
            inventaire.statut = self.Statut.VALIDE
            inventaire.utilisateur_validation = utilisateur
            inventaire.date_validation = timezone.now()
            inventaire.save(update_fields=["statut", "utilisateur_validation", "date_validation"])
        self.refresh_from_db()


class InventaireLigne(models.Model):
    inventaire = models.ForeignKey(Inventaire, verbose_name="inventaire", on_delete=models.CASCADE, related_name="lignes")
    lot = models.ForeignKey(Lot, verbose_name="lot", on_delete=models.PROTECT, related_name="lignes_inventaire")
    quantite_comptee = models.FloatField("quantité comptée", validators=[MinValueValidator(0, message="Ne peut pas être négatif.")])
    quantite_theorique = models.FloatField("quantité théorique", null=True, blank=True, editable=False)
    ecart = models.FloatField("écart", null=True, blank=True, editable=False)

    class Meta:
        verbose_name = "Ligne d'inventaire"
        verbose_name_plural = "Lignes d'inventaire"
        ordering = ["inventaire", "lot"]
        constraints = [models.UniqueConstraint(fields=["inventaire", "lot"], name="un_lot_par_inventaire")]

    def __str__(self):
        return f"{self.inventaire} — {self.lot}"

    def clean(self):
        super().clean()
        if self.inventaire_id and self.inventaire.statut == Inventaire.Statut.VALIDE and self.pk is None:
            raise ValidationError("Cet inventaire est validé : on ne peut plus y ajouter de ligne.")

    def appliquer(self, utilisateur):
        lot = Lot.objects.get(pk=self.lot_id)
        theorique = lot.quantite
        ecart = arrondir_quantite(self.quantite_comptee - theorique)
        if abs(ecart) >= EPSILON_QUANTITE:
            MouvementStock.objects.create(
                lot=lot,
                type_mouvement=MouvementStock.TypeMouvement.ENTREE if ecart > 0 else MouvementStock.TypeMouvement.SORTIE,
                quantite=abs(ecart),
                date_mouvement=self.inventaire.date_inventaire,
                reference_origine=f"INVENTAIRE-{self.inventaire_id}",
                motif="Écart d'inventaire",
                utilisateur=utilisateur,
                cout_unitaire=(lot.cout_unitaire_moyen or None) if ecart > 0 else None,
            )
        self.quantite_theorique = theorique
        self.ecart = ecart
        self.save(update_fields=["quantite_theorique", "ecart"])


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
