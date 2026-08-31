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

    numero = models.CharField("numéro", max_length=50, primary_key=True)
    commande = models.ForeignKey(
        Commande, verbose_name="commande", on_delete=models.PROTECT, related_name="factures"
    )
    reference_tiime = models.CharField("référence Tiime", max_length=100, blank=True)
    montant_ht = models.FloatField("montant HT", null=True, blank=True)
    montant_ttc = models.FloatField("montant TTC", null=True, blank=True)
    date_facturation = models.DateField("date de facturation")
    statut_paiement = models.CharField("statut de paiement", max_length=50, blank=True)
    mode_creation = models.CharField(
        "mode de création", max_length=20, choices=ModeCreation.choices, default=ModeCreation.MANUEL
    )

    class Meta:
        verbose_name = "Facture"
        verbose_name_plural = "Factures"
        ordering = ["-date_facturation", "numero"]

    def __str__(self):
        return self.numero
