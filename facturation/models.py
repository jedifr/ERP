from django.db import models

from chiffrage.models import Commande


class Facture(models.Model):
    """La facture légale vit dans Tiime (plateforme agréée, conforme à la
    réforme de facturation électronique). Ce modèle reste une trace côté ERP.

    Flux retenu pour démarrer : facturation créée manuellement dans Tiime,
    référence renseignée ensuite ici.
    """

    class ModeCreation(models.TextChoices):
        MANUEL = "manuel", "Manuel"
        AUTOMATIQUE = "automatique", "Automatique"

    numero = models.CharField(max_length=50, primary_key=True)
    commande = models.ForeignKey(Commande, on_delete=models.PROTECT, related_name="factures")
    reference_tiime = models.CharField(max_length=100, blank=True)
    montant_ht = models.FloatField(null=True, blank=True)
    montant_ttc = models.FloatField(null=True, blank=True)
    date_facturation = models.DateField()
    statut_paiement = models.CharField(max_length=50, blank=True)
    mode_creation = models.CharField(
        max_length=20, choices=ModeCreation.choices, default=ModeCreation.MANUEL
    )

    class Meta:
        verbose_name = "Facture"
        verbose_name_plural = "Factures"
        ordering = ["-date_facturation", "numero"]

    def __str__(self):
        return self.numero
