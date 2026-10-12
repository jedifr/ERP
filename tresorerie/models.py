"""Trésorerie : règlements reçus des clients et versés aux fournisseurs, échéanciers, lots de virements SEPA.

Un règlement se rattache à UNE facture (client ou fournisseur) ; la somme de ses règlements fait le
statut de paiement de la facture (à payer, partiellement payée, payée) et sa date de paiement, que
l'export comptable reprend dans le journal de banque. Les relances de paiement restent celles de la
facturation (facturation.relances), proposées ici depuis l'échéancier client."""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Max, Sum
from django.utils import timezone

from achats.models import FactureFournisseur
from comptes.champs import ChampDecimal
from comptes.montants import MONTANT, ZERO, D, arrondir
from facturation.models import Facture

# Statut texte posé sur FactureFournisseur.statut_paiement (champ libre côté achats).
STATUT_FOURNISSEUR_PAYEE = "Payée"
STATUT_FOURNISSEUR_PARTIEL = "Partiellement payée"
STATUT_FOURNISSEUR_A_PAYER = "À payer"


def montant_du(facture):
    """TTC à régler : celui saisi sur la facture, à défaut celui calculé depuis ses lignes (client). None si inconnu."""
    if isinstance(facture, Facture):
        return D(facture.montant_ttc) if facture.montant_ttc is not None else facture.montant_ttc_calcule
    return D(facture.montant_ttc)


def total_regle(facture, exclure_pk=None):
    reglements = facture.reglements.all()
    if exclure_pk:
        reglements = reglements.exclude(pk=exclure_pk)
    return arrondir(reglements.aggregate(total=Sum("montant"))["total"] or ZERO)


def reste_a_payer(facture):
    du = montant_du(facture)
    if du is None:
        return None
    return max(arrondir(du - total_regle(facture)), ZERO)


def echeance_fournisseur(facture):
    """Échéance d'une facture fournisseur selon les conditions de paiement du fournisseur (None si inconnues)."""
    conditions = facture.commande_fournisseur.fournisseur.conditions_paiement
    return conditions.calculer_echeance(facture.date_facture) if conditions else None


def mettre_a_jour_paiement(facture):
    """Recalcule statut et date de paiement d'une facture d'après ses règlements."""
    total = total_regle(facture)
    du = montant_du(facture)
    derniere = facture.reglements.aggregate(d=Max("date_reglement"))["d"]
    solde = du is not None and total > 0 and total >= du
    if isinstance(facture, Facture):
        if solde:
            facture.statut_paiement, facture.date_paiement = Facture.StatutPaiement.PAYE, derniere
        elif total > 0:
            facture.statut_paiement, facture.date_paiement = Facture.StatutPaiement.PARTIEL, derniere
        else:
            facture.statut_paiement, facture.date_paiement = Facture.StatutPaiement.A_PAYER, None
    else:
        # La date de règlement d'une facture fournisseur fait passer tout son TTC au journal de banque :
        # elle n'est posée qu'une fois la facture soldée.
        if solde:
            facture.statut_paiement, facture.date_paiement = STATUT_FOURNISSEUR_PAYEE, derniere
        elif total > 0:
            facture.statut_paiement, facture.date_paiement = STATUT_FOURNISSEUR_PARTIEL, None
        else:
            facture.statut_paiement, facture.date_paiement = STATUT_FOURNISSEUR_A_PAYER, None
    facture.save(update_fields=["statut_paiement", "date_paiement"])


class Reglement(models.Model):
    class Sens(models.TextChoices):
        ENCAISSEMENT = "encaissement", "Encaissement (client)"
        DECAISSEMENT = "decaissement", "Décaissement (fournisseur)"

    class Mode(models.TextChoices):
        VIREMENT = "virement", "Virement"
        CHEQUE = "cheque", "Chèque"
        CARTE = "carte", "Carte bancaire"
        PRELEVEMENT = "prelevement", "Prélèvement"
        LCR = "lcr", "Effet / LCR"
        ESPECES = "especes", "Espèces"
        COMPENSATION = "compensation", "Compensation"

    sens = models.CharField("sens", max_length=20, choices=Sens.choices, editable=False)
    facture = models.ForeignKey(
        Facture, verbose_name="facture client", on_delete=models.PROTECT, null=True, blank=True, related_name="reglements",
        limit_choices_to={"type_document": Facture.TypeDocument.FACTURE},
    )
    facture_fournisseur = models.ForeignKey(
        FactureFournisseur, verbose_name="facture fournisseur", on_delete=models.PROTECT, null=True, blank=True,
        related_name="reglements",
    )
    date_reglement = models.DateField("date du règlement", default=timezone.localdate)
    montant = ChampDecimal("montant", help_text="€ TTC réglés", **MONTANT)
    mode = models.CharField("mode de règlement", max_length=20, choices=Mode.choices, default=Mode.VIREMENT)
    reference = models.CharField(
        "référence", max_length=100, blank=True, help_text="N° de chèque, libellé du relevé bancaire, référence du virement…"
    )
    lot_virement = models.ForeignKey(
        "LotVirement", verbose_name="lot de virements", on_delete=models.PROTECT, null=True, blank=True,
        related_name="reglements", editable=False,
    )
    notes = models.CharField("notes", max_length=255, blank=True)
    utilisateur = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="saisi par", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="reglements_saisis", editable=False,
    )
    date_saisie = models.DateTimeField("saisi le", auto_now_add=True)

    class Meta:
        verbose_name = "Règlement"
        verbose_name_plural = "Règlements"
        ordering = ["-date_reglement", "-id"]

    def __str__(self):
        return f"{self.get_mode_display()} {self.montant} € — {self.facture_id or self.facture_fournisseur_id}"

    @property
    def document(self):
        return self.facture or self.facture_fournisseur

    @property
    def tiers(self):
        if self.facture_id:
            return self.facture.commande.client
        if self.facture_fournisseur_id:
            return self.facture_fournisseur.commande_fournisseur.fournisseur
        return None

    def clean(self):
        super().clean()
        if bool(self.facture_id) == bool(self.facture_fournisseur_id):
            raise ValidationError("Rattachez le règlement à une facture client OU à une facture fournisseur.")
        if self.montant is not None and D(self.montant) <= 0:
            raise ValidationError({"montant": "Le montant doit être positif."})
        if self.date_reglement and self.date_reglement > timezone.localdate():
            raise ValidationError({"date_reglement": "Un règlement ne peut pas être dans le futur."})
        facture = self.document
        if self.facture_id:
            if facture.est_avoir:
                raise ValidationError({"facture": "Un avoir ne se règle pas : il vient en déduction de la facture."})
            if self.date_reglement and self.date_reglement < facture.date_facturation:
                raise ValidationError({"date_reglement": "Le règlement ne peut pas précéder la facture."})
        du = montant_du(facture)
        if du is None:
            raise ValidationError("Renseignez d'abord le montant TTC de la facture.")
        if self.montant is not None:
            reste = arrondir(du - total_regle(facture, exclure_pk=self.pk))
            if D(self.montant) > reste:
                raise ValidationError(
                    {"montant": f"Dépasse le reste à payer de {facture} ({reste} € sur {du} € TTC)."}
                )

    def save(self, *args, **kwargs):
        self.sens = self.Sens.ENCAISSEMENT if self.facture_id else self.Sens.DECAISSEMENT
        self.montant = arrondir(self.montant)
        with transaction.atomic():
            ancienne = None
            if self.pk:
                ancienne = Reglement.objects.filter(pk=self.pk).values("facture_id", "facture_fournisseur_id").first()
            super().save(*args, **kwargs)
            mettre_a_jour_paiement(self.document)
            if ancienne and (ancienne["facture_id"], ancienne["facture_fournisseur_id"]) != (
                self.facture_id, self.facture_fournisseur_id
            ):
                if ancienne["facture_id"]:
                    mettre_a_jour_paiement(Facture.objects.get(pk=ancienne["facture_id"]))
                else:
                    mettre_a_jour_paiement(FactureFournisseur.objects.get(pk=ancienne["facture_fournisseur_id"]))

    def delete(self, *args, **kwargs):
        if self.lot_virement_id:
            raise ValidationError("Ce règlement vient d'un lot de virements exécuté : il ne se supprime pas.")
        document = self.document
        with transaction.atomic():
            resultat = super().delete(*args, **kwargs)
            mettre_a_jour_paiement(document)
        return resultat


class LotVirement(models.Model):
    """Lot de virements fournisseurs, remis à la banque sous forme d'un fichier XML SEPA (pain.001)."""

    class Statut(models.TextChoices):
        BROUILLON = "brouillon", "Brouillon"
        GENERE = "genere", "Fichier généré"
        EXECUTE = "execute", "Exécuté"

    libelle = models.CharField("libellé", max_length=100, blank=True, help_text="Ex. « Fournisseurs fin octobre »")
    date_execution = models.DateField(
        "date d'exécution", default=timezone.localdate, help_text="Date à laquelle la banque doit exécuter les virements."
    )
    statut = models.CharField("statut", max_length=20, choices=Statut.choices, default=Statut.BROUILLON, editable=False)
    message_id = models.CharField("identifiant du fichier", max_length=35, blank=True, editable=False)
    xml = models.TextField("fichier XML", blank=True, editable=False)
    date_generation = models.DateTimeField("généré le", null=True, blank=True, editable=False)
    date_creation = models.DateTimeField("créé le", auto_now_add=True)
    cree_par = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="créé par", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="lots_virement", editable=False,
    )

    class Meta:
        verbose_name = "Lot de virements"
        verbose_name_plural = "Lots de virements"
        ordering = ["-date_execution", "-id"]
        permissions = [("executer_lotvirement", "Peut marquer un lot de virements comme exécuté")]

    def __str__(self):
        return f"Lot {self.pk}" + (f" — {self.libelle}" if self.libelle else "")

    @property
    def modifiable(self):
        return self.statut != self.Statut.EXECUTE

    @property
    def total(self):
        return arrondir(self.lignes.aggregate(t=Sum("montant"))["t"] or ZERO)

    def delete(self, *args, **kwargs):
        if not self.modifiable:
            raise ValidationError("Un lot exécuté ne se supprime pas : ses règlements sont enregistrés.")
        return super().delete(*args, **kwargs)


class LigneLotVirement(models.Model):
    lot = models.ForeignKey(LotVirement, verbose_name="lot", on_delete=models.CASCADE, related_name="lignes")
    facture_fournisseur = models.ForeignKey(
        FactureFournisseur, verbose_name="facture fournisseur", on_delete=models.PROTECT, related_name="lignes_virement"
    )
    montant = ChampDecimal("montant", null=True, blank=True, help_text="€ ; vide : le reste à payer de la facture.", **MONTANT)
    libelle = models.CharField(
        "libellé du virement", max_length=140, blank=True,
        help_text="Lu par le fournisseur sur son relevé ; vide : « Facture » + sa référence.",
    )

    class Meta:
        verbose_name = "Virement"
        verbose_name_plural = "Virements"
        ordering = ["lot", "id"]
        constraints = [models.UniqueConstraint(fields=["lot", "facture_fournisseur"], name="unique_facture_par_lot")]

    def __str__(self):
        return f"{self.facture_fournisseur} — {self.montant} €"

    @property
    def fournisseur(self):
        return self.facture_fournisseur.commande_fournisseur.fournisseur

    def libelle_effectif(self):
        facture = self.facture_fournisseur
        return self.libelle or f"Facture {facture.reference_fournisseur or facture.numero}"

    def clean(self):
        super().clean()
        if not self.facture_fournisseur_id or not self.lot_id:
            return
        if not self.lot.modifiable:
            raise ValidationError("Ce lot est exécuté : ses virements ne se modifient plus.")
        fournisseur = self.fournisseur
        if not fournisseur.iban:
            raise ValidationError(
                {"facture_fournisseur": f"Le fournisseur « {fournisseur} » n'a pas d'IBAN : renseignez-le sur sa fiche."}
            )
        autre = (
            LigneLotVirement.objects.filter(facture_fournisseur=self.facture_fournisseur)
            .exclude(lot=self.lot).exclude(lot__statut=LotVirement.Statut.EXECUTE).select_related("lot").first()
        )
        if autre:
            raise ValidationError({"facture_fournisseur": f"Cette facture est déjà dans le {autre.lot}, pas encore exécuté."})
        reste = reste_a_payer(self.facture_fournisseur)
        if reste is None:
            raise ValidationError({"facture_fournisseur": "Renseignez d'abord le montant TTC de la facture."})
        if self.montant is None:
            self.montant = reste
        if D(self.montant) <= 0:
            raise ValidationError({"montant": "Rien à payer sur cette facture." if reste == 0 else "Le montant doit être positif."})
        if D(self.montant) > reste:
            raise ValidationError({"montant": f"Dépasse le reste à payer ({reste} €)."})

    def save(self, *args, **kwargs):
        if self.montant is None:
            self.montant = reste_a_payer(self.facture_fournisseur)
        self.montant = arrondir(self.montant)
        super().save(*args, **kwargs)


class EcheanceClient(Facture):
    """Vue « trésorerie » des factures clients restant à encaisser."""

    class Meta:
        proxy = True
        verbose_name = "Échéance client"
        verbose_name_plural = "Échéancier clients"


class EcheanceFournisseur(FactureFournisseur):
    """Vue « trésorerie » des factures fournisseur restant à payer."""

    class Meta:
        proxy = True
        verbose_name = "Échéance fournisseur"
        verbose_name_plural = "Échéancier fournisseurs"
