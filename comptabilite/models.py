from django.db import models


class CompteComptable(models.Model):
    """Compte du plan comptable général (PCG) français. Peut être saisi à la
    main ou importé en masse depuis le jeu de données officiel embarqué
    (voir comptabilite.pcg.importer_pcg, exposé dans l'admin par l'action
    "Importer le plan comptable officiel" sur la liste)."""

    class Systeme(models.TextChoices):
        BASE = "minimal", "Système de base"
        DEVELOPPE = "facultatif", "Système développé"

    code = models.CharField("code", max_length=10, primary_key=True)
    libelle = models.CharField("libellé", max_length=255)
    classe = models.PositiveSmallIntegerField(
        "classe", editable=False, help_text="1er chiffre du code (1 à 8) — déduite automatiquement du code"
    )
    compte_parent = models.ForeignKey(
        "self",
        verbose_name="compte parent",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="sous_comptes",
    )
    systeme = models.CharField(
        "système",
        max_length=20,
        choices=Systeme.choices,
        default=Systeme.BASE,
        help_text="Système de base (comptes obligatoires) ou développé (détail facultatif) du PCG",
    )
    actif = models.BooleanField("actif", default=True)

    class Meta:
        verbose_name = "Compte comptable"
        verbose_name_plural = "Plan comptable"
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} — {self.libelle}"

    def save(self, *args, **kwargs):
        self.classe = int(self.code[0])
        super().save(*args, **kwargs)


class JournalComptable(models.Model):
    """Journal comptable (dictionnaire des journaux, au sens Dolibarr) :
    regroupe les écritures par nature. Le jeu par défaut (migration 0002)
    reprend les journaux standards ; l'admin permet d'en ajouter librement
    (ex. un journal de banque par compte bancaire, BQ1/BQ2/...)."""

    class Nature(models.TextChoices):
        ACHATS = "achats", "Achats"
        VENTES = "ventes", "Ventes"
        BANQUE = "banque", "Banque"
        CAISSE = "caisse", "Caisse"
        NOTES_DE_FRAIS = "notes_de_frais", "Notes de frais"
        OPERATIONS_DIVERSES = "operations_diverses", "Opérations diverses"
        REPORTS_A_NOUVEAU = "reports_a_nouveau", "Reports à nouveaux"

    code = models.CharField("code", max_length=10, primary_key=True)
    libelle = models.CharField("libellé", max_length=200)
    nature = models.CharField("nature du journal", max_length=30, choices=Nature.choices)
    actif = models.BooleanField("état", default=True)

    class Meta:
        verbose_name = "Journal comptable"
        verbose_name_plural = "Journaux comptables"
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} — {self.libelle}"
