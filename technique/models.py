from django.core.exceptions import ValidationError
from django.db import models


class Matiere(models.Model):
    """Référentiel des matières (acier, aluminium, inox...)."""

    nom = models.CharField(max_length=100, primary_key=True)
    densite = models.FloatField(help_text="kg/dm³, utilisée pour le calcul au poids")

    class Meta:
        verbose_name = "Matière"
        verbose_name_plural = "Matières"
        ordering = ["nom"]

    def __str__(self):
        return self.nom


class Article(models.Model):
    """Table unique portant deux natures : matière première ou fabriqué."""

    class Nature(models.TextChoices):
        MATIERE_PREMIERE = "matiere_premiere", "Matière première"
        FABRIQUE = "fabrique", "Fabriqué"

    class UniteCout(models.TextChoices):
        SURFACE = "surface", "Surface"
        LONGUEUR = "longueur", "Longueur"
        POIDS = "poids", "Poids"
        PIECE = "piece", "Pièce"

    class TypeProfil(models.TextChoices):
        TUBE_CARRE = "tube_carre", "Tube carré"
        TUBE_RECTANGULAIRE = "tube_rectangulaire", "Tube rectangulaire"
        CORNIERE = "corniere", "Cornière"
        PROFIL_I = "profil_i", "Profilé I"
        PROFIL_U = "profil_u", "Profilé U"

    reference = models.CharField(max_length=100, primary_key=True)
    nature = models.CharField(max_length=20, choices=Nature.choices)
    matiere = models.ForeignKey(
        Matiere,
        on_delete=models.PROTECT,
        related_name="articles",
        null=True,
        blank=True,
        help_text="Pertinent pour tôles/profilés",
    )
    unite_cout = models.CharField(max_length=20, choices=UniteCout.choices, null=True, blank=True)
    epaisseur = models.FloatField(null=True, blank=True, help_text="Tôle")
    type_profil = models.CharField(max_length=20, choices=TypeProfil.choices, null=True, blank=True)
    poids_lineique = models.FloatField(null=True, blank=True, help_text="kg/mètre (profilés vendus au poids)")
    cout_unitaire = models.FloatField(null=True, blank=True, help_text="Coût d'achat (matière première)")
    taux_marge_defaut = models.FloatField(
        null=True, blank=True, help_text="Marge par défaut sur le coût matière (articles fabriqués)"
    )
    gere_en_stock = models.BooleanField(
        null=True,
        blank=True,
        help_text=(
            "Vrai par défaut pour une matière première, faux par défaut pour un fabriqué, "
            "modifiable au cas par cas. Laisser vide pour appliquer le défaut."
        ),
    )
    stock_mini = models.FloatField(null=True, blank=True, help_text="Seuil d'alerte de réapprovisionnement")
    quantite_reappro = models.FloatField(null=True, blank=True, help_text="Quantité suggérée à commander")

    class Meta:
        verbose_name = "Article"
        verbose_name_plural = "Articles"
        ordering = ["reference"]

    def __str__(self):
        return self.reference

    def clean(self):
        super().clean()
        if self.nature == self.Nature.FABRIQUE and self.cout_unitaire is not None:
            raise ValidationError(
                {
                    "cout_unitaire": (
                        "Un article fabriqué n'a pas de coût unitaire fixe : son coût est recalculé "
                        "à chaque devis à partir de sa nomenclature et de sa gamme."
                    )
                }
            )

    def save(self, *args, **kwargs):
        if self.gere_en_stock is None:
            self.gere_en_stock = self.nature == self.Nature.MATIERE_PREMIERE
        super().save(*args, **kwargs)


class PosteTravail(models.Model):
    """Un centre de charge logique (ex. "Mazak"), même si plusieurs machines identiques le composent."""

    class ModeCalcul(models.TextChoices):
        HORAIRE = "horaire", "Horaire"
        FORFAITAIRE = "forfaitaire", "Forfaitaire"

    nom = models.CharField(max_length=100, primary_key=True)
    type_operation = models.CharField(max_length=100, blank=True)
    mode_calcul = models.CharField(max_length=20, choices=ModeCalcul.choices)
    nombre_machines = models.PositiveIntegerField(default=1, help_text="Capacité agrégée (usage planning)")
    taux_marge_defaut = models.FloatField(
        null=True, blank=True, help_text="Marge par défaut sur les opérations de ce poste"
    )

    class Meta:
        verbose_name = "Poste de travail"
        verbose_name_plural = "Postes de travail"
        ordering = ["nom"]

    def __str__(self):
        return self.nom


class DateRangeHistoriqueMixin:
    """Empêche le chevauchement de deux enregistrements actifs sur le même périmètre.

    Une sous-classe doit définir `historique_scope_fields`, la liste des champs
    identifiant le "créneau" historisé (ex. le poste, ou l'article + le poste + l'ordre).
    """

    historique_scope_fields = ()

    def clean(self):
        super().clean()
        if self.date_debut is not None and self.date_fin is not None and self.date_fin < self.date_debut:
            raise ValidationError({"date_fin": "La date de fin doit être postérieure à la date de début."})

        if self.date_debut is None:
            return

        scope = {field: getattr(self, field) for field in self.historique_scope_fields}
        if any(value is None for value in scope.values()):
            return

        qs = type(self).objects.filter(**scope)
        if self.pk is not None:
            qs = qs.exclude(pk=self.pk)

        for other in qs:
            starts_before_other_ends = other.date_fin is None or self.date_debut <= other.date_fin
            other_starts_before_self_ends = self.date_fin is None or other.date_debut <= self.date_fin
            if starts_before_other_ends and other_starts_before_self_ends:
                raise ValidationError(
                    "Cette période chevauche une période existante "
                    f"({other.date_debut} → {other.date_fin or '…'}) pour le même périmètre."
                )


class TarifPoste(DateRangeHistoriqueMixin, models.Model):
    """Historise le coût horaire d'un poste — permet de recalculer un ancien devis avec les taux d'époque."""

    historique_scope_fields = ("poste",)

    poste = models.ForeignKey(PosteTravail, on_delete=models.CASCADE, related_name="tarifs")
    cout_horaire = models.FloatField(help_text="€/heure")
    date_debut = models.DateField()
    date_fin = models.DateField(null=True, blank=True)

    class Meta:
        verbose_name = "Tarif de poste"
        verbose_name_plural = "Tarifs de poste"
        ordering = ["poste", "-date_debut"]

    def __str__(self):
        return f"{self.poste} : {self.cout_horaire} €/h ({self.date_debut} → {self.date_fin or '…'})"


class Nomenclature(models.Model):
    """Ce qu'un article fabriqué consomme."""

    article_parent = models.ForeignKey(Article, on_delete=models.CASCADE, related_name="composants")
    article_composant = models.ForeignKey(Article, on_delete=models.PROTECT, related_name="utilise_dans")
    longueur_mm = models.FloatField(null=True, blank=True, help_text="Tôle (avec largeur) ou profilé (seule)")
    largeur_mm = models.FloatField(null=True, blank=True, help_text="Tôle uniquement")
    quantite = models.FloatField(help_text="Nombre de pièces/découpes identiques")

    class Meta:
        verbose_name = "Ligne de nomenclature"
        verbose_name_plural = "Nomenclatures"
        ordering = ["article_parent", "article_composant"]

    def __str__(self):
        return f"{self.article_parent} ← {self.quantite} × {self.article_composant}"

    def clean(self):
        super().clean()
        if self.article_parent_id and self.article_parent.nature != Article.Nature.FABRIQUE:
            raise ValidationError(
                {"article_parent": "Seul un article fabriqué peut porter une nomenclature."}
            )
        if self.article_parent_id and self.article_composant_id and self.article_parent_id == self.article_composant_id:
            raise ValidationError(
                {"article_composant": "Un article ne peut pas être son propre composant."}
            )


class Gamme(DateRangeHistoriqueMixin, models.Model):
    """Suite d'opérations (postes) suivie par un article fabriqué, historisée."""

    historique_scope_fields = ("article", "poste", "ordre")

    article = models.ForeignKey(Article, on_delete=models.CASCADE, related_name="gamme_etapes")
    poste = models.ForeignKey(PosteTravail, on_delete=models.PROTECT, related_name="gamme_etapes")
    ordre = models.PositiveIntegerField()
    temps_fixe = models.FloatField(null=True, blank=True, help_text="Réglage (mode horaire)")
    temps_variable = models.FloatField(null=True, blank=True, help_text="Temps unitaire (mode horaire)")
    cout_forfaitaire = models.FloatField(null=True, blank=True, help_text="Mode forfaitaire (sous-traitance)")
    date_debut = models.DateField()
    date_fin = models.DateField(null=True, blank=True, help_text="Historisation de la révision")

    class Meta:
        verbose_name = "Étape de gamme"
        verbose_name_plural = "Gammes"
        ordering = ["article", "ordre", "-date_debut"]

    def __str__(self):
        return f"{self.article} — étape {self.ordre} ({self.poste})"

    def clean(self):
        super().clean()
        if self.article_id and self.article.nature != Article.Nature.FABRIQUE:
            raise ValidationError({"article": "Seul un article fabriqué peut porter une gamme."})

        if self.poste_id:
            if self.poste.mode_calcul == PosteTravail.ModeCalcul.HORAIRE:
                if self.temps_fixe is None or self.temps_variable is None:
                    raise ValidationError(
                        "Un poste en mode horaire requiert un temps fixe et un temps variable."
                    )
                if self.cout_forfaitaire is not None:
                    raise ValidationError(
                        {"cout_forfaitaire": "Non applicable pour un poste en mode horaire."}
                    )
            elif self.poste.mode_calcul == PosteTravail.ModeCalcul.FORFAITAIRE:
                if self.cout_forfaitaire is None:
                    raise ValidationError(
                        {"cout_forfaitaire": "Un poste en mode forfaitaire requiert un coût forfaitaire."}
                    )
                if self.temps_fixe is not None or self.temps_variable is not None:
                    raise ValidationError(
                        "Temps fixe/variable non applicables pour un poste en mode forfaitaire."
                    )
