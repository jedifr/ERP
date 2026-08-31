from django.core.exceptions import ValidationError
from django.db import models

from commercial.models import Adresse, Tiers
from technique.models import Article, PosteTravail


class Devis(models.Model):
    class Statut(models.TextChoices):
        BROUILLON = "brouillon", "Brouillon"
        VALIDE = "valide", "Validé"

    numero = models.CharField("numéro", max_length=50, primary_key=True)
    client = models.ForeignKey(Tiers, verbose_name="client", on_delete=models.PROTECT, related_name="devis")
    date_creation = models.DateField("date de création")
    statut = models.CharField("statut", max_length=20, choices=Statut.choices, default=Statut.BROUILLON)
    taux_marge_globale = models.FloatField(
        "taux de marge globale", null=True, blank=True, help_text="Optionnel, écrase les marges par défaut"
    )

    class Meta:
        verbose_name = "Devis"
        verbose_name_plural = "Devis"
        ordering = ["-date_creation", "numero"]

    def __str__(self):
        return self.numero

    @property
    def montant_matiere_ht(self):
        return sum(ligne.prix_vente_matiere or 0 for ligne in self.lignes.all())

    montant_matiere_ht.fget.short_description = "Montant matière HT"

    @property
    def montant_operations_ht(self):
        return sum(ligne.prix_vente_operations for ligne in self.lignes.all())

    montant_operations_ht.fget.short_description = "Montant opérations HT (temps machine / main d'œuvre)"

    @property
    def montant_total_ht(self):
        return self.montant_matiere_ht + self.montant_operations_ht

    montant_total_ht.fget.short_description = "Montant total HT"


class DevisLigne(models.Model):
    devis = models.ForeignKey(Devis, verbose_name="devis", on_delete=models.CASCADE, related_name="lignes")
    article = models.ForeignKey(
        Article, verbose_name="article", on_delete=models.PROTECT, related_name="devis_lignes"
    )
    quantite = models.FloatField("quantité")
    cout_matiere_calcule = models.FloatField(
        "coût matière calculé", null=True, blank=True, editable=False
    )
    taux_marge_matiere_applique = models.FloatField(
        "taux de marge matière appliqué",
        null=True,
        blank=True,
        help_text="Pré-rempli depuis l'article, éditable",
    )
    prix_vente_matiere = models.FloatField("prix de vente matière", null=True, blank=True, editable=False)

    class Meta:
        verbose_name = "Ligne de devis"
        verbose_name_plural = "Lignes de devis"
        ordering = ["devis", "id"]

    def __str__(self):
        return f"{self.devis} — {self.article} × {self.quantite}"

    @property
    def prix_vente_operations(self):
        """Prix de vente cumulé des opérations de gamme (temps machine / main d'œuvre)."""
        return sum(op.prix_vente or 0 for op in self.operations.all())

    prix_vente_operations.fget.short_description = "Prix de vente opérations"

    @property
    def prix_vente_total(self):
        """Prix de vente matière + opérations. None tant que le chiffrage matière
        n'a pas été calculé (cohérent avec cout_matiere_calcule/prix_vente_matiere)."""
        if self.prix_vente_matiere is None:
            return None
        return self.prix_vente_matiere + self.prix_vente_operations

    prix_vente_total.fget.short_description = "Prix de vente total (matière + opérations)"


class DevisLigneOperation(models.Model):
    devis_ligne = models.ForeignKey(
        DevisLigne, verbose_name="ligne de devis", on_delete=models.CASCADE, related_name="operations"
    )
    poste = models.ForeignKey(
        PosteTravail, verbose_name="poste", on_delete=models.PROTECT, related_name="devis_operations"
    )
    ordre = models.PositiveIntegerField("ordre")
    cout_calcule = models.FloatField("coût calculé", null=True, blank=True, editable=False)
    taux_marge_applique = models.FloatField(
        "taux de marge appliqué", null=True, blank=True, help_text="Pré-rempli depuis le poste, éditable"
    )
    prix_vente = models.FloatField("prix de vente", null=True, blank=True, editable=False)

    class Meta:
        verbose_name = "Opération de ligne de devis"
        verbose_name_plural = "Opérations de ligne de devis"
        ordering = ["devis_ligne", "ordre"]
        constraints = [
            models.UniqueConstraint(
                fields=["devis_ligne", "ordre"], name="unique_ordre_par_ligne_devis"
            )
        ]

    def __str__(self):
        return f"{self.devis_ligne} — étape {self.ordre} ({self.poste})"


class Commande(models.Model):
    numero = models.CharField("numéro", max_length=50, primary_key=True)
    devis = models.ForeignKey(Devis, verbose_name="devis", on_delete=models.PROTECT, related_name="commandes")
    date_commande = models.DateField("date de commande")
    statut = models.CharField("statut", max_length=50, blank=True)
    adresse_facturation = models.ForeignKey(
        Adresse,
        verbose_name="adresse de facturation",
        on_delete=models.PROTECT,
        related_name="commandes_facturation",
    )
    adresse_livraison = models.ForeignKey(
        Adresse,
        verbose_name="adresse de livraison",
        on_delete=models.PROTECT,
        related_name="commandes_livraison",
    )

    class Meta:
        verbose_name = "Commande"
        verbose_name_plural = "Commandes"
        ordering = ["-date_commande", "numero"]

    def __str__(self):
        return self.numero


class OrdreFabrication(models.Model):
    class StatutSynchro(models.TextChoices):
        SYNCHRONISE = "synchronise", "Synchronisé"
        EN_ATTENTE = "en_attente", "En attente"
        ECHEC_PERSISTANT = "echec_persistant", "Échec persistant"

    numero = models.CharField("numéro", max_length=50, primary_key=True)
    commande = models.ForeignKey(
        Commande, verbose_name="commande", on_delete=models.CASCADE, related_name="ordres_fabrication"
    )
    article = models.ForeignKey(
        Article, verbose_name="article", on_delete=models.PROTECT, related_name="ordres_fabrication"
    )
    quantite = models.FloatField("quantité")
    date_lancement = models.DateField("date de lancement")
    statut = models.CharField("statut", max_length=50, blank=True, help_text="Statut de production")
    statut_synchro = models.CharField(
        "statut de synchronisation",
        max_length=20,
        choices=StatutSynchro.choices,
        default=StatutSynchro.EN_ATTENTE,
    )
    nombre_tentatives = models.PositiveIntegerField("nombre de tentatives", default=0)
    date_derniere_tentative = models.DateField("date de dernière tentative", null=True, blank=True)

    class Meta:
        verbose_name = "Ordre de fabrication"
        verbose_name_plural = "Ordres de fabrication"
        ordering = ["-date_lancement", "numero"]

    def __str__(self):
        return self.numero

    def clean(self):
        super().clean()
        if self.article_id and self.article.nature != Article.Nature.FABRIQUE:
            raise ValidationError(
                {"article": "Un ordre de fabrication ne peut porter que sur un article fabriqué."}
            )


class OperationOF(models.Model):
    ordre_fabrication = models.ForeignKey(
        OrdreFabrication,
        verbose_name="ordre de fabrication",
        on_delete=models.CASCADE,
        related_name="operations",
    )
    poste = models.ForeignKey(
        PosteTravail, verbose_name="poste", on_delete=models.PROTECT, related_name="operations_of"
    )
    ordre = models.PositiveIntegerField("ordre")
    temps_prevu = models.FloatField(
        "temps prévu", null=True, blank=True, help_text="Copié de la gamme au lancement"
    )
    temps_reel = models.FloatField(
        "temps réel", null=True, blank=True, help_text="Alimenté par le planning atelier"
    )
    quantite_bonne = models.FloatField(
        "quantité bonne", null=True, blank=True, help_text="Alimenté par le planning atelier"
    )
    quantite_rebut = models.FloatField(
        "quantité rebut", null=True, blank=True, help_text="Alimenté par le planning atelier"
    )
    statut = models.CharField("statut", max_length=50, blank=True)

    class Meta:
        verbose_name = "Opération d'ordre de fabrication"
        verbose_name_plural = "Opérations d'ordre de fabrication"
        ordering = ["ordre_fabrication", "ordre"]
        constraints = [
            models.UniqueConstraint(
                fields=["ordre_fabrication", "ordre"], name="unique_ordre_par_of"
            )
        ]

    def __str__(self):
        return f"{self.ordre_fabrication} — étape {self.ordre} ({self.poste})"
