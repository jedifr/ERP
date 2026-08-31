from django.core.exceptions import ValidationError
from django.db import models


class Tiers(models.Model):
    """Entité unique pour client et/ou fournisseur (un même acteur peut être les deux)."""

    class TypeTiers(models.TextChoices):
        CLIENT = "client", "Client"
        FOURNISSEUR = "fournisseur", "Fournisseur"
        LES_DEUX = "les_deux", "Les deux"

    code = models.CharField(max_length=50, primary_key=True)
    raison_sociale = models.CharField(max_length=200)
    type_tiers = models.CharField(max_length=20, choices=TypeTiers.choices)
    siret = models.CharField(
        max_length=14, blank=True, help_text="Obligatoire pour la facturation électronique"
    )
    numero_tva = models.CharField(max_length=20, blank=True, help_text="TVA intracommunautaire")
    conditions_paiement = models.CharField(
        max_length=200, blank=True, help_text="Valeur par défaut, reprise sur devis/commande"
    )

    class Meta:
        verbose_name = "Tiers"
        verbose_name_plural = "Tiers"
        ordering = ["raison_sociale"]

    def __str__(self):
        return f"{self.code} — {self.raison_sociale}"


class Adresse(models.Model):
    """Un tiers peut avoir plusieurs adresses de facturation et plusieurs adresses de livraison."""

    class TypeAdresse(models.TextChoices):
        FACTURATION = "facturation", "Facturation"
        LIVRAISON = "livraison", "Livraison"

    tiers = models.ForeignKey(Tiers, on_delete=models.CASCADE, related_name="adresses")
    type_adresse = models.CharField(max_length=20, choices=TypeAdresse.choices)
    libelle = models.CharField(max_length=100, blank=True, help_text='Ex. "Siège", "Entrepôt Nord"')
    adresse = models.CharField(max_length=255)
    code_postal = models.CharField(max_length=20)
    ville = models.CharField(max_length=100)
    est_principale = models.BooleanField(default=False, help_text="Adresse par défaut proposée")

    class Meta:
        verbose_name = "Adresse"
        verbose_name_plural = "Adresses"
        ordering = ["tiers", "type_adresse"]

    def __str__(self):
        return f"{self.tiers} — {self.get_type_adresse_display()} ({self.libelle or self.ville})"

    def clean(self):
        super().clean()
        if self.est_principale and self.tiers_id and self.type_adresse:
            qs = Adresse.objects.filter(
                tiers_id=self.tiers_id, type_adresse=self.type_adresse, est_principale=True
            )
            if self.pk is not None:
                qs = qs.exclude(pk=self.pk)
            if qs.exists():
                raise ValidationError(
                    {
                        "est_principale": (
                            "Une adresse principale existe déjà pour ce tiers et ce type "
                            "d'adresse. Décochez-la d'abord si vous voulez la remplacer."
                        )
                    }
                )


class Contact(models.Model):
    tiers = models.ForeignKey(Tiers, on_delete=models.CASCADE, related_name="contacts")
    nom = models.CharField(max_length=100)
    prenom = models.CharField(max_length=100, blank=True)
    email = models.EmailField(blank=True)
    telephone = models.CharField(max_length=30, blank=True)
    fonction = models.CharField(max_length=100, blank=True)

    class Meta:
        verbose_name = "Contact"
        verbose_name_plural = "Contacts"
        ordering = ["tiers", "nom"]

    def __str__(self):
        nom_complet = f"{self.prenom} {self.nom}".strip()
        return f"{nom_complet} ({self.tiers})"
