from django.core.exceptions import ValidationError
from django.db import models


class Pays(models.Model):
    """Référentiel de pays — sert à déduire automatiquement le régime
    fiscal d'un tiers depuis le pays de son adresse de livraison/facturation
    (voir Adresse.save()) : plus robuste qu'un champ texte libre pour
    décider si un pays est membre de l'UE (ça change dans le temps —
    Brexit — et une faute de frappe sur un nom de pays casserait la
    détection). Jeu de départ limité (UE + quelques partenaires courants),
    l'admin permet d'en ajouter librement."""

    code = models.CharField("code ISO 3166-1 alpha-2", max_length=2, primary_key=True)
    nom = models.CharField("nom", max_length=100)
    est_ue = models.BooleanField("membre de l'Union européenne", default=False)

    class Meta:
        verbose_name = "Pays"
        verbose_name_plural = "Pays"
        ordering = ["nom"]

    def __str__(self):
        return self.nom


class ConditionPaiement(models.Model):
    """Bibliothèque des conditions de paiement (ex. "30 jours fin de
    mois", "Comptant"), reprises sur Tiers.conditions_paiement. Porte un
    nombre de jours + une option "fin de mois" — pas seulement un libellé
    — pour rester exploitable plus tard dans un calcul de date
    d'échéance, contrairement à DelaiPropose qui n'est qu'une suggestion
    de texte libre."""

    libelle = models.CharField(
        "libellé", max_length=100, unique=True, help_text='Ex. "30 jours fin de mois", "Comptant"'
    )
    nombre_jours = models.PositiveIntegerField(
        "nombre de jours", null=True, blank=True, help_text="Délai avant échéance, en jours"
    )
    fin_de_mois = models.BooleanField(
        "fin de mois", default=False, help_text="Échéance reportée en fin de mois"
    )
    ordre = models.PositiveIntegerField("ordre d'affichage", default=0)

    class Meta:
        verbose_name = "Condition de paiement"
        verbose_name_plural = "Conditions de paiement"
        ordering = ["ordre", "libelle"]

    def __str__(self):
        return self.libelle


class Tiers(models.Model):
    """Entité unique pour client et/ou fournisseur (un même acteur peut être les deux)."""

    class TypeTiers(models.TextChoices):
        CLIENT = "client", "Client"
        FOURNISSEUR = "fournisseur", "Fournisseur"
        LES_DEUX = "les_deux", "Les deux"

    class RegimeFiscal(models.TextChoices):
        FRANCE = "france", "France"
        FRANCE_EXONERE = "france_exonere", "France (exonéré de TVA)"
        INTRA_UE = "intra_ue", "Intracommunautaire (UE)"
        HORS_UE = "hors_ue", "Hors Union européenne"

    code = models.CharField("code", max_length=50, primary_key=True)
    raison_sociale = models.CharField("raison sociale", max_length=200)
    type_tiers = models.CharField("type de tiers", max_length=20, choices=TypeTiers.choices)
    regime_fiscal = models.CharField(
        "régime fiscal",
        max_length=20,
        choices=RegimeFiscal.choices,
        default=RegimeFiscal.FRANCE,
        help_text=(
            "Détermine, avec le poste de gestion d'un article, quel compte "
            "d'achat/vente s'applique automatiquement (comptabilite.PosteGestion)."
        ),
    )
    siret = models.CharField(
        "SIRET", max_length=14, blank=True, help_text="Obligatoire pour la facturation électronique"
    )
    numero_tva = models.CharField(
        "numéro de TVA", max_length=20, blank=True, help_text="TVA intracommunautaire"
    )
    conditions_paiement = models.ForeignKey(
        ConditionPaiement,
        verbose_name="conditions de paiement",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="tiers",
        help_text="Valeur par défaut, reprise sur devis/commande",
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

    tiers = models.ForeignKey(
        Tiers, verbose_name="tiers", on_delete=models.CASCADE, related_name="adresses"
    )
    type_adresse = models.CharField("type d'adresse", max_length=20, choices=TypeAdresse.choices)
    libelle = models.CharField(
        "libellé", max_length=100, blank=True, help_text='Ex. "Siège", "Entrepôt Nord"'
    )
    adresse = models.CharField("adresse", max_length=255)
    code_postal = models.CharField("code postal", max_length=20)
    ville = models.CharField("ville", max_length=100)
    pays = models.ForeignKey(
        Pays,
        verbose_name="pays",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="adresses",
        help_text="Détermine automatiquement le régime fiscal du tiers si c'est l'adresse principale (livraison en priorité, sinon facturation).",
    )
    est_principale = models.BooleanField(
        "adresse principale", default=False, help_text="Adresse par défaut proposée"
    )

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

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.est_principale and self.pays_id and self.type_adresse == self.TypeAdresse.LIVRAISON:
            self._appliquer_regime_fiscal_au_tiers()
        elif (
            self.est_principale
            and self.pays_id
            and self.type_adresse == self.TypeAdresse.FACTURATION
            and not self.tiers.adresses.filter(
                type_adresse=self.TypeAdresse.LIVRAISON, est_principale=True, pays_id__isnull=False
            ).exists()
        ):
            # Repli sur l'adresse de facturation principale seulement si le
            # tiers n'a pas d'adresse de livraison principale avec un pays
            # renseigné (la livraison prime pour la territorialité de TVA).
            self._appliquer_regime_fiscal_au_tiers()

    def _appliquer_regime_fiscal_au_tiers(self):
        pays = self.pays
        if pays.code == "FR":
            regime = Tiers.RegimeFiscal.FRANCE
        elif pays.est_ue:
            regime = Tiers.RegimeFiscal.INTRA_UE
        else:
            regime = Tiers.RegimeFiscal.HORS_UE

        # Jamais d'écrasement automatique du cas "France exonérée" : ça ne
        # se déduit pas du pays, c'est une décision manuelle.
        Tiers.objects.filter(pk=self.tiers_id).exclude(
            regime_fiscal=Tiers.RegimeFiscal.FRANCE_EXONERE
        ).update(regime_fiscal=regime)


class TauxTVA(models.Model):
    """Référentiel des taux de TVA applicables (utilisé notamment par
    DevisLigne, dans l'app chiffrage)."""

    nom = models.CharField("nom", max_length=50, unique=True, help_text='Ex. "Taux normal"')
    taux = models.FloatField("taux (%)", help_text="Ex. 20 pour 20 %")
    est_defaut = models.BooleanField(
        "taux par défaut",
        default=False,
        help_text="Un seul taux peut être coché par défaut ; proposé automatiquement sur les nouvelles lignes de devis.",
    )

    class Meta:
        verbose_name = "Taux de TVA"
        verbose_name_plural = "Taux de TVA"
        ordering = ["-taux"]

    def __str__(self):
        return f"{self.nom} ({self.taux}%)"

    def clean(self):
        super().clean()
        if self.est_defaut:
            qs = TauxTVA.objects.filter(est_defaut=True)
            if self.pk is not None:
                qs = qs.exclude(pk=self.pk)
            if qs.exists():
                raise ValidationError(
                    {
                        "est_defaut": (
                            "Un taux par défaut existe déjà. Décochez-le d'abord si vous "
                            "voulez le remplacer."
                        )
                    }
                )


class DelaiPropose(models.Model):
    """Référentiel de délais suggérés sur le devis (ex. "2 semaines", "Sur
    stock") — de simples suggestions : le champ Devis.delai reste un texte
    libre, non contraint à cette liste (voir DelaiWidget, chiffrage/widgets.py)."""

    libelle = models.CharField(
        "libellé", max_length=100, unique=True, help_text='Ex. "2 semaines", "Sur stock"'
    )
    ordre = models.PositiveIntegerField(
        "ordre d'affichage", default=0, help_text="Ordre de présentation des suggestions"
    )

    class Meta:
        verbose_name = "Délai proposé"
        verbose_name_plural = "Délais proposés"
        ordering = ["ordre", "libelle"]

    def __str__(self):
        return self.libelle


class Contact(models.Model):
    tiers = models.ForeignKey(
        Tiers, verbose_name="tiers", on_delete=models.CASCADE, related_name="contacts"
    )
    nom = models.CharField("nom", max_length=100)
    prenom = models.CharField("prénom", max_length=100, blank=True)
    email = models.EmailField("email", blank=True)
    fonction = models.CharField("fonction", max_length=100, blank=True)
    est_principal = models.BooleanField(
        "contact principal", default=False, help_text="Contact par défaut proposé pour le tiers"
    )
    adresse_livraison = models.ForeignKey(
        Adresse,
        verbose_name="adresse de livraison associée",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="contacts",
        help_text=(
            "Optionnel : associe ce contact à une adresse de livraison précise du "
            "tiers (ex. le contact sur place à un site). Proposé en priorité sur "
            "cette adresse, avant le contact principal du tiers."
        ),
    )

    class Meta:
        verbose_name = "Contact"
        verbose_name_plural = "Contacts"
        ordering = ["tiers", "nom"]

    def __str__(self):
        nom_complet = f"{self.prenom} {self.nom}".strip()
        return f"{nom_complet} ({self.tiers})"

    def clean(self):
        super().clean()
        if self.est_principal and self.tiers_id:
            qs = Contact.objects.filter(tiers_id=self.tiers_id, est_principal=True)
            if self.pk is not None:
                qs = qs.exclude(pk=self.pk)
            if qs.exists():
                raise ValidationError(
                    {
                        "est_principal": (
                            "Un contact principal existe déjà pour ce tiers. Décochez-le "
                            "d'abord si vous voulez le remplacer."
                        )
                    }
                )
        if self.adresse_livraison_id and self.tiers_id:
            if self.adresse_livraison.tiers_id != self.tiers_id:
                raise ValidationError(
                    {"adresse_livraison": "Cette adresse n'appartient pas au tiers sélectionné."}
                )
            if self.adresse_livraison.type_adresse != Adresse.TypeAdresse.LIVRAISON:
                raise ValidationError(
                    {"adresse_livraison": "Seule une adresse de type « Livraison » peut être associée."}
                )


class ContactTelephone(models.Model):
    """Un contact peut avoir plusieurs numéros classés par type (portable
    ET bureau ET fax en même temps) — remplace l'ancien champ unique
    Contact.telephone, trop rigide pour ce cas courant."""

    class TypeTelephone(models.TextChoices):
        PORTABLE = "portable", "Portable"
        BUREAU = "bureau", "Bureau"
        FIXE = "fixe", "Fixe"
        FAX = "fax", "Fax"
        AUTRE = "autre", "Autre"

    contact = models.ForeignKey(
        Contact, verbose_name="contact", on_delete=models.CASCADE, related_name="telephones"
    )
    type_telephone = models.CharField("type", max_length=20, choices=TypeTelephone.choices)
    numero = models.CharField("numéro", max_length=30)

    class Meta:
        verbose_name = "Numéro de téléphone"
        verbose_name_plural = "Numéros de téléphone"
        ordering = ["contact", "type_telephone"]

    def __str__(self):
        return f"{self.get_type_telephone_display()} : {self.numero}"
