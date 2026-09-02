from django.db import models


class RegleCodification(models.Model):
    """Règle de génération automatique d'un code/numéro (préfixe + numéro sur
    N chiffres), configurable par entité. Le code proposé pré-remplit le
    formulaire d'ajout de l'entité concernée mais reste un champ texte normal,
    modifiable avant enregistrement — voir codification/services.py.
    """

    class Entite(models.TextChoices):
        DEVIS = "devis", "Devis"
        COMMANDE = "commande", "Commande"
        ORDRE_FABRICATION = "ordre_fabrication", "Ordre de fabrication"
        COMMANDE_FOURNISSEUR = "commande_fournisseur", "Commande fournisseur"
        RECEPTION = "reception", "Réception"
        LIVRAISON = "livraison", "Livraison"
        FACTURE = "facture", "Facture"
        ENVOI_SOUS_TRAITANCE = "envoi_sous_traitance", "Envoi sous-traitance"
        RETOUR_SOUS_TRAITANCE = "retour_sous_traitance", "Retour sous-traitance"
        TIERS = "tiers", "Tiers"
        EMPLACEMENT = "emplacement", "Emplacement"

    class Reinitialisation(models.TextChoices):
        JAMAIS = "jamais", "Jamais (compteur continu)"
        ANNUELLE = "annuelle", "Chaque année"

    entite = models.CharField("entité", max_length=30, choices=Entite.choices, primary_key=True)
    prefixe = models.CharField("préfixe", max_length=20, blank=True, help_text='Ex. "DEV-"')
    nombre_chiffres = models.PositiveSmallIntegerField(
        "nombre de chiffres",
        default=5,
        help_text="Largeur du numéro, complété par des zéros à gauche (ex. 5 → 00001)",
    )
    reinitialisation = models.CharField(
        "réinitialisation",
        max_length=20,
        choices=Reinitialisation.choices,
        default=Reinitialisation.JAMAIS,
        help_text="Si annuelle, l'année en cours (4 chiffres) est insérée entre le préfixe et le numéro",
    )
    compteur_actuel = models.PositiveIntegerField(
        "compteur actuel",
        default=0,
        help_text="Dernier numéro attribué — le prochain code proposé utilisera compteur + 1",
    )
    annee_compteur = models.PositiveIntegerField(
        "année du compteur",
        null=True,
        blank=True,
        editable=False,
        help_text="Année sur laquelle porte le compteur actuel (réinitialisation annuelle uniquement)",
    )

    class Meta:
        verbose_name = "Règle de codification"
        verbose_name_plural = "Règles de codification"
        ordering = ["entite"]

    def __str__(self):
        return self.get_entite_display()
