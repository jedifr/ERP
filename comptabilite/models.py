from django.core.exceptions import ValidationError
from django.db import models

from commercial.models import Tiers
from facturation.models import Facture
from technique.models import Article


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


class CodeAnalytique(models.Model):
    """Code comptable complémentaire (comptabilité analytique) : axe
    secondaire optionnel apposé sur une ligne d'écriture (LigneEcriture),
    en plus du compte du plan comptable général — ex. suivi par atelier,
    par chantier, par centre de coût. Peut aussi être posé par défaut sur
    un article (ArticleCompteVente/ArticleCompteAchat) pour être repris
    automatiquement à la génération d'une écriture. Dictionnaire libre,
    sur le même principe que JournalComptable : l'admin permet d'en créer
    autant que nécessaire."""

    code = models.CharField("code", max_length=20, primary_key=True)
    libelle = models.CharField("libellé", max_length=200)
    actif = models.BooleanField("actif", default=True)

    class Meta:
        verbose_name = "Code analytique"
        verbose_name_plural = "Codes analytiques"
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} — {self.libelle}"


class TiersCompteComptable(models.Model):
    """Comptes comptables auxiliaires d'un tiers — un compte client (ex.
    sous-compte de 411) et/ou un compte fournisseur (ex. sous-compte de
    401), un tiers "les deux" pouvant avoir besoin des deux à la fois.
    Prioritaire sur ParametresComptables.compte_client_defaut lors de la
    génération d'une écriture de vente ; le côté fournisseur reste
    déclaratif pour l'instant (pas de génération d'écriture d'achat, voir
    ArticleCompteAchat)."""

    tiers = models.OneToOneField(
        Tiers, verbose_name="tiers", on_delete=models.CASCADE, related_name="comptes_comptables"
    )
    compte_client = models.ForeignKey(
        CompteComptable, verbose_name="compte client", on_delete=models.PROTECT,
        null=True, blank=True, related_name="+", help_text="Ex. sous-compte de 411",
    )
    compte_fournisseur = models.ForeignKey(
        CompteComptable, verbose_name="compte fournisseur", on_delete=models.PROTECT,
        null=True, blank=True, related_name="+", help_text="Ex. sous-compte de 401",
    )

    class Meta:
        verbose_name = "Compte comptable de tiers"
        verbose_name_plural = "Comptes comptables de tiers"
        ordering = ["tiers"]

    def __str__(self):
        return f"{self.tiers} — {self.compte_client or ''} {self.compte_fournisseur or ''}".strip()

    def clean(self):
        super().clean()
        if not self.compte_client_id and not self.compte_fournisseur_id:
            raise ValidationError("Renseignez au moins un compte client ou un compte fournisseur.")


class PosteGestion(models.Model):
    """Poste de gestion : classification d'achat/vente transverse, plus
    large qu'un article — couvre aussi les charges générales (assurance,
    abonnements, carburant...) qui ne sont jamais des articles stockés.
    Un même poste porte, indépendamment, un mapping achat ET un mapping
    vente (ex. "Matière première" : achetée ET revendue en négoce), chacun
    décliné par régime fiscal du tiers (Tiers.regime_fiscal) — barème
    France / France exonérée / intracommunautaire / hors UE, comme l'exige
    la TVA sur les échanges internationaux. Reproduit le référentiel
    "poste de gestion" d'un logiciel de gestion existant (import Excel
    fourni par l'utilisateur — comptabilite.postes_gestion.importer)."""

    class Groupe(models.TextChoices):
        ACHATS = "AC", "Achats"
        VENTES = "VE", "Ventes"
        OPERATIONS = "OP", "Opérations"
        SOUS_TRAITANCE = "ST", "Sous-traitance"

    code = models.CharField("code", max_length=20, primary_key=True)
    libelle = models.CharField("libellé", max_length=200)
    groupe = models.CharField(
        "groupe", max_length=5, choices=Groupe.choices, blank=True,
        help_text="Tag informatif hérité de la source — n'importe quel poste peut porter un mapping achat et/ou vente quel que soit son groupe.",
    )

    compte_achat_france = models.ForeignKey(
        CompteComptable, verbose_name="compte d'achat — France",
        on_delete=models.PROTECT, null=True, blank=True, related_name="+",
    )
    compte_achat_france_exonere = models.ForeignKey(
        CompteComptable, verbose_name="compte d'achat — France exonérée de TVA",
        on_delete=models.PROTECT, null=True, blank=True, related_name="+",
    )
    compte_achat_intra_ue = models.ForeignKey(
        CompteComptable, verbose_name="compte d'achat — intracommunautaire",
        on_delete=models.PROTECT, null=True, blank=True, related_name="+",
    )
    compte_achat_hors_ue = models.ForeignKey(
        CompteComptable, verbose_name="compte d'achat — hors UE",
        on_delete=models.PROTECT, null=True, blank=True, related_name="+",
    )

    compte_vente_france = models.ForeignKey(
        CompteComptable, verbose_name="compte de vente — France",
        on_delete=models.PROTECT, null=True, blank=True, related_name="+",
    )
    compte_vente_france_exonere = models.ForeignKey(
        CompteComptable, verbose_name="compte de vente — France exonérée de TVA",
        on_delete=models.PROTECT, null=True, blank=True, related_name="+",
    )
    compte_vente_intra_ue = models.ForeignKey(
        CompteComptable, verbose_name="compte de vente — intracommunautaire",
        on_delete=models.PROTECT, null=True, blank=True, related_name="+",
    )
    compte_vente_hors_ue = models.ForeignKey(
        CompteComptable, verbose_name="compte de vente — hors UE",
        on_delete=models.PROTECT, null=True, blank=True, related_name="+",
    )
    compte_vente_tva_majoree = models.ForeignKey(
        CompteComptable, verbose_name="compte de vente — TVA majorée",
        on_delete=models.PROTECT, null=True, blank=True, related_name="+",
        help_text="Cas particulier hérité de la source, indépendant du régime fiscal — non résolu automatiquement, à sélectionner manuellement si besoin.",
    )

    code_analytique = models.ForeignKey(
        CodeAnalytique, verbose_name="code analytique par défaut",
        on_delete=models.PROTECT, null=True, blank=True, related_name="+",
    )
    actif = models.BooleanField("actif", default=True)

    class Meta:
        verbose_name = "Poste de gestion"
        verbose_name_plural = "Postes de gestion"
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} — {self.libelle}"

    _CHAMPS_ACHAT_PAR_REGIME = {
        Tiers.RegimeFiscal.FRANCE: "compte_achat_france",
        Tiers.RegimeFiscal.FRANCE_EXONERE: "compte_achat_france_exonere",
        Tiers.RegimeFiscal.INTRA_UE: "compte_achat_intra_ue",
        Tiers.RegimeFiscal.HORS_UE: "compte_achat_hors_ue",
    }
    _CHAMPS_VENTE_PAR_REGIME = {
        Tiers.RegimeFiscal.FRANCE: "compte_vente_france",
        Tiers.RegimeFiscal.FRANCE_EXONERE: "compte_vente_france_exonere",
        Tiers.RegimeFiscal.INTRA_UE: "compte_vente_intra_ue",
        Tiers.RegimeFiscal.HORS_UE: "compte_vente_hors_ue",
    }

    def compte_achat_pour_regime(self, regime_fiscal):
        champ = self._CHAMPS_ACHAT_PAR_REGIME.get(regime_fiscal)
        return getattr(self, champ) if champ else None

    def compte_vente_pour_regime(self, regime_fiscal):
        champ = self._CHAMPS_VENTE_PAR_REGIME.get(regime_fiscal)
        return getattr(self, champ) if champ else None


class ArticleCompteVente(models.Model):
    """Compte de vente spécifique à UN article — surcharge, pour cet
    article seulement, le compte de vente par défaut des Paramètres
    comptables lors de la génération d'une écriture (voir
    comptabilite.generation). Deux façons de le renseigner : soit un
    `poste_gestion` (le compte est alors résolu dynamiquement selon le
    régime fiscal du client — prioritaire), soit un `compte_vente` fixe.
    Le code analytique associé (le sien, ou à défaut celui du poste de
    gestion) est repris sur la ligne de vente de l'écriture générée
    (jamais sur les lignes Clients/TVA)."""

    article = models.OneToOneField(
        Article, verbose_name="article", on_delete=models.CASCADE, related_name="compte_vente_override"
    )
    poste_gestion = models.ForeignKey(
        PosteGestion, verbose_name="poste de gestion", on_delete=models.PROTECT,
        null=True, blank=True, related_name="+",
        help_text="Si renseigné, prioritaire sur le compte de vente fixe ci-dessous : le compte est résolu selon le régime fiscal du client.",
    )
    compte_vente = models.ForeignKey(
        CompteComptable, verbose_name="compte de vente (fixe)", on_delete=models.PROTECT,
        null=True, blank=True, related_name="+",
        help_text="Utilisé seulement si aucun poste de gestion n'est renseigné ci-dessus.",
    )
    code_analytique = models.ForeignKey(
        CodeAnalytique,
        verbose_name="code analytique par défaut",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        help_text="Prioritaire sur le code analytique du poste de gestion, s'il y en a un.",
    )

    class Meta:
        verbose_name = "Compte de vente d'article"
        verbose_name_plural = "Comptes de vente d'article"
        ordering = ["article"]

    def __str__(self):
        return f"{self.article} → {self.poste_gestion or self.compte_vente}"

    def clean(self):
        super().clean()
        if not self.poste_gestion_id and not self.compte_vente_id:
            raise ValidationError("Renseignez soit un poste de gestion, soit un compte de vente fixe.")

    def resoudre_compte_vente(self, regime_fiscal):
        if self.poste_gestion_id:
            return self.poste_gestion.compte_vente_pour_regime(regime_fiscal)
        return self.compte_vente

    def resoudre_code_analytique(self):
        if self.code_analytique_id:
            return self.code_analytique
        return self.poste_gestion.code_analytique if self.poste_gestion_id else None


class ArticleCompteAchat(models.Model):
    """Compte d'achat (charge) spécifique à UN article acheté — même
    principe qu'ArticleCompteVente côté vente (poste de gestion résolu par
    régime fiscal du fournisseur, prioritaire sur un compte fixe).
    Purement déclaratif pour l'instant : il n'existe pas encore de
    génération automatique d'écriture d'achat (pas de document "facture
    fournisseur" dans l'app — voir achats/models.py, seulement des
    commandes/réceptions logistiques). Sert de référence pour la saisie
    manuelle des écritures d'achat, et de point d'ancrage prêt pour une
    future génération automatique le jour où un tel document existera."""

    article = models.OneToOneField(
        Article, verbose_name="article", on_delete=models.CASCADE, related_name="compte_achat_override"
    )
    poste_gestion = models.ForeignKey(
        PosteGestion, verbose_name="poste de gestion", on_delete=models.PROTECT,
        null=True, blank=True, related_name="+",
        help_text="Si renseigné, prioritaire sur le compte d'achat fixe ci-dessous : le compte est résolu selon le régime fiscal du fournisseur.",
    )
    compte_achat = models.ForeignKey(
        CompteComptable, verbose_name="compte d'achat (fixe)", on_delete=models.PROTECT,
        null=True, blank=True, related_name="+",
        help_text="Utilisé seulement si aucun poste de gestion n'est renseigné ci-dessus.",
    )
    code_analytique = models.ForeignKey(
        CodeAnalytique,
        verbose_name="code analytique par défaut",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        help_text="Prioritaire sur le code analytique du poste de gestion, s'il y en a un.",
    )

    class Meta:
        verbose_name = "Compte d'achat d'article"
        verbose_name_plural = "Comptes d'achat d'article"
        ordering = ["article"]

    def __str__(self):
        return f"{self.article} → {self.poste_gestion or self.compte_achat}"

    def clean(self):
        super().clean()
        if not self.poste_gestion_id and not self.compte_achat_id:
            raise ValidationError("Renseignez soit un poste de gestion, soit un compte d'achat fixe.")

    def resoudre_compte_achat(self, regime_fiscal):
        if self.poste_gestion_id:
            return self.poste_gestion.compte_achat_pour_regime(regime_fiscal)
        return self.compte_achat

    def resoudre_code_analytique(self):
        if self.code_analytique_id:
            return self.code_analytique
        return self.poste_gestion.code_analytique if self.poste_gestion_id else None


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
    code_analytique = models.ForeignKey(
        CodeAnalytique,
        verbose_name="code analytique",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="lignes_ecriture",
        help_text="Axe complémentaire optionnel — atelier, chantier, centre de coût...",
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
