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
