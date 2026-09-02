from django.core.exceptions import ValidationError
from django.db import models

from commercial.models import Adresse, Contact, TauxTVA, Tiers
from technique.models import Article, PosteTravail


def _taux_tva_par_defaut():
    """Valeur par défaut du champ DevisLigne.taux_tva : le taux coché comme
    « taux par défaut » dans le référentiel, ou aucun s'il n'y en a pas."""
    defaut = TauxTVA.objects.filter(est_defaut=True).first()
    return defaut.pk if defaut else None


class Devis(models.Model):
    class Statut(models.TextChoices):
        BROUILLON = "brouillon", "Brouillon"
        VALIDE = "valide", "Validé"

    numero = models.CharField("numéro", max_length=50, primary_key=True)
    client = models.ForeignKey(Tiers, verbose_name="client", on_delete=models.PROTECT, related_name="devis")
    adresse_facturation = models.ForeignKey(
        Adresse,
        verbose_name="adresse de facturation",
        on_delete=models.PROTECT,
        related_name="devis_facturation",
        null=True,
        blank=True,
    )
    adresse_livraison = models.ForeignKey(
        Adresse,
        verbose_name="adresse de livraison",
        on_delete=models.PROTECT,
        related_name="devis_livraison",
        null=True,
        blank=True,
    )
    contact = models.ForeignKey(
        Contact,
        verbose_name="contact",
        on_delete=models.PROTECT,
        related_name="devis",
        null=True,
        blank=True,
    )
    date_creation = models.DateField("date de création")
    statut = models.CharField("statut", max_length=20, choices=Statut.choices, default=Statut.BROUILLON)
    taux_marge_globale = models.FloatField(
        "taux de marge globale", null=True, blank=True, help_text="Optionnel, écrase les marges par défaut"
    )
    delai = models.CharField(
        "délai",
        max_length=100,
        blank=True,
        help_text=(
            "Délai de livraison annoncé. Texte libre : des suggestions viennent du "
            "référentiel « Délais proposés » (commercial.DelaiPropose), mais toute "
            "valeur saisie est acceptée."
        ),
    )

    class Meta:
        verbose_name = "Devis"
        verbose_name_plural = "Devis"
        ordering = ["-date_creation", "numero"]

    def __str__(self):
        return self.numero

    def clean(self):
        super().clean()
        if self.adresse_facturation_id and self.client_id:
            if self.adresse_facturation.tiers_id != self.client_id:
                raise ValidationError(
                    {"adresse_facturation": "Cette adresse n'appartient pas au client sélectionné."}
                )
        if self.adresse_livraison_id and self.client_id:
            if self.adresse_livraison.tiers_id != self.client_id:
                raise ValidationError(
                    {"adresse_livraison": "Cette adresse n'appartient pas au client sélectionné."}
                )
        if self.contact_id and self.client_id:
            if self.contact.tiers_id != self.client_id:
                raise ValidationError({"contact": "Ce contact n'appartient pas au client sélectionné."})

    @property
    def montant_matiere_ht(self):
        return sum(ligne.prix_vente_matiere or 0 for ligne in self.lignes.all())

    montant_matiere_ht.fget.short_description = "Montant matière HT"

    @property
    def montant_operations_ht(self):
        return sum(ligne.prix_vente_operations for ligne in self.lignes.all())

    montant_operations_ht.fget.short_description = "Montant opérations HT (temps machine / main d'œuvre)"

    @property
    def montant_total_ht(self):
        return self.montant_matiere_ht + self.montant_operations_ht

    montant_total_ht.fget.short_description = "Montant total HT"

    @property
    def montant_total_ttc(self):
        """Somme des prix TTC de chaque ligne (chacune avec son propre taux de
        TVA) — reflète donc correctement un devis à taux de TVA mixtes."""
        return sum(ligne.prix_vente_ttc or 0 for ligne in self.lignes.all())

    montant_total_ttc.fget.short_description = "Montant total TTC"


class DevisLigne(models.Model):
    devis = models.ForeignKey(Devis, verbose_name="devis", on_delete=models.CASCADE, related_name="lignes")
    article = models.ForeignKey(
        Article, verbose_name="article", on_delete=models.PROTECT, related_name="devis_lignes"
    )
    quantite = models.FloatField("quantité")
    cout_matiere_calcule = models.FloatField(
        "coût matière calculé", null=True, blank=True, editable=False
    )
    taux_marge_matiere_applique = models.FloatField(
        "taux de marge matière appliqué",
        null=True,
        blank=True,
        help_text="Pré-rempli depuis l'article, éditable",
    )
    prix_vente_unitaire_force = models.FloatField(
        "prix de vente unitaire forcé (HT)",
        null=True,
        blank=True,
        help_text=(
            "Si renseigné, remplace le calcul automatique (coût matière × marge) : "
            "prix de vente matière de la ligne = quantité × ce prix unitaire."
        ),
    )
    prix_vente_matiere = models.FloatField(
        "prix de vente matière (HT)", null=True, blank=True, editable=False
    )
    taux_tva = models.ForeignKey(
        TauxTVA,
        verbose_name="taux de TVA",
        on_delete=models.PROTECT,
        related_name="devis_lignes",
        null=True,
        blank=True,
        default=_taux_tva_par_defaut,
        help_text="Pré-rempli avec le taux par défaut du référentiel, modifiable par ligne",
    )

    class Meta:
        verbose_name = "Ligne de devis"
        verbose_name_plural = "Lignes de devis"
        ordering = ["devis", "id"]

    def __str__(self):
        return f"{self.devis} — {self.article} × {self.quantite}"

    @property
    def prix_vente_operations(self):
        """Prix de vente cumulé des opérations de gamme (temps machine / main d'œuvre)."""
        return sum(op.prix_vente or 0 for op in self.operations.all())

    prix_vente_operations.fget.short_description = "Prix de vente opérations (HT)"

    @property
    def prix_vente_total(self):
        """Prix de vente matière + opérations. None tant que le chiffrage matière
        n'a pas été calculé (cohérent avec cout_matiere_calcule/prix_vente_matiere)."""
        if self.prix_vente_matiere is None:
            return None
        return self.prix_vente_matiere + self.prix_vente_operations

    prix_vente_total.fget.short_description = "Prix de vente total (matière + opérations, HT)"

    @property
    def prix_vente_unitaire(self):
        """Prix de vente total (matière + opérations, HT) ramené à une unité de
        l'article — pratique pour comparer des lignes de quantités différentes.
        None tant que le chiffrage n'a pas été calculé, ou si quantite est nulle."""
        if self.prix_vente_total is None or not self.quantite:
            return None
        return self.prix_vente_total / self.quantite

    prix_vente_unitaire.fget.short_description = "Prix de vente unitaire (HT)"

    @property
    def prix_vente_ttc(self):
        """Prix de vente total (matière + opérations) TTC, à partir du taux de
        TVA de la ligne. None tant que le chiffrage n'a pas été calculé ;
        0 % appliqué si aucun taux de TVA n'est renseigné sur la ligne."""
        if self.prix_vente_total is None:
            return None
        taux = self.taux_tva.taux if self.taux_tva_id else 0
        return self.prix_vente_total * (1 + taux / 100)

    prix_vente_ttc.fget.short_description = "Prix de vente TTC"


class DevisLigneOperation(models.Model):
    devis_ligne = models.ForeignKey(
        DevisLigne, verbose_name="ligne de devis", on_delete=models.CASCADE, related_name="operations"
    )
    poste = models.ForeignKey(
        PosteTravail, verbose_name="poste", on_delete=models.PROTECT, related_name="devis_operations"
    )
    ordre = models.PositiveIntegerField("ordre")
    cout_calcule = models.FloatField("coût calculé", null=True, blank=True, editable=False)
    taux_marge_applique = models.FloatField(
        "taux de marge appliqué", null=True, blank=True, help_text="Pré-rempli depuis le poste, éditable"
    )
    prix_vente = models.FloatField("prix de vente (HT)", null=True, blank=True, editable=False)

    class Meta:
        verbose_name = "Opération de ligne de devis"
        verbose_name_plural = "Opérations de ligne de devis"
        ordering = ["devis_ligne", "ordre"]
        constraints = [
            models.UniqueConstraint(
                fields=["devis_ligne", "ordre"], name="unique_ordre_par_ligne_devis"
            )
        ]

    def __str__(self):
        return f"{self.devis_ligne} — étape {self.ordre} ({self.poste})"


class Commande(models.Model):
    numero = models.CharField("numéro", max_length=50, primary_key=True)
    devis = models.ForeignKey(Devis, verbose_name="devis", on_delete=models.PROTECT, related_name="commandes")
    date_commande = models.DateField("date de commande")
    statut = models.CharField("statut", max_length=50, blank=True)
    adresse_facturation = models.ForeignKey(
        Adresse,
        verbose_name="adresse de facturation",
        on_delete=models.PROTECT,
        related_name="commandes_facturation",
    )
    adresse_livraison = models.ForeignKey(
        Adresse,
        verbose_name="adresse de livraison",
        on_delete=models.PROTECT,
        related_name="commandes_livraison",
    )

    class Meta:
        verbose_name = "Commande"
        verbose_name_plural = "Commandes"
        ordering = ["-date_commande", "numero"]

    def __str__(self):
        return self.numero


class OrdreFabrication(models.Model):
    class StatutSynchro(models.TextChoices):
        SYNCHRONISE = "synchronise", "Synchronisé"
        EN_ATTENTE = "en_attente", "En attente"
        ECHEC_PERSISTANT = "echec_persistant", "Échec persistant"

    numero = models.CharField("numéro", max_length=50, primary_key=True)
    commande = models.ForeignKey(
        Commande, verbose_name="commande", on_delete=models.CASCADE, related_name="ordres_fabrication"
    )
    article = models.ForeignKey(
        Article, verbose_name="article", on_delete=models.PROTECT, related_name="ordres_fabrication"
    )
    quantite = models.FloatField("quantité")
    date_lancement = models.DateField("date de lancement")
    statut = models.CharField("statut", max_length=50, blank=True, help_text="Statut de production")
    statut_synchro = models.CharField(
        "statut de synchronisation",
        max_length=20,
        choices=StatutSynchro.choices,
        default=StatutSynchro.EN_ATTENTE,
    )
    nombre_tentatives = models.PositiveIntegerField("nombre de tentatives", default=0)
    date_derniere_tentative = models.DateField("date de dernière tentative", null=True, blank=True)

    class Meta:
        verbose_name = "Ordre de fabrication"
        verbose_name_plural = "Ordres de fabrication"
        ordering = ["-date_lancement", "numero"]

    def __str__(self):
        return self.numero

    def clean(self):
        super().clean()
        if self.article_id and self.article.nature != Article.Nature.FABRIQUE:
            raise ValidationError(
                {"article": "Un ordre de fabrication ne peut porter que sur un article fabriqué."}
            )


class OperationOF(models.Model):
    ordre_fabrication = models.ForeignKey(
        OrdreFabrication,
        verbose_name="ordre de fabrication",
        on_delete=models.CASCADE,
        related_name="operations",
    )
    poste = models.ForeignKey(
        PosteTravail, verbose_name="poste", on_delete=models.PROTECT, related_name="operations_of"
    )
    ordre = models.PositiveIntegerField("ordre")
    temps_prevu = models.FloatField(
        "temps prévu", null=True, blank=True, help_text="Copié de la gamme au lancement"
    )
    temps_reel = models.FloatField(
        "temps réel", null=True, blank=True, help_text="Alimenté par le planning atelier"
    )
    quantite_bonne = models.FloatField(
        "quantité bonne", null=True, blank=True, help_text="Alimenté par le planning atelier"
    )
    quantite_rebut = models.FloatField(
        "quantité rebut", null=True, blank=True, help_text="Alimenté par le planning atelier"
    )
    statut = models.CharField("statut", max_length=50, blank=True)

    class Meta:
        verbose_name = "Opération d'ordre de fabrication"
        verbose_name_plural = "Opérations d'ordre de fabrication"
        ordering = ["ordre_fabrication", "ordre"]
        constraints = [
            models.UniqueConstraint(
                fields=["ordre_fabrication", "ordre"], name="unique_ordre_par_of"
            )
        ]

    def __str__(self):
        return f"{self.ordre_fabrication} — étape {self.ordre} ({self.poste})"
