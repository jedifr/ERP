from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models

from comptes.champs import ChampDecimal
from comptes.montants import MONTANT, TAUX


class ReglageWeb(models.Model):
    """Grille de prix du configurateur web (une seule ligne). Les tôles proposées en ligne se cochent sur leur fiche article
    (« vendu en ligne »)."""

    taux_marge_pct = ChampDecimal(
        "marge web (%)", default=Decimal("20"), validators=[MinValueValidator(Decimal("0"))],
        help_text="Ajoutée au coût de revient (matière, découpe, pliage) pour le prix affiché en ligne.", **TAUX,
    )
    minimum_commande_ht = ChampDecimal(
        "minimum de commande (€ HT)", default=Decimal("60"), validators=[MinValueValidator(Decimal("0"))],
        help_text="Hors frais de port : un panier en dessous est complété jusqu'à ce montant.", **MONTANT,
    )
    quantite_max = models.PositiveIntegerField(
        "quantité max par pièce", default=500, help_text="Au-delà, le configurateur renvoie vers une demande de devis.",
    )

    class Meta:
        verbose_name = "Réglage de la vente en ligne"
        verbose_name_plural = "Réglage de la vente en ligne"

    def __str__(self):
        return "Réglage de la vente en ligne"

    @classmethod
    def charger(cls):
        return cls.objects.get_or_create(pk=1)[0]


class DemandeWeb(models.Model):
    """Panier envoyé depuis le site client : coordonnées et pièces, prix recalculés par l'ERP à la réception. Le commercial
    la transforme en devis (ou la classe) ; rien n'est lancé en fabrication sans lui."""

    class Statut(models.TextChoices):
        NOUVELLE = "nouvelle", "Nouvelle"
        TRAITEE = "traitee", "Traitée (devis fait)"
        ABANDONNEE = "abandonnee", "Abandonnée"

    numero = models.CharField("numéro", max_length=20, unique=True, editable=False)
    date = models.DateTimeField("reçue le", auto_now_add=True)
    statut = models.CharField("statut", max_length=12, choices=Statut.choices, default=Statut.NOUVELLE)
    nom = models.CharField("nom", max_length=120)
    societe = models.CharField("société", max_length=160, blank=True)
    email = models.EmailField("e-mail")
    telephone = models.CharField("téléphone", max_length=30, blank=True)
    adresse = models.TextField("adresse de livraison", blank=True)
    code_postal = models.CharField("code postal", max_length=20, blank=True)
    ville = models.CharField("ville", max_length=100, blank=True)
    message = models.TextField("message", blank=True)
    lignes = models.JSONField("pièces", default=list, help_text="Demande du client et prix recalculé par l'ERP, ligne par ligne.")
    total_pieces_ht = ChampDecimal("total des pièces (€ HT)", **MONTANT)
    total_ht = ChampDecimal("total (€ HT)", help_text="Minimum de commande appliqué, hors port.", **MONTANT)
    a_valider = models.BooleanField("à valider par un technicien", default=False)
    note_interne = models.TextField("note interne", blank=True)
    devis = models.ForeignKey(
        "chiffrage.Devis", verbose_name="devis créé", on_delete=models.SET_NULL, null=True, blank=True, related_name="demandes_web",
        help_text="Devis créé par « Valider » (client retrouvé par son e-mail ou créé).",
    )

    class Meta:
        verbose_name = "Demande web"
        verbose_name_plural = "Demandes web"
        ordering = ["-date"]

    def __str__(self):
        return f"{self.numero} – {self.societe or self.nom}"

    def save(self, *args, **kwargs):
        if not self.numero:
            from django.utils import timezone

            prefixe = f"WEB{timezone.localdate():%y%m}-"
            dernier = DemandeWeb.objects.filter(numero__startswith=prefixe).order_by("-numero").values_list("numero", flat=True).first()
            self.numero = f"{prefixe}{int(dernier.rsplit('-', 1)[1]) + 1 if dernier else 1:04d}"
        super().save(*args, **kwargs)
