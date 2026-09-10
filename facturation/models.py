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

    @property
    def date_echeance(self):
        """Date d'échéance calculée à partir des conditions de paiement du
        client (délai en jours, éventuellement reporté en fin de mois).
        None si le client n'a pas de conditions de paiement renseignées, ou
        si celles-ci sont purement descriptives (pas de délai chiffré)."""
        conditions = self.commande.devis.client.conditions_paiement
        if conditions is None:
            return None
        return conditions.calculer_echeance(self.date_facturation)

    @property
    def montant_ht_calcule(self):
        """Total HT indicatif recalculé depuis les lignes actuelles de la
        commande (CommandeLigne.montant_ht) — n'est jamais écrit dans
        montant_ht, qui reste la valeur saisie à la main depuis la facture
        réelle (émise dans Tiime, qui fait foi et peut différer :
        facturation partielle d'une commande sur plusieurs factures,
        remise, arrondi...). Ignore les lignes sans prix renseigné, comme
        comptabilite.generation._repartition_lignes. None si la commande
        n'a aucune ligne chiffrée."""
        montants = [l.montant_ht for l in self.commande.lignes.all() if l.montant_ht is not None]
        return sum(montants) if montants else None

    @property
    def montant_ttc_calcule(self):
        montants = [l.montant_ttc for l in self.commande.lignes.all() if l.montant_ttc is not None]
        return sum(montants) if montants else None
