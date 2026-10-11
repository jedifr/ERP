from django.conf import settings
from django.db import models
from simple_history.models import HistoricalRecords


class ModeleDocument(models.Model):
    """Mise en page personnalisée d'un document de vente (édition visuelle GrapesJS).

    Un seul modèle par type de document. Tant qu'il n'est pas « actif », le PDF d'origine (dessiné
    par le code) est utilisé : un modèle cassé ou inachevé ne peut donc jamais empêcher d'éditer
    un devis."""

    class Type(models.TextChoices):
        DEVIS = "devis", "Devis"
        AR_COMMANDE = "ar_commande", "AR de commande"
        BON_PREPARATION = "bon_preparation", "Bon de préparation"
        BON_LIVRAISON = "bon_livraison", "Bon de livraison"
        FICHE_FABRICATION = "fiche_fabrication", "Fiche de fabrication"

    type_document = models.CharField("document", max_length=20, choices=Type.choices, unique=True)
    actif = models.BooleanField(
        "utiliser ce modèle", default=False,
        help_text="Décoché : le PDF d'origine est utilisé. Cochez après avoir vérifié l'aperçu.",
    )
    html = models.TextField("HTML", blank=True)
    css = models.TextField("CSS", blank=True)
    projet = models.TextField("projet de l'éditeur", blank=True, help_text="Pour rouvrir l'éditeur à l'identique.")
    version = models.PositiveIntegerField("version", default=1)
    modifie_le = models.DateTimeField("modifié le", auto_now=True)
    modifie_par = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="modifié par", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+", editable=False,
    )

    history = HistoricalRecords()

    class Meta:
        verbose_name = "Modèle de document"
        verbose_name_plural = "Modèles de documents"
        ordering = ["type_document"]

    def __str__(self):
        return self.get_type_document_display()

    @classmethod
    def pour(cls, type_document):
        """Le modèle de ce type, créé au besoin avec la mise en page par défaut (inactif)."""
        from .defaults import modele_par_defaut

        modele = cls.objects.filter(type_document=type_document).first()
        if modele is None:
            html, css = modele_par_defaut(type_document)
            modele = cls.objects.create(type_document=type_document, html=html, css=css)
        return modele

    @classmethod
    def actif_pour(cls, type_document):
        return cls.objects.filter(type_document=type_document, actif=True).first()
