from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone
from simple_history.models import HistoricalRecords

from chiffrage.models import Commande


class FactureVerrouilleeError(Exception):
    """Une facture émise ou comptabilisée ne se modifie plus (hors paiement) et ne se supprime pas."""


# Champs qui ne bougent plus dès que la facture est émise (référence Tiime
# renseignée) ou comptabilisée : seul le suivi du paiement reste libre. Une
# erreur se corrige par un avoir, jamais en réécrivant la facture.
CHAMPS_FIGES = ("commande_id", "montant_ht", "montant_ttc", "date_facturation", "reference_tiime", "mode_creation")


def facture_verrouillee(facture):
    """Lu en base (jamais sur l'instance, qu'un formulaire ou un serializer a
    déjà pu modifier) : facture émise dans Tiime ou déjà passée en comptabilité."""
    if facture is None or facture.pk is None:
        return False
    from comptabilite.models import EcritureComptable

    if Facture.objects.filter(pk=facture.pk).exclude(reference_tiime="").exists():
        return True
    return EcritureComptable.objects.filter(facture_id=facture.pk).exists()


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
    montant_ht = models.FloatField(
        "montant HT", null=True, blank=True, validators=[MinValueValidator(0, message="Ne peut pas être négatif.")]
    )
    montant_ttc = models.FloatField(
        "montant TTC", null=True, blank=True, validators=[MinValueValidator(0, message="Ne peut pas être négatif.")]
    )
    date_facturation = models.DateField("date de facturation")

    class StatutPaiement(models.TextChoices):
        A_PAYER = "a_payer", "À payer"
        PARTIEL = "partiel", "Partiellement payée"
        PAYE = "paye", "Payée"

    statut_paiement = models.CharField(
        "statut de paiement", max_length=20, choices=StatutPaiement.choices, default=StatutPaiement.A_PAYER
    )
    date_paiement = models.DateField(
        "date de paiement", null=True, blank=True, help_text="Date du dernier règlement ; posée automatiquement au passage à « Payée »."
    )
    mode_creation = models.CharField(
        "mode de création", max_length=20, choices=ModeCreation.choices, default=ModeCreation.MANUEL
    )

    history = HistoricalRecords()

    class Meta:
        verbose_name = "Facture"
        verbose_name_plural = "Factures"
        ordering = ["-date_facturation", "numero"]

    def __str__(self):
        return self.numero

    def clean(self):
        super().clean()
        if self.commande_id and self.date_facturation:
            if self.date_facturation < self.commande.date_commande:
                raise ValidationError(
                    {"date_facturation": "La facture ne peut pas être antérieure à la commande "
                     f"({self.commande.date_commande:%d/%m/%Y})."}
                )
        if self.date_facturation and self.date_facturation > timezone.localdate():
            raise ValidationError({"date_facturation": "La date de facturation ne peut pas être dans le futur."})
        if self.montant_ht is not None and self.montant_ttc is not None and self.montant_ttc < self.montant_ht - 0.005:
            raise ValidationError({"montant_ttc": "Le montant TTC ne peut pas être inférieur au montant HT."})
        if self.date_paiement and self.date_facturation and self.date_paiement < self.date_facturation:
            raise ValidationError({"date_paiement": "Le paiement ne peut pas précéder la facture."})
        if self.pk is not None and facture_verrouillee(self):
            en_base = Facture.objects.filter(pk=self.pk).values(*CHAMPS_FIGES).first()
            for champ in CHAMPS_FIGES:
                if en_base and getattr(self, champ) != en_base[champ]:
                    nom = champ[:-3] if champ.endswith("_id") else champ
                    raise ValidationError(
                        {nom: "Cette facture est émise ou comptabilisée : ce champ ne se modifie plus "
                              "(corrigez par un avoir)."}
                    )

    def save(self, *args, **kwargs):
        if self.montant_ht is not None:
            self.montant_ht = round(self.montant_ht, 2)
        if self.montant_ttc is not None:
            self.montant_ttc = round(self.montant_ttc, 2)
        if self.statut_paiement == self.StatutPaiement.PAYE and self.date_paiement is None:
            self.date_paiement = timezone.localdate()
        if self.pk is not None and facture_verrouillee(self):
            en_base = Facture.objects.filter(pk=self.pk).values(*CHAMPS_FIGES).first()
            if en_base and any(getattr(self, champ) != en_base[champ] for champ in CHAMPS_FIGES):
                raise FactureVerrouilleeError(
                    "Cette facture est émise ou comptabilisée : seul son paiement se modifie (corrigez par un avoir)."
                )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if facture_verrouillee(self):
            raise FactureVerrouilleeError(
                "Une facture émise ou comptabilisée ne se supprime pas (continuité de la numérotation) : émettez un avoir."
            )
        return super().delete(*args, **kwargs)

    @property
    def date_echeance(self):
        """Date d'échéance calculée à partir des conditions de paiement du
        client (délai en jours, éventuellement reporté en fin de mois).
        None si le client n'a pas de conditions de paiement renseignées, ou
        si celles-ci sont purement descriptives (pas de délai chiffré)."""
        conditions = self.commande.client.conditions_paiement
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
        return round(sum(montants), 2) if montants else None

    @property
    def montant_ttc_calcule(self):
        montants = [l.montant_ttc for l in self.commande.lignes.all() if l.montant_ttc is not None]
        return round(sum(montants), 2) if montants else None
