from django.db import models


class EvenementConnexion(models.Model):
    """Journal des connexions : succès, échecs, refus pour cause de verrouillage et
    déblocages manuels. Sert à la fois de piste d'audit et de compteur pour limiter les
    tentatives (voir comptes.connexions). Jamais modifiable ni supprimable à la main."""

    class Type(models.TextChoices):
        SUCCES = "succes", "Connexion réussie"
        ECHEC = "echec", "Échec d'authentification"
        BLOQUE = "bloque", "Refusée : compte verrouillé"
        DEBLOCAGE = "deblocage", "Déblocage manuel"

    identifiant = models.CharField("identifiant saisi", max_length=150, db_index=True)
    type_evenement = models.CharField("événement", max_length=12, choices=Type.choices)
    adresse_ip = models.GenericIPAddressField(
        "adresse IP", null=True, blank=True,
        help_text="Indicative : derrière un reverse proxy, c'est l'adresse transmise par celui-ci.",
    )
    date = models.DateTimeField("date", auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "Événement de connexion"
        verbose_name_plural = "Journal des connexions"
        ordering = ["-date", "-id"]
        indexes = [models.Index(fields=["identifiant", "date"])]
        permissions = [("debloquer_compte", "Peut débloquer un compte verrouillé après trop d'échecs")]

    def __str__(self):
        return f"{self.get_type_evenement_display()} — {self.identifiant}"


class Societe(models.Model):
    """Identité de l'entreprise, reprise en en-tête et pied de page des documents PDF (devis,
    bons de livraison). Une seule fiche (voir `charger`)."""

    raison_sociale = models.CharField("raison sociale", max_length=200, default="Mon entreprise")
    forme_juridique = models.CharField("forme juridique", max_length=50, blank=True, help_text="Ex. SAS, SARL, EURL")
    adresse = models.TextField("adresse", blank=True)
    code_postal = models.CharField("code postal", max_length=10, blank=True)
    ville = models.CharField("ville", max_length=100, blank=True)
    telephone = models.CharField("téléphone", max_length=30, blank=True)
    email = models.EmailField("e-mail", blank=True)
    site_web = models.CharField("site web", max_length=100, blank=True)
    siret = models.CharField("SIRET", max_length=20, blank=True)
    tva_intracommunautaire = models.CharField("TVA intracommunautaire", max_length=20, blank=True)
    capital = models.CharField("capital social", max_length=50, blank=True, help_text="Ex. 10 000 €")
    rcs = models.CharField("RCS", max_length=100, blank=True, help_text="Ex. RCS Lyon 123 456 789")
    iban = models.CharField("IBAN", max_length=40, blank=True)
    bic = models.CharField("BIC", max_length=15, blank=True)
    logo = models.ImageField("logo", upload_to="societe/", blank=True, help_text="Affiché en en-tête des documents (PNG ou JPEG).")
    mentions_devis = models.TextField(
        "mentions sur les devis", blank=True,
        help_text="Conditions de vente, pénalités de retard, etc., imprimées en bas du devis.",
    )
    mentions_livraison = models.TextField("mentions sur les bons de livraison", blank=True)

    class Meta:
        verbose_name = "Société"
        verbose_name_plural = "Société"

    def __str__(self):
        return self.raison_sociale

    @classmethod
    def charger(cls):
        societe, _ = cls.objects.get_or_create(pk=1)
        return societe

    def save(self, *args, **kwargs):
        self.pk = 1  # une seule fiche
        super().save(*args, **kwargs)
