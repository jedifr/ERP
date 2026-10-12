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
    "client_id", "nom_client", "adresse_client", "code_postal_client", "ville_client", "pays_client_id",
)


class AdresseFacture:
    """Adresse imprimée sur la facture (mêmes attributs qu'une commercial.Adresse, pour les blocs PDF et Factur-X)."""

    def __init__(self, adresse, code_postal, ville, pays):
        self.adresse, self.code_postal, self.ville, self.pays = adresse, code_postal, ville, pays
        self.pays_id = pays.pk if pays is not None else None


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
        Commande, verbose_name="commande", on_delete=models.PROTECT, related_name="factures", null=True, blank=True,
        help_text="Optionnelle : une facture peut être créée directement pour un client, sans commande.",
    )
    client = models.ForeignKey(
        "commercial.Tiers", verbose_name="client", on_delete=models.PROTECT, related_name="factures", null=True, blank=True,
        help_text="Repris de la commande si laissé vide.",
    )
    # Nom et adresse imprimés sur la facture : repris de la fiche du client (adresse de facturation de la commande, sinon
    # l'adresse de facturation principale) à la création, modifiables tant que la facture n'est pas émise.
    nom_client = models.CharField(
        "nom du client", max_length=200, blank=True, help_text="Tel qu'imprimé sur la facture. Vide : raison sociale du client.",
    )
    adresse_client = models.CharField("adresse", max_length=255, blank=True)
    code_postal_client = models.CharField("code postal", max_length=20, blank=True)
    ville_client = models.CharField("ville", max_length=100, blank=True)
    pays_client = models.ForeignKey(
        "commercial.Pays", verbose_name="pays", on_delete=models.PROTECT, null=True, blank=True, related_name="+",
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
        if self.commande_id and not self.client_id:
            self.client_id = self.commande.client_id
        if not self.client_id:
            raise ValidationError({"client": "Choisissez le client (ou une commande)."})
        if self.commande_id and self.commande.client_id != self.client_id:
            raise ValidationError({"commande": "Cette commande n'est pas une commande de ce client."})
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
        if origine.commande_id != self.commande_id or origine.client_id != self.client_id:
            raise ValidationError({"facture_origine": "La facture d'origine doit porter sur la même commande et le même client."})
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

    def adresse_facturation_client(self):
        """Adresse de facturation de la fiche : celle de la commande, sinon l'adresse de facturation principale du client."""
        from commercial.models import Adresse

        if self.commande_id and self.commande.adresse_facturation_id:
            return self.commande.adresse_facturation
        if not self.client_id:
            return None
        return Adresse.objects.filter(tiers_id=self.client_id, est_facturation=True).order_by("-est_principale", "id").first()

    def _reprendre_identite_client(self):
        """Pré-remplit nom et adresse imprimés depuis la fiche du client, seulement s'ils sont vides."""
        if not self.client_id:
            return
        if not self.nom_client:
            # Relu en base sans garnir le cache de self.client (une fiche modifiée ensuite doit être relue à jour).
            from commercial.models import Tiers

            self.nom_client = Tiers.objects.filter(pk=self.client_id).values_list("raison_sociale", flat=True).first() or ""
        if not (self.adresse_client or self.code_postal_client or self.ville_client):
            adresse = self.adresse_facturation_client()
            if adresse is not None:
                self.adresse_client, self.code_postal_client = adresse.adresse, adresse.code_postal
                self.ville_client, self.pays_client_id = adresse.ville, adresse.pays_id

    @property
    def nom_client_imprime(self):
        return self.nom_client or (self.client.raison_sociale if self.client_id else "")

    @property
    def adresse_imprimee(self):
        """Adresse imprimée sur la facture (saisie sur la facture, sinon celle de la fiche) ; None si aucune."""
        if self.adresse_client or self.code_postal_client or self.ville_client:
            return AdresseFacture(self.adresse_client, self.code_postal_client, self.ville_client, self.pays_client)
        return self.adresse_facturation_client()

    def save(self, *args, **kwargs):
        if self.commande_id and not self.client_id:
            self.client_id = self.commande.client_id
        if self.pk is None or not facture_verrouillee(self):
            self._reprendre_identite_client()
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
        conditions = self.client.conditions_paiement if self.client_id else None
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
        if not self.commande_id:
            return None
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
        if not self.commande_id:
            return None
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

    def suivre_montants_des_lignes(self, suivait):
        """Après un ajout ou une modification de lignes : si les montants étaient vides ou égaux au total des lignes
        (`suivait`, mesuré avant), ils suivent le nouveau total ; un montant saisi différent (remise Tiime) n'est jamais écrasé."""
        if facture_verrouillee(self) or not self._lignes_pour_montants():
            return False
        if suivait and (self.montant_ht, self.montant_ttc) != (self.montant_ht_calcule, self.montant_ttc_calcule):
            self.montant_ht, self.montant_ttc = self.montant_ht_calcule, self.montant_ttc_calcule
            self.save()
            return True
        return self.remplir_montants_depuis_les_lignes()

    @property
    def montants_suivent_les_lignes(self):
        """Vrai si les montants sont vides ou égaux au total des lignes (à mesurer avant de toucher aux lignes)."""
        if self.montant_ht is None or self.montant_ttc is None:
            return True
        if not self._lignes_pour_montants():
            return False
        return (D(self.montant_ht), D(self.montant_ttc)) == (self.montant_ht_calcule, self.montant_ttc_calcule)


class FactureLigne(models.Model):
    """Ce qu'une facture (ou un avoir) porte réellement. Deux sortes de lignes :

    - une quantité d'une ligne de commande (préparée depuis le livré ou importée d'un bon de livraison), au prix et à la TVA
      du moment : c'est ce qui permet de savoir ce qui a été facturé (donc ce qui reste à facturer), d'empêcher de facturer
      deux fois ou plus que livré, et de passer des écritures comptables justes pour une facturation partielle ;
    - une ligne directe, sans commande : un article du référentiel ou une simple désignation libre, avec son prix.

    La quantité est toujours positive ; sur un avoir, les montants sont comptés en négatif."""

    facture = models.ForeignKey(Facture, verbose_name="facture", on_delete=models.CASCADE, related_name="lignes")
    commande_ligne = models.ForeignKey(
        "chiffrage.CommandeLigne", verbose_name="ligne de commande", on_delete=models.PROTECT, related_name="lignes_facture",
        null=True, blank=True, help_text="Vide pour une ligne directe (article ou désignation libre).",
    )
    article = models.ForeignKey(
        "technique.Article", verbose_name="article", on_delete=models.PROTECT, null=True, blank=True,
        related_name="lignes_facture", help_text="Ligne directe : article facturé (repris de la commande sinon).",
    )
    designation = models.CharField(
        "désignation", max_length=255, blank=True,
        help_text="Texte imprimé ; vide : désignation de la ligne de commande ou libellé de l'article.",
    )
    livraison = models.ForeignKey(
        "chiffrage.Livraison", verbose_name="bon de livraison", on_delete=models.PROTECT, null=True, blank=True,
        related_name="lignes_facture", help_text="Bon de livraison d'où la ligne a été importée.",
    )
    quantite = models.FloatField("quantité", validators=[MinValueValidator(0, message="Ne peut pas être négatif.")])
    prix_unitaire_ht = ChampDecimal(
        "prix unitaire HT", null=True, blank=True, validators=[MinValueValidator(0, message="Ne peut pas être négatif.")],
        help_text="Repris de la ligne de commande si laissé vide (obligatoire pour une ligne directe).", **PRIX_VENTE,
    )
    taux_tva = ChampDecimal(
        "taux de TVA (%)", null=True, blank=True,
        help_text="Repris de la ligne de commande, ou de l'article et du régime du client, si laissé vide.", **TAUX,
    )

    class Meta:
        verbose_name = "Ligne de facture"
        verbose_name_plural = "Lignes de facture"
        ordering = ["facture", "id"]

    def __str__(self):
        return f"{self.facture} — {self.libelle_court} × {self.quantite:g}"

    @property
    def article_facture(self):
        """Article de la ligne : celui de la ligne de commande, sinon celui saisi (None pour une désignation libre)."""
        if self.commande_ligne_id:
            return self.commande_ligne.article
        return self.article

    @property
    def libelle(self):
        """Désignation imprimée : celle saisie, sinon celle de la ligne de commande, sinon le libellé de l'article."""
        if self.designation:
            return self.designation
        if self.commande_ligne_id and self.commande_ligne.designation:
            return self.commande_ligne.designation
        article = self.article_facture
        return (article.libelle or "") if article is not None else ""

    @property
    def libelle_court(self):
        article = self.article_facture
        return str(article) if article is not None else (self.designation or "ligne libre")

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
        if not self.commande_ligne_id:
            return 0
        return self.commande_ligne.prix_vente_unitaire or 0

    def _taux(self):
        if self.taux_tva is not None:
            return self.taux_tva
        cl = self.commande_ligne
        if cl is None:
            return self._taux_ligne_directe()
        return cl.taux_tva.taux if cl.taux_tva_id else 0

    def _taux_ligne_directe(self):
        """TVA d'une ligne directe laissée vide : selon l'article et le régime fiscal du client (0 % hors TVA française),
        sinon le taux par défaut du référentiel."""
        from chiffrage.moteur import resoudre_taux_tva
        from commercial.models import TauxTVA, Tiers

        client = self.facture.client if self.facture_id else None
        if client is not None and self.article_id:
            try:
                return resoudre_taux_tva(self.article, client).taux
            except Exception:  # référentiel incomplet : on retombe sur le défaut ci-dessous
                pass
        if client is not None and client.regime_fiscal != Tiers.RegimeFiscal.FRANCE:
            return 0
        defaut = TauxTVA.objects.filter(est_defaut=True).first()
        return defaut.taux if defaut else 0

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
        if not self.facture_id or self.quantite is None:
            return
        if self.quantite <= 0:
            raise ValidationError({"quantite": "La quantité doit être strictement positive."})
        facture = self.facture
        if facture_verrouillee(facture):
            raise ValidationError("Cette facture est émise ou comptabilisée : ses lignes ne se modifient plus.")
        if not self.commande_ligne_id:
            self._clean_ligne_directe()
            return
        commande = self.commande_ligne.commande
        if facture.commande_id and commande.pk != facture.commande_id and not self.livraison_id:
            raise ValidationError({"commande_ligne": "Cette ligne n'appartient pas à la commande de la facture."})
        if facture.client_id and commande.client_id != facture.client_id:
            raise ValidationError({"commande_ligne": "Cette ligne n'appartient pas à une commande de ce client."})
        if self.livraison_id and self.livraison.commande_id != commande.pk:
            raise ValidationError({"livraison": "Ce bon de livraison ne porte pas sur la commande de cette ligne."})
        if self.article_id and self.article_id != self.commande_ligne.article_id:
            raise ValidationError({"article": "L'article est celui de la ligne de commande : laissez ce champ vide."})
        if facture.est_avoir:
            self._clean_avoir(facture)
        else:
            self._clean_facture()

    def _clean_ligne_directe(self):
        if self.livraison_id:
            raise ValidationError({"commande_ligne": "Une ligne importée d'un bon de livraison porte sa ligne de commande."})
        if not self.article_id and not self.designation.strip():
            raise ValidationError({"designation": "Choisissez un article, une ligne de commande, ou saisissez une désignation."})
        if self.prix_unitaire_ht is None:
            raise ValidationError({"prix_unitaire_ht": "Indiquez le prix unitaire HT de cette ligne."})

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
        if self.prix_unitaire_ht is None and self.commande_ligne_id:
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
