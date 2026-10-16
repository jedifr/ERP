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
