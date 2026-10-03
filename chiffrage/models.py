import datetime
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.utils import timezone
from simple_history.models import HistoricalRecords

from commercial.models import Adresse, Contact, Devise, TauxTVA, Tiers
from stock.models import Lot, MouvementStock, StockInsuffisantError
from technique.models import Article, PosteTravail
from comptes.champs import ChampDecimal
from comptes.montants import (
    MONTANT, PRIX, PRIX_VENTE, TAUX, ZERO, D, D0, arrondir, arrondir_prix_vente, somme,
)


def _strictement_positif(valeur):
    if valeur is not None and valeur <= 0:
        raise ValidationError("Doit être strictement supérieur à 0.")


positif_ou_nul = MinValueValidator(0, message="Ne peut pas être négatif.")


def _taux_tva_par_defaut():
    """Valeur par défaut du champ DevisLigne.taux_tva : le taux coché comme
    « taux par défaut » dans le référentiel, ou aucun s'il n'y en a pas."""
    defaut = TauxTVA.objects.filter(est_defaut=True).first()
    return defaut.pk if defaut else None


def indice_pour(revision):
    """1 -> A, 2 -> B, … 26 -> Z, 27 -> AA (à la manière des colonnes d'un tableur)."""
    lettres, n = "", max(int(revision), 1)
    while n:
        n, reste = divmod(n - 1, 26)
        lettres = chr(65 + reste) + lettres
    return lettres


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

    class Issue(models.TextChoices):
        EN_ATTENTE = "en_attente", "En attente de réponse"
        ACCEPTE = "accepte", "Accepté"
        REFUSE = "refuse", "Refusé"
        REMPLACE = "remplace", "Remplacé par une révision"

    issue = models.CharField(
        "réponse du client", max_length=12, choices=Issue.choices, default=Issue.EN_ATTENTE,
        help_text="Passe à « Accepté » à la création de la commande, à « Remplacé » quand on révise le devis.",
    )
    motif_refus = models.CharField("motif du refus", max_length=200, blank=True)
    date_validite = models.DateField(
        "valable jusqu'au", null=True, blank=True,
        help_text="Fin de validité de l'offre. Posée à la validation (30 jours par défaut) ; vide = sans limite.",
    )
    devis_origine = models.ForeignKey(
        "self", verbose_name="révision de", on_delete=models.PROTECT, null=True, blank=True,
        related_name="revisions", editable=False,
    )
    revision = models.PositiveSmallIntegerField("révision", default=1, editable=False)
    motif_revision = models.CharField(
        "motif de la révision", max_length=200, blank=True, editable=False,
        help_text="Ce qui change par rapport à l'indice précédent (renseigné à la création de la révision).",
    )
    taux_marge_globale = ChampDecimal(
        "taux de marge globale",
        null=True,
        blank=True,
        validators=[positif_ou_nul],
        help_text="Optionnel, écrase les marges par défaut", **TAUX,
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

    history = HistoricalRecords()

    class Meta:
        verbose_name = "Devis"
        verbose_name_plural = "Devis"
        ordering = ["-date_creation", "numero"]
        permissions = [
            ("valider_devis", "Peut valider un devis"),
            ("valider_vente_sous_cout", "Peut valider un devis vendu sous le coût"),
        ]

    def __str__(self):
        return self.numero

    @property
    def indice(self):
        """Indice de la version : A pour l'offre initiale, B pour sa première révision, etc."""
        return indice_pour(self.revision)

    indice.fget.short_description = "Indice"

    @property
    def racine(self):
        """Premier devis de la chaîne de révisions (lui-même s'il n'est pas une révision)."""
        devis = self
        while devis.devis_origine_id:
            devis = devis.devis_origine
        return devis

    def versions(self):
        """Tous les indices de ce devis (de A au plus récent), y compris celui-ci."""
        racine = self.racine
        chaine, courant = [racine], racine
        while True:
            suivante = courant.revisions.order_by("revision").first()
            if suivante is None:
                return chaine
            chaine.append(suivante)
            courant = suivante

    @property
    def est_expire(self):
        """Offre validée, restée sans réponse, dont la date de validité est dépassée."""
        return (
            self.statut == self.Statut.VALIDE
            and self.issue == self.Issue.EN_ATTENTE
            and self.date_validite is not None
            and self.date_validite < timezone.localdate()
        )

    est_expire.fget.short_description = "Expiré"

    @property
    def jours_avant_expiration(self):
        if self.date_validite is None:
            return None
        return (self.date_validite - timezone.localdate()).days

    def save(self, *args, **kwargs):
        avant = Devis.objects.filter(pk=self.pk).values_list("statut", flat=True).first() if self.pk else None
        if self.statut == self.Statut.VALIDE and avant != self.Statut.VALIDE and self.date_validite is None:
            # À la validation, l'offre reçoit sa durée de validité (réglable) si elle n'en a pas.
            jours = getattr(settings, "DEVIS_VALIDITE_JOURS", 30)
            self.date_validite = timezone.localdate() + datetime.timedelta(days=jours)
        elif self.statut == self.Statut.BROUILLON and avant == self.Statut.VALIDE:
            # Repassé en brouillon : l'ancienne échéance ne vaut plus, une nouvelle sera posée à la re-validation.
            self.date_validite = None
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if self.issue == self.Issue.REFUSE and not self.motif_refus:
            raise ValidationError({"motif_refus": "Indiquez le motif du refus."})
        if self.issue != self.Issue.REFUSE and self.motif_refus and self.issue != self.Issue.REMPLACE:
            raise ValidationError({"motif_refus": "Un motif de refus n'a de sens que pour un devis refusé."})
        if self.issue in (self.Issue.ACCEPTE, self.Issue.REFUSE) and self.statut != self.Statut.VALIDE:
            raise ValidationError({"issue": "Seul un devis validé (envoyé au client) peut être accepté ou refusé."})
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
        return arrondir(somme(ligne.prix_vente_matiere for ligne in self.lignes.all()))

    montant_matiere_ht.fget.short_description = "Montant matière HT"

    @property
    def montant_operations_ht(self):
        return arrondir(somme(ligne.prix_vente_operations for ligne in self.lignes.all()))

    montant_operations_ht.fget.short_description = "Montant opérations HT (temps machine / main d'œuvre)"

    @property
    def montant_total_ht(self):
        return arrondir(self.montant_matiere_ht + self.montant_operations_ht)

    montant_total_ht.fget.short_description = "Montant total HT"

    @property
    def montant_total_ttc(self):
        """Somme des prix TTC de chaque ligne (chacune avec son propre taux de
        TVA et arrondie au centime) — reflète donc correctement un devis à taux
        de TVA mixtes, et correspond à ce que la facture affichera."""
        return arrondir(somme(ligne.prix_vente_ttc for ligne in self.lignes.all()))

    montant_total_ttc.fget.short_description = "Montant total TTC"


class DevisLigne(models.Model):
    devis = models.ForeignKey(Devis, verbose_name="devis", on_delete=models.CASCADE, related_name="lignes")
    article = models.ForeignKey(
        Article, verbose_name="article", on_delete=models.PROTECT, related_name="devis_lignes"
    )
    ordre = models.PositiveIntegerField(
        "ordre",
        default=0,
        help_text="Ordre d'affichage (glisser-déposer sur la fiche devis) — repris tel quel sur la commande.",
    )
    quantite = models.FloatField("quantité", validators=[_strictement_positif])
    cout_matiere_calcule = ChampDecimal(
        "coût matière calculé", null=True, blank=True, editable=False, **PRIX,
    )
    taux_marge_matiere_applique = ChampDecimal(
        "taux de marge matière appliqué",
        null=True,
        blank=True,
        validators=[positif_ou_nul],
        help_text="Pré-rempli depuis l'article, éditable", **TAUX,
    )
    prix_vente_unitaire_force = ChampDecimal(
        "prix de vente unitaire forcé (HT)",
        null=True,
        blank=True,
        validators=[positif_ou_nul],
        help_text=(
            "Si renseigné, remplace le calcul automatique (coût matière × marge) : "
            "prix de vente matière de la ligne = quantité × ce prix unitaire."
        ), **PRIX,
    )
    prix_vente_matiere = ChampDecimal(
        "prix de vente matière (HT)", null=True, blank=True, editable=False, **MONTANT,
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

    history = HistoricalRecords()

    class Meta:
        verbose_name = "Ligne de devis"
        verbose_name_plural = "Lignes de devis"
        ordering = ["devis", "ordre", "id"]

    def __str__(self):
        return f"{self.devis} — {self.article} × {self.quantite}"

    @property
    def prix_vente_operations(self):
        """Prix de vente cumulé des opérations de gamme (temps machine / main d'œuvre)."""
        return somme(op.prix_vente for op in self.operations.all())

    prix_vente_operations.fget.short_description = "Prix de vente opérations (HT)"

    @property
    def prix_vente_total(self):
        """Prix de vente matière + opérations. None tant que le chiffrage matière
        n'a pas été calculé (cohérent avec cout_matiere_calcule/prix_vente_matiere)."""
        if self.prix_vente_matiere is None:
            return None
        return D(self.prix_vente_matiere) + self.prix_vente_operations

    prix_vente_total.fget.short_description = "Prix de vente total (matière + opérations, HT)"

    @property
    def prix_vente_unitaire(self):
        """Prix de vente total (matière + opérations, HT) ramené à une unité de
        l'article — pratique pour comparer des lignes de quantités différentes.
        None tant que le chiffrage n'a pas été calculé, ou si quantite est nulle."""
        if self.prix_vente_total is None or not self.quantite:
            return None
        return arrondir_prix_vente(self.prix_vente_total / D(self.quantite))

    prix_vente_unitaire.fget.short_description = "Prix de vente unitaire (HT)"

    @property
    def prix_vente_ttc(self):
        """Prix de vente total (matière + opérations) TTC, à partir du taux de
        TVA de la ligne. None tant que le chiffrage n'a pas été calculé ;
        0 % appliqué si aucun taux de TVA n'est renseigné sur la ligne."""
        if self.prix_vente_total is None:
            return None
        taux = D0(self.taux_tva.taux) if self.taux_tva_id else ZERO
        return arrondir(self.prix_vente_total * (1 + taux / 100))

    prix_vente_ttc.fget.short_description = "Prix de vente TTC"

    @property
    def cout_total(self):
        """Coût matière + coût des opérations de gamme. None tant que le
        chiffrage matière n'a pas été calculé."""
        if self.cout_matiere_calcule is None:
            return None
        return D(self.cout_matiere_calcule) + somme(op.cout_calcule for op in self.operations.all())

    cout_total.fget.short_description = "Coût total"

    @property
    def vente_sous_le_cout(self):
        """Vrai si le prix de vente est inférieur au coût (au centime près) —
        typiquement un prix unitaire forcé trop bas."""
        if self.prix_vente_total is None or self.cout_total is None:
            return False
        return self.prix_vente_total < self.cout_total - Decimal("0.005")


class DevisLigneOperation(models.Model):
    devis_ligne = models.ForeignKey(
        DevisLigne, verbose_name="ligne de devis", on_delete=models.CASCADE, related_name="operations"
    )
    poste = models.ForeignKey(
        PosteTravail, verbose_name="poste", on_delete=models.PROTECT, related_name="devis_operations"
    )
    ordre = models.PositiveIntegerField("ordre")
    cout_calcule = ChampDecimal("coût calculé", null=True, blank=True, editable=False, **PRIX)
    taux_marge_applique = ChampDecimal(
        "taux de marge appliqué",
        null=True,
        blank=True,
        validators=[positif_ou_nul],
        help_text="Pré-rempli depuis le poste, éditable", **TAUX,
    )
    prix_vente = ChampDecimal("prix de vente (HT)", null=True, blank=True, editable=False, **MONTANT)

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


class CommandeError(Exception):
    """Opération impossible sur une commande dans son état actuel."""


class Commande(models.Model):
    class Statut(models.TextChoices):
        EN_COURS = "en_cours", "En cours"
        SOLDEE = "soldee", "Soldée"
        ANNULEE = "annulee", "Annulée"

    numero = models.CharField("numéro", max_length=50, primary_key=True)
    devis = models.ForeignKey(
        Devis,
        verbose_name="devis",
        on_delete=models.PROTECT,
        related_name="commandes",
        null=True,
        blank=True,
        help_text="Optionnel : une commande peut être créée directement, sans devis d'origine.",
    )
    client = models.ForeignKey(
        Tiers,
        verbose_name="client",
        on_delete=models.PROTECT,
        related_name="commandes",
        help_text="Pré-rempli depuis le devis s'il y en a un, modifiable ensuite.",
    )
    reference_client = models.CharField(
        "réf. commande client",
        max_length=100,
        blank=False,
        default="",
        help_text="Référence donnée par le client à sa propre commande (numéro de bon de commande, etc.).",
    )
    date_commande = models.DateField("date de commande")
    statut = models.CharField("statut", max_length=20, choices=Statut.choices, default=Statut.EN_COURS)
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
    devise = models.ForeignKey(
        Devise,
        verbose_name="devise",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="commandes",
        help_text="Par défaut celle du client si renseignée",
    )

    history = HistoricalRecords()

    class Meta:
        verbose_name = "Commande"
        verbose_name_plural = "Commandes"
        ordering = ["-date_commande", "numero"]
        permissions = [("annuler_commande", "Peut annuler une commande")]

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

    def annuler(self):
        """Annule la commande — refusé dès qu'une livraison existe (il faudrait
        alors un retour client et un avoir, pas une annulation). Les ordres de
        fabrication déjà lancés ne sont pas touchés : à arrêter côté planning."""
        if self.statut == self.Statut.ANNULEE:
            raise CommandeError(f"La commande « {self} » est déjà annulée.")
        if self.livraisons.exists():
            raise CommandeError(
                f"La commande « {self} » a déjà été livrée (au moins en partie) : "
                "elle ne peut plus être annulée."
            )
        self.statut = self.Statut.ANNULEE
        self.save(update_fields=["statut"])

    def mettre_a_jour_statut_livraison(self):
        """Passe la commande à « soldée » quand toutes ses lignes sont
        entièrement livrées (et la rouvre si ce n'est plus le cas)."""
        if self.statut == self.Statut.ANNULEE:
            return
        lignes = list(self.lignes.all())
        soldee = bool(lignes) and all(ligne.entierement_livree for ligne in lignes)
        nouveau = self.Statut.SOLDEE if soldee else self.Statut.EN_COURS
        if nouveau != self.statut:
            self.statut = nouveau
            self.save(update_fields=["statut"])


class LivraisonError(Exception):
    """Donnée de référence manquante ou incohérente empêchant la livraison."""


class CommandeLigne(models.Model):
    """Une ligne par article commandé — créée automatiquement (une par ligne
    de devis, quelle que soit sa nature) au lancement en production
    (`production.lancer_en_production`). Permet une livraison partielle,
    article par article, indépendante des ordres de fabrication.

    quantite_commandee, prix_vente_unitaire, taux_tva et designation sont
    des SURCHARGES : pré-remplies depuis devis_ligne à la création, mais
    modifiables ensuite sans jamais toucher le devis d'origine (qui garde
    la valeur réellement quotée). Chaque changement est tracé dans
    CommandeLigneModification (voir plus bas), posé par les admin (pas ici
    : il faut request.user, indisponible au niveau du modèle)."""

    commande = models.ForeignKey(
        Commande, verbose_name="commande", on_delete=models.CASCADE, related_name="lignes"
    )
    article = models.ForeignKey(
        Article, verbose_name="article", on_delete=models.PROTECT, related_name="lignes_commande"
    )
    devis_ligne = models.ForeignKey(
        DevisLigne,
        verbose_name="ligne de devis d'origine",
        on_delete=models.PROTECT,
        related_name="lignes_commande",
        null=True,
        blank=True,
        help_text=(
            "Ligne de devis servie de valeur de départ pour les surcharges "
            "ci-dessous (jamais modifiée elle-même) — vide pour une ligne "
            "ajoutée directement sur la commande, sans devis correspondant."
        ),
    )
    designation = models.CharField(
        "désignation",
        max_length=255,
        blank=True,
        help_text="Libellé propre à cette commande, remplace celui de l'article s'il est renseigné.",
    )
    quantite_commandee = models.FloatField("quantité commandée", validators=[positif_ou_nul])
    prix_vente_unitaire = ChampDecimal(
        "prix de vente unitaire (HT)",
        null=True,
        blank=True,
        validators=[positif_ou_nul],
        help_text="Pré-rempli depuis le devis à la création de la commande, modifiable ensuite.", **PRIX_VENTE,
    )
    taux_tva = models.ForeignKey(
        TauxTVA,
        verbose_name="taux de TVA",
        on_delete=models.PROTECT,
        related_name="lignes_commande",
        null=True,
        blank=True,
        help_text="Pré-rempli depuis le devis à la création de la commande, modifiable ensuite.",
    )
    quantite_livree = models.FloatField(
        "quantité livrée", default=0, editable=False, help_text="Cumul recalculé depuis les livraisons"
    )
    date_livraison_prevue = models.DateField(
        "date de livraison prévue",
        null=True,
        blank=True,
        help_text="Peut différer d'une ligne à l'autre au sein d'une même commande.",
    )

    class Meta:
        verbose_name = "Ligne de commande"
        verbose_name_plural = "Lignes de commande"
        ordering = ["commande", "id"]

    def __str__(self):
        return f"{self.commande} — {self.article} × {self.quantite_commandee}"

    def clean(self):
        super().clean()
        if self.quantite_commandee is not None and self.quantite_commandee < self.quantite_livree:
            raise ValidationError(
                {
                    "quantite_commandee": (
                        f"Ne peut pas être inférieure à la quantité déjà livrée "
                        f"({self.quantite_livree}) — annulez plutôt le reliquat en la "
                        "ramenant à cette valeur."
                    )
                }
            )
        if self.pk and self.quantite_livree > 0:
            article_en_base = CommandeLigne.objects.filter(pk=self.pk).values_list("article_id", flat=True).first()
            if article_en_base is not None and article_en_base != self.article_id:
                raise ValidationError(
                    {"article": "Impossible de changer l'article d'une ligne déjà livrée, même partiellement."}
                )

    @property
    def reliquat(self):
        """Quantité restant à livrer (commandée − déjà livrée). None tant
        que quantite_commandee n'est pas renseignée (ligne pas encore
        enregistrée — ex. la ligne vierge du formulaire d'ajout)."""
        if self.quantite_commandee is None:
            return None
        return self.quantite_commandee - self.quantite_livree

    reliquat.fget.short_description = "Reliquat"

    @property
    def quantite_facturee(self):
        """Quantité nette facturée (factures moins avoirs, brouillons compris)."""
        from facturation.models import FactureLigne

        return FactureLigne.cumul_facture(self)

    quantite_facturee.fget.short_description = "Quantité facturée"

    @property
    def reste_a_facturer(self):
        """Livré mais pas encore facturé."""
        if self.quantite_commandee is None:
            return None
        return max(round(self.quantite_livree - self.quantite_facturee, 6), 0)

    reste_a_facturer.fget.short_description = "Livré non facturé"

    @property
    def entierement_livree(self):
        return self.reliquat is not None and self.reliquat <= 0

    entierement_livree.fget.short_description = "Entièrement livrée"

    @property
    def montant_ht(self):
        """Prix de vente unitaire × quantité commandée — recalculé à partir
        des valeurs courantes de la ligne (surchargeables), pas de celles du
        devis d'origine."""
        if self.prix_vente_unitaire is None:
            return None
        return arrondir(D(self.prix_vente_unitaire) * D(self.quantite_commandee))

    montant_ht.fget.short_description = "Montant HT"

    @property
    def montant_ttc(self):
        if self.montant_ht is None:
            return None
        taux = D0(self.taux_tva.taux) if self.taux_tva_id else ZERO
        return arrondir(self.montant_ht * (1 + taux / 100))

    montant_ttc.fget.short_description = "Montant TTC"

    @property
    def date_livraison_possible(self):
        """Date de livraison réaliste selon l'approvisionnement en cours,
        calculée en direct depuis les lignes de commande fournisseur
        rattachées (achats.LigneCommandeFournisseur.commande_ligne_client) —
        jamais stockée, donc toujours à jour, contrairement à
        date_livraison_prevue (engagement client, saisi à la main, jamais
        réécrit automatiquement par l'approvisionnement). Le plus tardif des
        fournisseurs rattachés : la ligne n'est complète que quand tout est
        arrivé. None si aucun achat n'est rattaché, ou si aucun n'a de date
        de livraison prévue renseignée."""
        dates = [
            ligne.commande_fournisseur.date_livraison_prevue
            for ligne in self.approvisionnements.select_related("commande_fournisseur").all()
            if ligne.commande_fournisseur.date_livraison_prevue
        ]
        return max(dates) if dates else None

    date_livraison_possible.fget.short_description = "Date de livraison possible (appro)"

    @property
    def statut_approvisionnement(self):
        """Résumé lisible de l'avancement des achats rattachés — purement
        informatif, ne touche jamais quantite_livree (livraison au client,
        pilotée par Livraison/LivraisonLigne, indépendante de l'achat)."""
        lignes = list(self.approvisionnements.select_related("commande_fournisseur__fournisseur").all())
        if not lignes:
            return None
        commande = sum(ligne.quantite_commandee for ligne in lignes)
        recu = sum(ligne.quantite_recue for ligne in lignes)
        fournisseurs = ", ".join(
            sorted({ligne.commande_fournisseur.fournisseur.raison_sociale for ligne in lignes})
        )
        return f"{fournisseurs} — reçu {recu:g}/{commande:g}"

    statut_approvisionnement.fget.short_description = "Statut d'approvisionnement"


class CommandeLigneModification(models.Model):
    """Historique des surcharges sur une ligne de commande (quantité, prix,
    taux de TVA, désignation) — CommandeLigne ne garde que la valeur
    courante ; ceci conserve chaque ancienne valeur (pas seulement le nom
    du champ touché, contrairement au bouton "Historique" générique de
    l'admin). Peuplé par les ModelAdmin (chiffrage/admin.py), jamais par
    CommandeLigne elle-même : il faut request.user, indisponible au niveau
    du modèle."""

    commande_ligne = models.ForeignKey(
        CommandeLigne, verbose_name="ligne de commande", on_delete=models.CASCADE, related_name="modifications"
    )
    champ = models.CharField("champ modifié", max_length=50)
    ancienne_valeur = models.CharField("ancienne valeur", max_length=255, blank=True)
    nouvelle_valeur = models.CharField("nouvelle valeur", max_length=255, blank=True)
    utilisateur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="utilisateur",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="modifications_lignes_commande",
    )
    date_modification = models.DateTimeField("date de modification", auto_now_add=True)

    class Meta:
        verbose_name = "Modification de ligne de commande"
        verbose_name_plural = "Modifications de ligne de commande"
        ordering = ["-date_modification"]

    def __str__(self):
        return f"{self.commande_ligne} — {self.champ} : « {self.ancienne_valeur} » → « {self.nouvelle_valeur} »"


class Livraison(models.Model):
    class Statut(models.TextChoices):
        VALIDEE = "validee", "Validée"
        ANNULEE = "annulee", "Annulée"

    numero = models.CharField("numéro", max_length=50, primary_key=True)
    commande = models.ForeignKey(
        Commande, verbose_name="commande", on_delete=models.PROTECT, related_name="livraisons"
    )
    date_livraison = models.DateField("date de livraison", default=timezone.now)
    statut = models.CharField("statut", max_length=20, choices=Statut.choices, default=Statut.VALIDEE, editable=False)
    date_annulation = models.DateTimeField("annulée le", null=True, blank=True, editable=False)
    motif_annulation = models.CharField("motif d'annulation", max_length=200, blank=True, editable=False)
    utilisateur_annulation = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="annulée par", on_delete=models.SET_NULL, null=True, blank=True,
        editable=False, related_name="livraisons_annulees",
    )

    history = HistoricalRecords()

    class Meta:
        verbose_name = "Livraison"
        verbose_name_plural = "Livraisons"
        ordering = ["-date_livraison", "numero"]
        permissions = [("annuler_livraison", "Peut annuler une livraison")]

    def __str__(self):
        return self.numero

    def delete(self, *args, **kwargs):
        raise LivraisonError("Une livraison ne se supprime pas : annulez-la (le stock et le cumul livré sont alors rétablis).")

    def annuler(self, utilisateur=None, motif=""):
        """Annule la livraison : le cumul livré de chaque ligne, le stock (par
        contre-passation des sorties, jamais en les supprimant) et le statut de la
        commande sont rétablis. La livraison et ses lignes restent consultables.
        Refusée si elle est déjà annulée ou si la commande a déjà été facturée
        au-delà de ce qui resterait livré (il faut alors d'abord un avoir)."""
        with transaction.atomic():
            livraison = Livraison.objects.select_for_update().get(pk=self.pk)
            if livraison.statut == self.Statut.ANNULEE:
                raise LivraisonError(f"La livraison « {self} » est déjà annulée.")
            lignes = list(livraison.lignes.select_related("commande_ligne__article"))
            self._verifier_non_facturee(lignes)
            for ligne in lignes:
                CommandeLigne.objects.filter(pk=ligne.commande_ligne_id).update(
                    quantite_livree=models.F("quantite_livree") - ligne.quantite_livree
                )
            sorties = MouvementStock.objects.filter(
                reference_origine=f"LIVRAISON-{self.numero}",
                type_mouvement=MouvementStock.TypeMouvement.SORTIE,
                contre_passation__isnull=True,
            )
            for sortie in sorties:
                sortie.annuler(utilisateur=utilisateur, motif=f"Annulation de la livraison {self.numero}")
            # Via save() (pas update()) : l'historique doit enregistrer l'annulation.
            livraison.statut = self.Statut.ANNULEE
            livraison.date_annulation = timezone.now()
            livraison.motif_annulation = motif[:200]
            livraison.utilisateur_annulation = utilisateur
            livraison._change_reason = f"Annulation : {motif}"[:100]
            livraison.save(update_fields=["statut", "date_annulation", "motif_annulation", "utilisateur_annulation"])
            self.commande.refresh_from_db()
            self.commande.mettre_a_jour_statut_livraison()
        self.refresh_from_db()

    def _verifier_non_facturee(self, lignes):
        """Annuler cette livraison ne doit pas laisser plus de quantité facturée (nette
        d'avoirs) que ce qui resterait livré : sinon il faut d'abord un avoir."""
        from facturation.models import FactureLigne

        for ligne in lignes:
            facture = FactureLigne.cumul_facture(ligne.commande_ligne)
            restant_livre = ligne.commande_ligne.quantite_livree - ligne.quantite_livree
            if facture > restant_livre + 1e-9:
                raise LivraisonError(
                    f"« {ligne.commande_ligne.article} » est déjà facturé à hauteur de {facture:g} : "
                    "émettez d'abord un avoir pour annuler cette livraison."
                )
        # Facture antérieure aux lignes de facture : on ne sait pas ce qu'elle porte.
        if self.commande.factures.filter(lignes__isnull=True, type_document="facture").exists():
            raise LivraisonError(
                f"La commande « {self.commande} » porte une facture sans lignes : "
                "émettez d'abord un avoir pour annuler cette livraison."
            )


class LivraisonLigne(models.Model):
    """Une livraison peut porter sur une partie seulement de la quantité
    commandée d'un article (livraison partielle) ; le cumul sur
    CommandeLigne.quantite_livree matérialise le reliquat éventuel — même
    principe que achats.ReceptionLigne côté réception fournisseur."""

    livraison = models.ForeignKey(
        Livraison, verbose_name="livraison", on_delete=models.CASCADE, related_name="lignes"
    )
    commande_ligne = models.ForeignKey(
        CommandeLigne,
        verbose_name="ligne de commande",
        on_delete=models.PROTECT,
        related_name="livraisons_lignes",
    )
    quantite_livree = models.FloatField("quantité livrée")
    lot = models.ForeignKey(
        Lot, verbose_name="lot livré", on_delete=models.PROTECT, null=True, blank=True,
        related_name="livraisons_lignes",
        help_text="Pour livrer une coulée précise (certificat 3.1). Vide : lots consommés du plus ancien au plus récent.",
    )

    class Meta:
        verbose_name = "Ligne de livraison"
        verbose_name_plural = "Lignes de livraison"
        ordering = ["livraison", "id"]

    def __str__(self):
        return f"{self.livraison} — {self.commande_ligne.article} × {self.quantite_livree}"

    def clean(self):
        super().clean()
        if self.quantite_livree is not None and self.quantite_livree <= 0:
            raise ValidationError({"quantite_livree": "La quantité livrée doit être positive."})
        if self.pk is None and self.livraison_id and self.livraison.statut == Livraison.Statut.ANNULEE:
            raise ValidationError("Cette livraison est annulée : on ne peut plus y ajouter de ligne.")
        if self.pk is None and self.commande_ligne_id:
            if self.commande_ligne.commande.statut == Commande.Statut.ANNULEE:
                raise ValidationError("Cette commande est annulée : aucune livraison possible.")
            deja_livre = self.commande_ligne.quantite_livree
            commandee = self.commande_ligne.quantite_commandee
            if deja_livre + (self.quantite_livree or 0) > commandee:
                raise ValidationError(
                    {
                        "quantite_livree": (
                            f"Dépasse la quantité commandée ({commandee}, déjà livré {deja_livre})."
                        )
                    }
                )
        if self.lot_id and self.commande_ligne_id and self.lot.article_id != self.commande_ligne.article_id:
            raise ValidationError({"lot": f"Ce lot n'est pas un lot de l'article « {self.commande_ligne.article} »."})

    def delete(self, *args, **kwargs):
        raise LivraisonError("Une ligne de livraison ne se supprime pas : annulez la livraison.")

    def save(self, *args, **kwargs):
        creation = self.pk is None
        if not creation:
            raise LivraisonError("Une ligne de livraison ne se modifie pas : annulez la livraison et saisissez-en une autre.")
        # Tout ou rien : si le lot est ambigu (LivraisonError), ni cette
        # ligne ni la mise à jour du cumul livré ne doivent être enregistrées
        # — sans quoi on se retrouve avec une ligne "fantôme" enregistrée
        # dont la quantité livrée n'a jamais été répercutée nulle part.
        with transaction.atomic():
            super().save(*args, **kwargs)
            self._appliquer()

    def _appliquer(self):
        ligne = self.commande_ligne
        # Contrairement aux matières premières achetées, un article fabriqué
        # sur mesure n'a le plus souvent aucun lot de stock (gere_en_stock
        # est faux par défaut pour un FABRIQUE) : dans ce cas la sortie de
        # stock est simplement sautée plutôt que de bloquer la livraison.
        # Plusieurs lots : consommés du plus ancien au plus récent (FIFO).
        lots = self._lots_a_consommer(ligne.article) if settings.STOCK_ACTIF else []

        CommandeLigne.objects.filter(pk=ligne.pk).update(
            quantite_livree=models.F("quantite_livree") + self.quantite_livree
        )
        ligne.refresh_from_db(fields=["quantite_livree"])
        ligne.commande.mettre_a_jour_statut_livraison()

        if not lots:
            return
        reste = self.quantite_livree
        try:
            for lot in lots:
                prelevement = min(reste, lot.quantite)
                if prelevement <= 0:
                    continue
                MouvementStock.objects.create(
                    lot=lot,
                    type_mouvement=MouvementStock.TypeMouvement.SORTIE,
                    quantite=prelevement,
                    date_mouvement=self.livraison.date_livraison,
                    reference_origine=f"LIVRAISON-{self.livraison.numero}",
                )
                reste = round(reste - prelevement, 6)
                if reste <= 0:
                    break
            if reste > 0:
                raise StockInsuffisantError(
                    f"Stock insuffisant pour « {ligne.article} » : il manque {reste:g} "
                    f"sur {self.quantite_livree:g} à sortir."
                )
        except StockInsuffisantError as exc:
            # Annule toute la ligne (save() est atomique) : on ne livre pas ce
            # que le stock ne contient pas.
            raise LivraisonError(
                f"{exc} Régularisez le stock (entrée ou inventaire) avant de saisir cette livraison."
            ) from exc

    def _lots_a_consommer(self, article):
        if self.lot_id:
            return list(Lot.objects.select_for_update().filter(pk=self.lot_id))
        return self._lots_fifo(article)

    @staticmethod
    def _lots_fifo(article):
        return list(Lot.objects.select_for_update().filter(article=article, quantite__gt=0).order_by("id")) or list(
            Lot.objects.filter(article=article).order_by("id")[:1]
        )


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
    derniere_erreur = models.CharField("dernière erreur de synchronisation", max_length=500, blank=True)
    prochaine_tentative = models.DateTimeField(
        "prochaine reprise automatique",
        null=True,
        blank=True,
        help_text="Délai croissant entre deux tentatives ; vide = à reprendre dès la prochaine exécution.",
    )
    empreinte_envoyee = models.CharField(
        "empreinte du dernier envoi réussi", max_length=64, blank=True, editable=False
    )

    class Meta:
        verbose_name = "Ordre de fabrication"
        verbose_name_plural = "Ordres de fabrication"
        ordering = ["-date_lancement", "numero"]
        permissions = [("voir_marges", "Peut consulter les marges réelles et taux de charge (pilotage)")]

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


@receiver(post_delete, sender=Devis)
def _restaurer_indice_precedent(sender, instance, **kwargs):
    """Une révision brouillon abandonnée (supprimée) rend son indice précédent à nouveau valable :
    sans cela il resterait « remplacé » par un devis qui n'existe plus."""
    if not instance.devis_origine_id:
        return
    precedent = Devis.objects.filter(pk=instance.devis_origine_id).first()
    if precedent and precedent.issue == Devis.Issue.REMPLACE and not precedent.revisions.exists():
        precedent.issue = Devis.Issue.EN_ATTENTE
        precedent._change_reason = f"Révision {instance.numero} supprimée"[:100]
        precedent.save(update_fields=["issue"])
