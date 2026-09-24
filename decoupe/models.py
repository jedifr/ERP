from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from technique.models import Article


class PieceDecoupe(models.Model):
    """Silhouette d'une pièce à découper (laser / jet d'eau), importée depuis un DXF ou un DWG."""

    class FormatSource(models.TextChoices):
        DXF = "dxf", "DXF"
        DWG = "dwg", "DWG"

    class Statut(models.TextChoices):
        EN_ATTENTE = "en_attente", "En attente d'import"
        OK = "ok", "Importée"
        ERREUR = "erreur", "Erreur d'import"

    nom = models.CharField(max_length=200)
    article = models.ForeignKey(
        Article,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pieces_decoupe",
        help_text="Article fabriqué correspondant, si déjà référencé au socle technique",
    )
    fichier_source = models.FileField(upload_to="decoupe/sources/%Y/%m/")
    format_source = models.CharField(max_length=10, choices=FormatSource.choices, blank=True)
    rotation_autorisee = models.BooleanField(
        default=True,
        help_text="À décocher si le sens de la matière (fil, grain) impose l'orientation de la pièce",
    )

    statut = models.CharField(max_length=20, choices=Statut.choices, default=Statut.EN_ATTENTE)
    message_erreur = models.TextField(blank=True)
    avertissements = models.JSONField(default=list, blank=True)

    # Géométrie calculée à l'import — repère local à la pièce (origine = coin bas-gauche du
    # rectangle englobant).
    surface_mm2 = models.FloatField(null=True, blank=True)
    perimetre_decoupe_mm = models.FloatField(
        null=True, blank=True, help_text="Longueur totale à découper : contour extérieur + trous"
    )
    largeur_mm = models.FloatField(null=True, blank=True, help_text="Largeur du rectangle englobant")
    hauteur_mm = models.FloatField(null=True, blank=True, help_text="Hauteur du rectangle englobant")
    nb_contours_interieurs = models.PositiveIntegerField(default=0, help_text="Nombre de trous détectés")
    contour_json = models.JSONField(
        null=True, blank=True, help_text="Contour extérieur et trous, coordonnées locales en mm"
    )

    date_import = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Pièce à découper"
        verbose_name_plural = "Pièces à découper"
        ordering = ["-date_import"]

    def __str__(self):
        return self.nom

    def clean(self):
        super().clean()
        if self.fichier_source:
            extension = self.fichier_source.name.rsplit(".", 1)[-1].lower()
            if extension not in (self.FormatSource.DXF, self.FormatSource.DWG):
                raise ValidationError({"fichier_source": "Seuls les fichiers .dxf et .dwg sont acceptés."})
            if self.format_source and self.format_source != extension:
                raise ValidationError(
                    {"format_source": "L'extension du fichier ne correspond pas au format déclaré."}
                )

    def save(self, *args, **kwargs):
        if self.fichier_source and not self.format_source:
            extension = self.fichier_source.name.rsplit(".", 1)[-1].lower()
            if extension in (self.FormatSource.DXF, self.FormatSource.DWG):
                self.format_source = extension
        super().save(*args, **kwargs)

    def importer_geometrie(self):
        """Extrait la géométrie du fichier source et met à jour la pièce en conséquence.

        Ne lève jamais d'exception : en cas d'échec, la pièce est marquée en erreur avec le
        détail dans `message_erreur`, pour que l'import via API/admin reste toujours possible
        à corriger sans perdre le fichier déjà déposé.
        """
        from .services.geometrie import ErreurImportGeometrie, extraire_geometrie

        try:
            resultat = extraire_geometrie(self.fichier_source.path, self.format_source)
        except ErreurImportGeometrie as exc:
            self.statut = self.Statut.ERREUR
            self.message_erreur = str(exc)
            self.save(update_fields=["statut", "message_erreur"])
            return False

        self.statut = self.Statut.OK
        self.message_erreur = ""
        self.surface_mm2 = resultat.surface_mm2
        self.perimetre_decoupe_mm = resultat.perimetre_mm
        self.largeur_mm = resultat.largeur_mm
        self.hauteur_mm = resultat.hauteur_mm
        self.nb_contours_interieurs = len(resultat.holes)
        self.contour_json = {"exterieur": resultat.exterior, "trous": resultat.holes}
        self.avertissements = resultat.avertissements
        self.save()
        return True


class ImbricationJob(models.Model):
    """Un calcul d'imbrication : un jeu de pièces (avec quantités) placées dans une surface de
    tôle donnée, avec estimation du nombre de feuilles et du coût matière."""

    article_matiere = models.ForeignKey(
        Article,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="imbrications",
        help_text="Tôle (article matière première) utilisée, pour le chiffrage matière",
    )
    largeur_feuille_mm = models.FloatField()
    longueur_feuille_mm = models.FloatField()
    marge_bord_mm = models.FloatField(default=5, help_text="Marge non utilisable en bord de feuille")
    espacement_pieces_mm = models.FloatField(default=5, help_text="Espacement minimal entre deux pièces")

    nb_feuilles = models.PositiveIntegerField(null=True, blank=True)
    surface_pieces_mm2 = models.FloatField(null=True, blank=True)
    surface_feuilles_mm2 = models.FloatField(null=True, blank=True)
    taux_utilisation_pct = models.FloatField(null=True, blank=True)
    cout_matiere_estime = models.FloatField(null=True, blank=True)
    pieces_non_placees = models.JSONField(default=list, blank=True)

    date_calcul = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Imbrication"
        verbose_name_plural = "Imbrications"
        ordering = ["-id"]

    def __str__(self):
        return f"Imbrication #{self.pk} ({self.largeur_feuille_mm:g}×{self.longueur_feuille_mm:g} mm)"

    def clean(self):
        super().clean()
        if self.article_matiere_id and self.article_matiere.nature != Article.Nature.MATIERE_PREMIERE:
            raise ValidationError({"article_matiere": "L'article matière doit être une matière première."})
        if self.largeur_feuille_mm and self.marge_bord_mm and self.largeur_feuille_mm - 2 * self.marge_bord_mm <= 0:
            raise ValidationError({"marge_bord_mm": "La marge de bord est trop grande pour la largeur de feuille."})
        if (
            self.longueur_feuille_mm
            and self.marge_bord_mm
            and self.longueur_feuille_mm - 2 * self.marge_bord_mm <= 0
        ):
            raise ValidationError({"marge_bord_mm": "La marge de bord est trop grande pour la longueur de feuille."})

    def calculer(self):
        """(Re)calcule l'imbrication à partir des lignes actuelles et persiste le résultat."""
        from .services.imbrication import ItemANester, calculer_imbrication

        items = [
            ItemANester(
                piece_id=ligne.piece_id,
                largeur_mm=ligne.piece.largeur_mm,
                hauteur_mm=ligne.piece.hauteur_mm,
                surface_mm2=ligne.piece.surface_mm2,
                rotation_autorisee=ligne.piece.rotation_autorisee,
                quantite=ligne.quantite,
            )
            for ligne in self.lignes.select_related("piece").all()
        ]
        resultat = calculer_imbrication(
            items,
            largeur_feuille_mm=self.largeur_feuille_mm,
            longueur_feuille_mm=self.longueur_feuille_mm,
            marge_bord_mm=self.marge_bord_mm,
            espacement_pieces_mm=self.espacement_pieces_mm,
        )

        self.placements.all().delete()
        ImbricationPlacement.objects.bulk_create(
            ImbricationPlacement(
                job=self,
                piece_id=placement.piece_id,
                numero_feuille=placement.numero_feuille,
                x_mm=placement.x_mm,
                y_mm=placement.y_mm,
                rotation_deg=placement.rotation_deg,
            )
            for placement in resultat.placements
        )

        self.nb_feuilles = resultat.nb_feuilles
        self.surface_pieces_mm2 = resultat.surface_pieces_mm2
        self.surface_feuilles_mm2 = resultat.surface_feuilles_mm2
        self.taux_utilisation_pct = resultat.taux_utilisation_pct
        self.pieces_non_placees = resultat.pieces_non_placees
        self.cout_matiere_estime = self._calculer_cout_matiere()
        self.date_calcul = timezone.now()
        self.save()
        return resultat

    def _calculer_cout_matiere(self):
        article = self.article_matiere
        if not article or article.unite_cout != Article.UniteCout.SURFACE or article.cout_unitaire is None:
            return None
        if not self.nb_feuilles:
            return None
        surface_feuille_m2 = (self.largeur_feuille_mm * self.longueur_feuille_mm) / 1_000_000
        return self.nb_feuilles * surface_feuille_m2 * article.cout_unitaire


class ImbricationLigne(models.Model):
    """Une pièce et sa quantité à imbriquer dans un job."""

    job = models.ForeignKey(ImbricationJob, on_delete=models.CASCADE, related_name="lignes")
    piece = models.ForeignKey(PieceDecoupe, on_delete=models.PROTECT, related_name="lignes_imbrication")
    quantite = models.PositiveIntegerField()

    class Meta:
        verbose_name = "Ligne d'imbrication"
        verbose_name_plural = "Lignes d'imbrication"
        unique_together = [("job", "piece")]

    def __str__(self):
        return f"{self.quantite} × {self.piece}"

    def clean(self):
        super().clean()
        if self.piece_id and self.piece.statut != PieceDecoupe.Statut.OK:
            raise ValidationError({"piece": "La pièce doit avoir été importée avec succès avant d'être imbriquée."})


class ImbricationPlacement(models.Model):
    """Position calculée d'une occurrence de pièce sur une feuille — résultat persisté d'un job."""

    job = models.ForeignKey(ImbricationJob, on_delete=models.CASCADE, related_name="placements")
    piece = models.ForeignKey(PieceDecoupe, on_delete=models.PROTECT, related_name="placements")
    numero_feuille = models.PositiveIntegerField()
    x_mm = models.FloatField()
    y_mm = models.FloatField()
    rotation_deg = models.PositiveIntegerField(choices=[(0, "0°"), (90, "90°")], default=0)

    class Meta:
        verbose_name = "Placement"
        verbose_name_plural = "Placements"
        ordering = ["job", "numero_feuille", "y_mm", "x_mm"]

    def __str__(self):
        return f"{self.piece} — feuille {self.numero_feuille} @ ({self.x_mm:g}, {self.y_mm:g})"
