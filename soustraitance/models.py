from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Sum
from django.utils import timezone

from chiffrage.models import OperationOF
from commercial.models import Tiers


class EnvoiSousTraitance(models.Model):
    numero = models.CharField(max_length=50, primary_key=True)
    operation_of = models.ForeignKey(
        OperationOF, on_delete=models.PROTECT, related_name="envois_sous_traitance"
    )
    sous_traitant = models.ForeignKey(Tiers, on_delete=models.PROTECT, related_name="envois_sous_traitance")
    date_envoi = models.DateField(default=timezone.now)
    quantite_envoyee = models.FloatField()
    statut = models.CharField(max_length=50, blank=True)

    class Meta:
        verbose_name = "Envoi en sous-traitance"
        verbose_name_plural = "Envois en sous-traitance"
        ordering = ["-date_envoi", "numero"]

    def __str__(self):
        return self.numero

    @property
    def quantite_retournee_totale(self):
        return self.retours.aggregate(total=Sum("quantite_retournee"))["total"] or 0


class RetourSousTraitance(models.Model):
    numero = models.CharField(max_length=50, primary_key=True)
    envoi = models.ForeignKey(EnvoiSousTraitance, on_delete=models.CASCADE, related_name="retours")
    date_retour = models.DateField(default=timezone.now)
    quantite_retournee = models.FloatField()
    conforme = models.BooleanField(default=True, help_text="Contrôle qualité simple")

    class Meta:
        verbose_name = "Retour de sous-traitance"
        verbose_name_plural = "Retours de sous-traitance"
        ordering = ["-date_retour", "numero"]

    def __str__(self):
        return self.numero

    def clean(self):
        super().clean()
        if self.quantite_retournee is not None and self.quantite_retournee <= 0:
            raise ValidationError({"quantite_retournee": "La quantité retournée doit être positive."})
        if self._state.adding and self.envoi_id:
            deja_retourne = self.envoi.quantite_retournee_totale
            if deja_retourne + (self.quantite_retournee or 0) > self.envoi.quantite_envoyee:
                raise ValidationError(
                    {
                        "quantite_retournee": (
                            f"Dépasse la quantité envoyée ({self.envoi.quantite_envoyee}, "
                            f"déjà retourné {deja_retourne})."
                        )
                    }
                )

    def save(self, *args, **kwargs):
        creation = self._state.adding
        super().save(*args, **kwargs)
        if creation:
            self._appliquer_a_operation()

    def _appliquer_a_operation(self):
        operation = self.envoi.operation_of
        if self.conforme:
            operation.quantite_bonne = (operation.quantite_bonne or 0) + self.quantite_retournee
        else:
            operation.quantite_rebut = (operation.quantite_rebut or 0) + self.quantite_retournee

        if self.envoi.quantite_retournee_totale >= self.envoi.quantite_envoyee:
            operation.statut = "terminee"
            EnvoiSousTraitance.objects.filter(pk=self.envoi_id).update(statut="retourne")

        operation.save(update_fields=["quantite_bonne", "quantite_rebut", "statut"])
