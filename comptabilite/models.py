from django.core.exceptions import ValidationError
from django.db import models

from facturation.models import Facture


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


class ParametresComptables(models.Model):
    """Ligne unique de paramétrage : journal et comptes par défaut utilisés
    pour générer automatiquement les écritures comptables (voir
    comptabilite.generation). Rien n'est figé dans le code — modifiable
    depuis l'admin pour s'adapter au plan comptable réellement utilisé.
    Si un compte n'est pas configuré ici, comptabilite.generation retombe
    sur les codes PCG usuels (411/706/44571) s'ils existent en base."""

    journal_ventes = models.ForeignKey(
        JournalComptable,
        verbose_name="journal des ventes",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    compte_client_defaut = models.ForeignKey(
        CompteComptable,
        verbose_name="compte client par défaut",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        help_text="Ex. 411 — Clients",
    )
    compte_vente_defaut = models.ForeignKey(
        CompteComptable,
        verbose_name="compte de vente par défaut",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        help_text="Ex. 706 — Prestations de services",
    )
    compte_tva_collectee_defaut = models.ForeignKey(
        CompteComptable,
        verbose_name="compte de TVA collectée par défaut",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        help_text="Ex. 44571 — TVA collectée",
    )

    class Meta:
        verbose_name = "Paramètres comptables"
        verbose_name_plural = "Paramètres comptables"

    def __str__(self):
        return "Paramètres comptables"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def charger(cls):
        """Renvoie la ligne unique de paramètres (créée au besoin), avec les
        comptes non configurés remplacés en mémoire par le code PCG usuel
        correspondant s'il existe en base — sans jamais écrire cette
        substitution : la valeur enregistrée reste ce que l'utilisateur a
        choisi (ou rien)."""
        parametres, _ = cls.objects.get_or_create(pk=1)
        codes_usuels = {
            "compte_client_defaut": "411",
            "compte_vente_defaut": "706",
            "compte_tva_collectee_defaut": "44571",
        }
        for champ, code in codes_usuels.items():
            if getattr(parametres, f"{champ}_id") is None:
                compte = CompteComptable.objects.filter(code=code).first()
                if compte is not None:
                    setattr(parametres, champ, compte)
        return parametres


class EcritureComptable(models.Model):
    """Écriture comptable (partie double) : un journal, une date, une pièce,
    et des lignes équilibrées (total débit == total crédit — imposé par
    LigneEcritureFormSet côté admin, voir comptabilite/admin.py). Peut être
    saisie manuellement ou générée automatiquement (voir
    comptabilite.generation.generer_ecriture_facture)."""

    journal = models.ForeignKey(
        JournalComptable, verbose_name="journal", on_delete=models.PROTECT, related_name="ecritures"
    )
    date_ecriture = models.DateField("date d'écriture")
    piece = models.CharField(
        "pièce", max_length=50, help_text="Référence de la pièce justificative (ex. numéro de facture)"
    )
    libelle = models.CharField("libellé", max_length=255)
    facture = models.OneToOneField(
        Facture,
        verbose_name="facture d'origine",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="ecriture_comptable",
        help_text="Renseigné automatiquement pour une écriture générée depuis une facture (empêche sa double génération).",
    )

    class Meta:
        verbose_name = "Écriture comptable"
        verbose_name_plural = "Écritures comptables"
        ordering = ["-date_ecriture", "-pk"]

    def __str__(self):
        return f"{self.journal.code} {self.piece} ({self.date_ecriture})"

    @property
    def total_debit(self):
        return sum(ligne.debit for ligne in self.lignes.all())

    @property
    def total_credit(self):
        return sum(ligne.credit for ligne in self.lignes.all())

    @property
    def est_equilibree(self):
        return round(self.total_debit - self.total_credit, 2) == 0


class LigneEcriture(models.Model):
    """Un mouvement (débit ou crédit, jamais les deux) sur un compte, au
    sein d'une écriture."""

    ecriture = models.ForeignKey(
        EcritureComptable, verbose_name="écriture", on_delete=models.CASCADE, related_name="lignes"
    )
    compte = models.ForeignKey(
        CompteComptable, verbose_name="compte", on_delete=models.PROTECT, related_name="lignes_ecriture"
    )
    libelle = models.CharField("libellé", max_length=255, blank=True)
    debit = models.FloatField("débit", default=0)
    credit = models.FloatField("crédit", default=0)

    class Meta:
        verbose_name = "Ligne d'écriture"
        verbose_name_plural = "Lignes d'écriture"
        ordering = ["ecriture", "id"]

    def __str__(self):
        return f"{self.compte} : {self.debit or self.credit}"

    def clean(self):
        super().clean()
        if (self.debit or 0) < 0 or (self.credit or 0) < 0:
            raise ValidationError("Les montants doivent être positifs.")
        if self.debit and self.credit:
            raise ValidationError("Une ligne d'écriture ne peut pas être à la fois au débit et au crédit.")
        if not self.debit and not self.credit:
            raise ValidationError("Une ligne d'écriture doit avoir un montant au débit ou au crédit.")
