from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone
from simple_history.models import HistoricalRecords

from chiffrage.models import Commande
from comptes.champs import ChampDecimal
from comptes.montants import MONTANT, PRIX_VENTE, TAUX, ZERO, D, D0, arrondir, pourcent, somme


class FactureVerrouilleeError(Exception):
    """Une facture émise ou comptabilisée ne se modifie plus (hors paiement) et ne se supprime pas."""


# Champs qui ne bougent plus dès que la facture est émise (référence Tiime
# renseignée) ou comptabilisée : seul le suivi du paiement reste libre. Une
# erreur se corrige par un avoir, jamais en réécrivant la facture.
CHAMPS_FIGES = (
    "commande_id", "montant_ht", "montant_ttc", "date_facturation", "reference_tiime", "mode_creation",
    "type_document", "facture_origine_id", "anticipee", "motif",
)


def facture_verrouillee(facture):
    """Lu en base (jamais sur l'instance, qu'un formulaire ou un serializer a
    déjà pu modifier) : facture émise dans Tiime ou déjà passée en comptabilité."""
    if facture is None or facture.pk is None:
        return False
    from comptabilite.models import EcritureComptable

    if Facture.objects.filter(pk=facture.pk).exclude(reference_tiime="").exists():
        return True
    return EcritureComptable.objects.filter(facture_id=facture.pk).exists()


class Facture(models.Model):
    """La facture légale vit dans Tiime (plateforme agréée, conforme à la
    réforme de facturation électronique). Ce modèle reste une trace côté ERP.

    Flux retenu pour démarrer : facturation créée manuellement dans Tiime,
    référence renseignée ensuite ici.
    """

    class ModeCreation(models.TextChoices):
        MANUEL = "manuel", "Manuel"
        AUTOMATIQUE = "automatique", "Automatique"

    numero = models.CharField("numéro", max_length=50, primary_key=True)
    commande = models.ForeignKey(
        Commande, verbose_name="commande", on_delete=models.PROTECT, related_name="factures"
    )
    reference_tiime = models.CharField("référence Tiime", max_length=100, blank=True)
    class TypeDocument(models.TextChoices):
        FACTURE = "facture", "Facture"
        AVOIR = "avoir", "Avoir"

    type_document = models.CharField(
        "type de document", max_length=10, choices=TypeDocument.choices, default=TypeDocument.FACTURE
    )
    facture_origine = models.ForeignKey(
        "self",
        verbose_name="facture d'origine",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="avoirs",
        help_text="Pour un avoir : la facture qu'il corrige (obligatoire).",
    )
    motif = models.CharField("motif", max_length=200, blank=True, help_text="Obligatoire pour un avoir.")
    anticipee = models.BooleanField(
        "facturation anticipée",
        default=False,
        help_text="Cochez pour facturer avant livraison (acompte, facturation à la commande) : "
        "lève la limite « pas plus que le livré ».",
    )
    montant_ht = ChampDecimal(
        "montant HT", null=True, blank=True, help_text="Négatif pour un avoir.", **MONTANT,
    )
    montant_ttc = ChampDecimal(
        "montant TTC", null=True, blank=True, help_text="Négatif pour un avoir.", **MONTANT,
    )
    date_facturation = models.DateField("date de facturation")

    class StatutPaiement(models.TextChoices):
        A_PAYER = "a_payer", "À payer"
        PARTIEL = "partiel", "Partiellement payée"
        PAYE = "paye", "Payée"

    statut_paiement = models.CharField(
        "statut de paiement", max_length=20, choices=StatutPaiement.choices, default=StatutPaiement.A_PAYER
    )
    date_paiement = models.DateField(
        "date de paiement", null=True, blank=True, help_text="Date du dernier règlement ; posée automatiquement au passage à « Payée »."
    )
    mode_creation = models.CharField(
        "mode de création", max_length=20, choices=ModeCreation.choices, default=ModeCreation.MANUEL
    )

    history = HistoricalRecords()

    class Meta:
        verbose_name = "Facture"
        verbose_name_plural = "Factures"
        ordering = ["-date_facturation", "numero"]
        permissions = [
            ("relancer_facture", "Peut envoyer une relance de paiement"),
            ("creer_avoir", "Peut créer un avoir"),
            ("facturer_avant_livraison", "Peut faire une facturation anticipée (avant livraison)"),
        ]

    def __str__(self):
        return self.numero

    def clean(self):
        super().clean()
        if self.commande_id and self.date_facturation:
            if self.date_facturation < self.commande.date_commande:
                raise ValidationError(
                    {"date_facturation": "La facture ne peut pas être antérieure à la commande "
                     f"({self.commande.date_commande:%d/%m/%Y})."}
                )
        if self.date_facturation and self.date_facturation > timezone.localdate():
            raise ValidationError({"date_facturation": "La date de facturation ne peut pas être dans le futur."})
        avoir = self.type_document == self.TypeDocument.AVOIR
        signe = -1 if avoir else 1
        for champ in ("montant_ht", "montant_ttc"):
            valeur = getattr(self, champ)
            if valeur is not None and valeur * signe < 0:
                raise ValidationError(
                    {champ: "Le montant d'un avoir est négatif." if avoir else "Ne peut pas être négatif."}
                )
        if self.montant_ht is not None and self.montant_ttc is not None:
            if abs(D(self.montant_ttc)) < abs(D(self.montant_ht)):
                raise ValidationError({"montant_ttc": "Le montant TTC ne peut pas être inférieur au montant HT."})
        self._clean_avoir(avoir)
        if self.date_paiement and self.date_facturation and self.date_paiement < self.date_facturation:
            raise ValidationError({"date_paiement": "Le paiement ne peut pas précéder la facture."})
        if self.pk is not None and facture_verrouillee(self):
            en_base = Facture.objects.filter(pk=self.pk).values(*CHAMPS_FIGES).first()
            for champ in CHAMPS_FIGES:
                if en_base and getattr(self, champ) != en_base[champ]:
                    nom = champ[:-3] if champ.endswith("_id") else champ
                    raise ValidationError(
                        {nom: "Cette facture est émise ou comptabilisée : ce champ ne se modifie plus "
                              "(corrigez par un avoir)."}
                    )

    def _clean_avoir(self, avoir):
        if not avoir:
            if self.facture_origine_id:
                raise ValidationError({"facture_origine": "Seul un avoir a une facture d'origine."})
            return
        if not self.facture_origine_id:
            raise ValidationError({"facture_origine": "Un avoir doit référencer la facture qu'il corrige."})
        if not self.motif:
            raise ValidationError({"motif": "Indiquez le motif de l'avoir."})
        origine = self.facture_origine
        if origine.type_document != self.TypeDocument.FACTURE:
            raise ValidationError({"facture_origine": "On ne fait pas d'avoir sur un avoir."})
        if origine.commande_id != self.commande_id:
            raise ValidationError({"facture_origine": "La facture d'origine doit porter sur la même commande."})
        if self.date_facturation and self.date_facturation < origine.date_facturation:
            raise ValidationError({"date_facturation": "Un avoir ne peut pas précéder sa facture d'origine."})
        if self.montant_ht is not None and origine.montant_ht is not None:
            deja_credite = -(
                origine.avoirs.exclude(pk=self.pk).aggregate(total=models.Sum("montant_ht"))["total"] or 0
            )
            if -D(self.montant_ht) + D(deja_credite) > D(origine.montant_ht):
                raise ValidationError(
                    {"montant_ht": f"Dépasse le montant de la facture d'origine ({pourcent(origine.montant_ht)} € HT, "
                                   f"{pourcent(deja_credite)} € déjà crédités)."}
                )

    @property
    def est_avoir(self):
        return self.type_document == self.TypeDocument.AVOIR

    @property
    def est_en_retard(self):
        """Facture (jamais un avoir) non soldée dont l'échéance calculée est dépassée.
        Faux si l'échéance est inconnue (client sans délai de paiement chiffré)."""
        if self.est_avoir or self.statut_paiement == self.StatutPaiement.PAYE:
            return False
        echeance = self.date_echeance
        return echeance is not None and echeance < timezone.localdate()

    @property
    def ecart_avec_les_lignes(self):
        """Montant HT saisi (Tiime) moins le total des lignes : signale une remise ou
        une erreur de saisie. None si pas de lignes ou pas de montant saisi."""
        if self.montant_ht is None or not self.lignes.exists():
            return None
        return arrondir(D(self.montant_ht) - self.montant_ht_calcule)

    def save(self, *args, **kwargs):
        if self.montant_ht is not None:
            self.montant_ht = arrondir(self.montant_ht)
        if self.montant_ttc is not None:
            self.montant_ttc = arrondir(self.montant_ttc)
        if self.statut_paiement == self.StatutPaiement.PAYE and self.date_paiement is None:
            self.date_paiement = timezone.localdate()
        if self.pk is not None and facture_verrouillee(self):
            en_base = Facture.objects.filter(pk=self.pk).values(*CHAMPS_FIGES).first()
            if en_base and any(getattr(self, champ) != en_base[champ] for champ in CHAMPS_FIGES):
                raise FactureVerrouilleeError(
                    "Cette facture est émise ou comptabilisée : seul son paiement se modifie (corrigez par un avoir)."
                )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if facture_verrouillee(self):
            raise FactureVerrouilleeError(
                "Une facture émise ou comptabilisée ne se supprime pas (continuité de la numérotation) : émettez un avoir."
            )
        return super().delete(*args, **kwargs)

    @property
    def date_echeance(self):
        """Date d'échéance calculée à partir des conditions de paiement du
        client (délai en jours, éventuellement reporté en fin de mois).
        None si le client n'a pas de conditions de paiement renseignées, ou
        si celles-ci sont purement descriptives (pas de délai chiffré)."""
        conditions = self.commande.client.conditions_paiement
        if conditions is None:
            return None
        return conditions.calculer_echeance(self.date_facturation)

    def _lignes_pour_montants(self):
        return list(self.lignes.select_related("commande_ligne")) if self.pk else []

    @property
    def montant_ht_calcule(self):
        """Total HT indicatif : somme des lignes de la facture si elle en a (signée
        pour un avoir), sinon des lignes actuelles de la commande
        (CommandeLigne.montant_ht) — n'est jamais écrit dans montant_ht, qui reste
        la valeur de la facture réelle émise dans Tiime (qui fait foi : remise,
        arrondi...). None si rien n'est chiffré."""
        lignes = self._lignes_pour_montants()
        if lignes:
            return arrondir(somme(ligne.montant_ht for ligne in lignes))
        montants = [l.montant_ht for l in self.commande.lignes.all() if l.montant_ht is not None]
        return arrondir(somme(montants)) if montants else None

    @property
    def montant_ttc_calcule(self):
        """Total TTC indicatif. La TVA se calcule **par taux sur la base HT totale** puis s'arrondit (règle de la norme EN 16931, celle du PDF
        et du fichier Factur-X) — pas ligne à ligne : sinon un écart d'un centime apparaît entre les lignes et le total. Signé pour un avoir."""
        lignes = self._lignes_pour_montants()
        if lignes:
            bases = {}
            for ligne in lignes:
                taux = D0(ligne._taux())
                bases[taux] = bases.get(taux, ZERO) + abs(ligne.montant_ht)
            total_ht = arrondir(sum((arrondir(base) for base in bases.values()), ZERO))
            total_tva = arrondir(sum((arrondir(base * taux / 100) for taux, base in bases.items()), ZERO))
            ttc = arrondir(total_ht + total_tva)
            return -ttc if self.est_avoir else ttc
        montants = [l.montant_ttc for l in self.commande.lignes.all() if l.montant_ttc is not None]
        return arrondir(somme(montants)) if montants else None

    def remplir_montants_depuis_les_lignes(self):
        """Renseigne montant_ht/montant_ttc depuis les lignes quand ils sont encore
        vides (jamais au détriment d'un montant saisi) et que la facture n'est pas
        verrouillée."""
        if facture_verrouillee(self) or not self._lignes_pour_montants():
            return False
        modifie = False
        if self.montant_ht is None:
            self.montant_ht, modifie = self.montant_ht_calcule, True
        if self.montant_ttc is None:
            self.montant_ttc, modifie = self.montant_ttc_calcule, True
        if modifie:
            self.save()
        return modifie


class FactureLigne(models.Model):
    """Ce qu'une facture (ou un avoir) porte réellement : une quantité d'une ligne
    de commande, au prix et à la TVA du moment. C'est ce qui permet de savoir ce
    qui a été facturé (donc ce qui reste à facturer), d'empêcher de facturer deux
    fois ou plus que livré, et de passer des écritures comptables justes pour une
    facturation partielle. La quantité est toujours positive ; sur un avoir, les
    montants sont comptés en négatif."""

    facture = models.ForeignKey(Facture, verbose_name="facture", on_delete=models.CASCADE, related_name="lignes")
    commande_ligne = models.ForeignKey(
        "chiffrage.CommandeLigne", verbose_name="ligne de commande", on_delete=models.PROTECT, related_name="lignes_facture"
    )
    quantite = models.FloatField("quantité", validators=[MinValueValidator(0, message="Ne peut pas être négatif.")])
    prix_unitaire_ht = ChampDecimal(
        "prix unitaire HT", null=True, blank=True, validators=[MinValueValidator(0, message="Ne peut pas être négatif.")],
        help_text="Repris de la ligne de commande si laissé vide.", **PRIX_VENTE,
    )
    taux_tva = ChampDecimal("taux de TVA (%)", null=True, blank=True, help_text="Repris de la ligne de commande si laissé vide.", **TAUX)

    class Meta:
        verbose_name = "Ligne de facture"
        verbose_name_plural = "Lignes de facture"
        ordering = ["facture", "id"]

    def __str__(self):
        return f"{self.facture} — {self.commande_ligne.article} × {self.quantite:g}"

    @staticmethod
    def cumul_facture(commande_ligne, exclure_pk=None):
        """Quantité nette facturée d'une ligne de commande : factures moins avoirs,
        brouillons compris (une facture pas encore émise réserve déjà sa quantité)."""
        lignes = FactureLigne.objects.filter(commande_ligne=commande_ligne)
        if exclure_pk:
            lignes = lignes.exclude(pk=exclure_pk)
        total = 0.0
        for ligne in lignes.select_related("facture"):
            total += -ligne.quantite if ligne.facture.est_avoir else ligne.quantite
        return round(total, 6)

    def _prix(self):
        if self.prix_unitaire_ht is not None:
            return self.prix_unitaire_ht
        return self.commande_ligne.prix_vente_unitaire or 0

    def _taux(self):
        if self.taux_tva is not None:
            return self.taux_tva
        cl = self.commande_ligne
        return cl.taux_tva.taux if cl.taux_tva_id else 0

    @property
    def montant_ht(self):
        brut = arrondir(D(self._prix()) * D(self.quantite))
        return -brut if self.facture.est_avoir else brut

    @property
    def montant_ttc(self):
        valeur = arrondir(abs(self.montant_ht) * (1 + D0(self._taux()) / 100))
        return -valeur if self.facture.est_avoir else valeur

    def clean(self):
        super().clean()
        if not self.facture_id or not self.commande_ligne_id or self.quantite is None:
            return
        if self.quantite <= 0:
            raise ValidationError({"quantite": "La quantité doit être strictement positive."})
        facture = self.facture
        if self.commande_ligne.commande_id != facture.commande_id:
            raise ValidationError({"commande_ligne": "Cette ligne n'appartient pas à la commande de la facture."})
        if facture_verrouillee(facture):
            raise ValidationError("Cette facture est émise ou comptabilisée : ses lignes ne se modifient plus.")
        if facture.est_avoir:
            self._clean_avoir(facture)
        else:
            self._clean_facture()

    def _clean_facture(self):
        cl = self.commande_ligne
        deja = self.cumul_facture(cl, exclure_pk=self.pk)
        limite = cl.quantite_commandee if self.facture.anticipee else min(cl.quantite_livree, cl.quantite_commandee)
        if deja + self.quantite > limite + 1e-9:
            reste = max(limite - deja, 0)
            raison = "commandé" if self.facture.anticipee else "livré"
            raise ValidationError(
                {"quantite": f"Dépasse ce qui peut être facturé : {cl.quantite_commandee:g} commandé, "
                             f"{cl.quantite_livree:g} livré, {deja:g} déjà facturé — il reste {reste:g} à facturer "
                             f"(plafond : le {raison})."}
            )

    def _clean_avoir(self, avoir):
        origine = avoir.facture_origine
        if origine is None:
            return
        facture_sur_ligne = (
            origine.lignes.filter(commande_ligne=self.commande_ligne).aggregate(total=models.Sum("quantite"))["total"] or 0
        )
        deja_credite = sum(
            ligne.quantite
            for ligne in FactureLigne.objects.filter(facture__facture_origine=origine, commande_ligne=self.commande_ligne)
            .exclude(pk=self.pk)
        )
        if self.quantite + deja_credite > facture_sur_ligne + 1e-9:
            raise ValidationError(
                {"quantite": f"Dépasse ce que la facture d'origine porte sur cette ligne "
                             f"({facture_sur_ligne:g} facturé, {deja_credite:g} déjà crédité)."}
            )

    def save(self, *args, **kwargs):
        if self.pk is not None and facture_verrouillee(self.facture):
            raise FactureVerrouilleeError("Facture émise ou comptabilisée : ses lignes ne se modifient plus.")
        if self.prix_unitaire_ht is None:
            self.prix_unitaire_ht = self.commande_ligne.prix_vente_unitaire
        if self.taux_tva is None:
            self.taux_tva = self._taux()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if facture_verrouillee(self.facture):
            raise FactureVerrouilleeError("Facture émise ou comptabilisée : ses lignes ne se suppriment plus.")
        return super().delete(*args, **kwargs)


class RelanceFacture(models.Model):
    """Trace d'une relance de paiement envoyée (ou tentée) pour une facture : niveau, destinataire,
    texte exact. Consultation seule."""

    facture = models.ForeignKey(Facture, verbose_name="facture", on_delete=models.CASCADE, related_name="relances")
    niveau = models.PositiveSmallIntegerField("niveau", help_text="1 rappel, 2 relance, 3 dernière relance")
    destinataire = models.EmailField("destinataire")
    objet = models.CharField("objet", max_length=200)
    corps = models.TextField("message")
    envoyee = models.BooleanField("envoyée", default=True)
    erreur = models.CharField("erreur d'envoi", max_length=255, blank=True)
    date_envoi = models.DateTimeField("date", auto_now_add=True)
    utilisateur = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="envoyée par", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="relances_envoyees",
    )

    class Meta:
        verbose_name = "Relance de paiement"
        verbose_name_plural = "Relances de paiement"
        ordering = ["-date_envoi", "-id"]

    def __str__(self):
        return f"Relance {self.niveau} — {self.facture_id}"
