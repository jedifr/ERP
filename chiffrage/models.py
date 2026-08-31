from django.core.exceptions import ValidationError
from django.db import models

from commercial.models import Adresse, Tiers
from technique.models import Article, PosteTravail


class Devis(models.Model):
    class Statut(models.TextChoices):
        BROUILLON = "brouillon", "Brouillon"
        VALIDE = "valide", "Validé"

    numero = models.CharField(max_length=50, primary_key=True)
    client = models.ForeignKey(Tiers, on_delete=models.PROTECT, related_name="devis")
    date_creation = models.DateField()
    statut = models.CharField(max_length=20, choices=Statut.choices, default=Statut.BROUILLON)
    taux_marge_globale = models.FloatField(
        null=True, blank=True, help_text="Optionnel, écrase les marges par défaut"
    )

    class Meta:
        verbose_name = "Devis"
        verbose_name_plural = "Devis"
        ordering = ["-date_creation", "numero"]

    def __str__(self):
        return self.numero


class DevisLigne(models.Model):
    devis = models.ForeignKey(Devis, on_delete=models.CASCADE, related_name="lignes")
    article = models.ForeignKey(Article, on_delete=models.PROTECT, related_name="devis_lignes")
    quantite = models.FloatField()
    cout_matiere_calcule = models.FloatField(null=True, blank=True, editable=False)
    taux_marge_matiere_applique = models.FloatField(
        null=True, blank=True, help_text="Pré-rempli depuis l'article, éditable"
    )
    prix_vente_matiere = models.FloatField(null=True, blank=True, editable=False)

    class Meta:
        verbose_name = "Ligne de devis"
        verbose_name_plural = "Lignes de devis"
        ordering = ["devis", "id"]

    def __str__(self):
        return f"{self.devis} — {self.article} × {self.quantite}"


class DevisLigneOperation(models.Model):
    devis_ligne = models.ForeignKey(DevisLigne, on_delete=models.CASCADE, related_name="operations")
    poste = models.ForeignKey(PosteTravail, on_delete=models.PROTECT, related_name="devis_operations")
    ordre = models.PositiveIntegerField()
    cout_calcule = models.FloatField(null=True, blank=True, editable=False)
    taux_marge_applique = models.FloatField(
        null=True, blank=True, help_text="Pré-rempli depuis le poste, éditable"
    )
    prix_vente = models.FloatField(null=True, blank=True, editable=False)

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
    numero = models.CharField(max_length=50, primary_key=True)
    devis = models.ForeignKey(Devis, on_delete=models.PROTECT, related_name="commandes")
    date_commande = models.DateField()
    statut = models.CharField(max_length=50, blank=True)
    adresse_facturation = models.ForeignKey(
        Adresse, on_delete=models.PROTECT, related_name="commandes_facturation"
    )
    adresse_livraison = models.ForeignKey(
        Adresse, on_delete=models.PROTECT, related_name="commandes_livraison"
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

    numero = models.CharField(max_length=50, primary_key=True)
    commande = models.ForeignKey(Commande, on_delete=models.CASCADE, related_name="ordres_fabrication")
    article = models.ForeignKey(Article, on_delete=models.PROTECT, related_name="ordres_fabrication")
    quantite = models.FloatField()
    date_lancement = models.DateField()
    statut = models.CharField(max_length=50, blank=True, help_text="Statut de production")
    statut_synchro = models.CharField(
        max_length=20, choices=StatutSynchro.choices, default=StatutSynchro.EN_ATTENTE
    )
    nombre_tentatives = models.PositiveIntegerField(default=0)
    date_derniere_tentative = models.DateField(null=True, blank=True)

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
        OrdreFabrication, on_delete=models.CASCADE, related_name="operations"
    )
    poste = models.ForeignKey(PosteTravail, on_delete=models.PROTECT, related_name="operations_of")
    ordre = models.PositiveIntegerField()
    temps_prevu = models.FloatField(null=True, blank=True, help_text="Copié de la gamme au lancement")
    temps_reel = models.FloatField(null=True, blank=True, help_text="Alimenté par le planning atelier")
    quantite_bonne = models.FloatField(null=True, blank=True, help_text="Alimenté par le planning atelier")
    quantite_rebut = models.FloatField(null=True, blank=True, help_text="Alimenté par le planning atelier")
    statut = models.CharField(max_length=50, blank=True)

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
