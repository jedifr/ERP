"""Arithmétique monétaire : montants et prix en `Decimal`, quantités en flottants.

Règles (une seule fois, ici) :
- les montants (HT, TTC, débit, crédit, prix de vente de ligne) sont au centime ;
- les prix et coûts unitaires gardent 4 décimales (0,0035 € la vis existe) ;
- les taux (TVA, marges) ont 2 décimales ;
- l'arrondi est « au plus proche, 0,5 vers le haut » (arrondi commercial, celui de Tiime),
  appliqué ligne par ligne ; les totaux sont la somme des lignes arrondies ;
- les quantités, durées et dimensions restent des flottants (grandeurs physiques) :
  on les convertit avec `D()` au moment de les multiplier par un prix.
"""

from decimal import ROUND_HALF_UP, Decimal

ZERO = Decimal("0")
CENTIME = Decimal("0.01")
DIX_MILLIEME = Decimal("0.0001")

# Paramètres des champs DecimalField du projet.
MONTANT = {"max_digits": 14, "decimal_places": 2}
PRIX = {"max_digits": 14, "decimal_places": 4}
TAUX = {"max_digits": 7, "decimal_places": 2}


def D(valeur):
    """Nombre quelconque (int, float, str, Decimal, None) -> Decimal. None reste None.
    Les flottants passent par leur écriture décimale la plus courte : D(0.1) == Decimal("0.1")."""
    if valeur is None or isinstance(valeur, Decimal):
        return valeur
    return Decimal(str(valeur))


def D0(valeur):
    """Comme D(), mais None vaut zéro (pratique dans les sommes)."""
    return ZERO if valeur is None else D(valeur)


def arrondir(valeur, pas=CENTIME):
    """Arrondi commercial au `pas` (le centime par défaut). None reste None."""
    if valeur is None:
        return None
    return D(valeur).quantize(pas, rounding=ROUND_HALF_UP)


def arrondir_prix(valeur):
    return arrondir(valeur, DIX_MILLIEME)


def somme(valeurs):
    """Somme exacte de nombres hétérogènes (None ignoré), en Decimal."""
    total = ZERO
    for v in valeurs:
        if v is not None:
            total += D(v)
    return total


def tva(montant_ht, taux_pct):
    """TVA d'un montant HT (taux en %), arrondie au centime."""
    return arrondir(D0(montant_ht) * D0(taux_pct) / 100)


def ttc(montant_ht, taux_pct):
    ht = arrondir(montant_ht)
    return ht + tva(ht, taux_pct)

# Prix de vente unitaire (lignes de commande et de facture) : 6 décimales, pour qu'un prix
# ramené à l'unité (total / quantité) redonne au centime le total d'origine, même sur de
# grandes quantités.
PRIX_VENTE = {"max_digits": 16, "decimal_places": 6}
MICRO = Decimal("0.000001")


def arrondir_prix_vente(valeur):
    return arrondir(valeur, MICRO)


def pourcent(taux):
    """Taux sans zéros inutiles : Decimal('20.00') -> '20', Decimal('5.50') -> '5.5'."""
    return format(D(taux).normalize(), "f")
