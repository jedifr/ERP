from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from comptes.champs import ChampDecimal
from comptes.montants import PRIX, TAUX


class FamilleMatiere(models.Model):
    """Matière « générique » (acier, inox, aluminium…) à laquelle se rattachent les nuances précises (S235, 5754…).

    C'est à ce niveau que vivent les données communes : usinabilité standard, paramètres de coupe (vitesses, temps de
    perçage… de la base du logiciel de la machine). Une nuance hérite de sa famille ; elle peut surcharger un paramètre
    si besoin. `mots_cles` sert à rattacher automatiquement une nuance d'après son nom."""

    nom = models.CharField("nom", max_length=100, unique=True)
    nom_igems = models.CharField(
        "nom dans le logiciel de la machine", max_length=100, blank=True,
        help_text="Nom de la matière dans materials.lua (Steel, Stainless Steel, Aluminium…) : sert à l'import de la base de coupe.",
    )
    usinabilite = models.FloatField("usinabilité standard", null=True, blank=True, help_text="Indice d'usinabilité au jet d'eau")
    mots_cles = models.TextField(
        "mots-clés des nuances", blank=True,
        help_text="Un par ligne ou séparés par des virgules (ex. s235, s355, acier, steel). Une nuance dont le nom commence par l'un d'eux "
                  "est rattachée à cette famille. Les expressions à plusieurs mots (« en aw ») se cherchent dans le nom entier.",
    )
    gaz_laser_prefere = models.CharField(
        "gaz de coupe laser usuel", max_length=4, blank=True,
        choices=[("O2", "Oxygène"), ("N2", "Azote"), ("Air", "Air comprimé")],
        help_text="Gaz retenu au laser quand la pièce n'en précise pas (oxygène pour l'acier, azote pour l'inox et l'alu…).",
    )
    ordre = models.PositiveSmallIntegerField(
        "priorité", default=50, help_text="Les familles de priorité faible sont essayées d'abord (« inox » avant « acier »)."
    )

    class Meta:
        verbose_name = "Famille de matière"
        verbose_name_plural = "Familles de matière"
        ordering = ["ordre", "nom"]

    def __str__(self):
        return self.nom

    def liste_mots_cles(self):
        brut = (self.mots_cles or "").replace("\n", ",").split(",")
        return [m.strip().lower() for m in brut if m.strip()]

    @classmethod
    def pour_nom(cls, nom_matiere):
        """Famille dont un mot-clé correspond au nom d'une nuance (la plus prioritaire), ou None."""
        import re
        import unicodedata

        nom = "".join(c for c in unicodedata.normalize("NFD", (nom_matiere or "").lower()) if unicodedata.category(c) != "Mn")
        jetons = re.findall(r"[a-z0-9]+(?:\.[0-9]+)*", nom)
        for famille in cls.objects.all():
            for mot in famille.liste_mots_cles():
                mot = "".join(c for c in unicodedata.normalize("NFD", mot) if unicodedata.category(c) != "Mn")
                if " " in mot or "-" in mot:
                    if mot in nom:
                        return famille
                elif any(j.startswith(mot) for j in jetons):
                    return famille
        return None


class Matiere(models.Model):
    """Référentiel des matières : les nuances précises (S235, 5754…) ou les matières courantes (acier, aluminium…)."""

    nom = models.CharField("nom", max_length=100, primary_key=True)
    densite = models.FloatField("densité", help_text="kg/dm³, utilisée pour le calcul au poids")
    usinabilite = models.FloatField(
        "usinabilité", null=True, blank=True,
        help_text="Indice d'usinabilité au jet d'eau (acier 87,6 ; inox 81,9 ; cuivre/laiton 110 ; aluminium 213…) : sert à calculer "
                  "les vitesses de coupe quand elles n'ont pas été relevées sur la machine. Vide : celle de la famille.",
    )
    famille = models.ForeignKey(
        FamilleMatiere, verbose_name="famille de matière", on_delete=models.SET_NULL, null=True, blank=True, related_name="nuances",
        help_text="Matière générique dont cette nuance hérite les paramètres de coupe (S235 → Acier, 5754 → Aluminium…). "
                  "Rattachée automatiquement d'après le nom à la création si elle est laissée vide.",
    )

    class Meta:
        verbose_name = "Matière"
        verbose_name_plural = "Matières"
        ordering = ["nom"]

    def __str__(self):
        return self.nom

    def save(self, *args, **kwargs):
        if self._state.adding and self.famille_id is None:
            self.famille = FamilleMatiere.pour_nom(self.nom)  # S235 → Acier, 5754 → Aluminium…
        super().save(*args, **kwargs)

    @property
    def usinabilite_effective(self):
        """Usinabilité de la nuance, à défaut celle de sa famille."""
        if self.usinabilite:
            return self.usinabilite
        return self.famille.usinabilite if self.famille_id else None


class Article(models.Model):
    """Table unique portant deux natures : matière première ou fabriqué."""

    class Nature(models.TextChoices):
        MATIERE_PREMIERE = "matiere_premiere", "Matière première"
        FABRIQUE = "fabrique", "Fabriqué"
        SERVICE_ACHETE = "service_achete", "Service acheté"
        CONSOMMABLE = "consommable", "Consommable"
        COMPOSANT = "composant", "Composant"

    class UniteCout(models.TextChoices):
        SURFACE = "surface", "Au m² (€/m²)"
        LONGUEUR = "longueur", "Au mètre (€/m)"
        POIDS = "poids", "Au kilo (€/kg)"
        PIECE = "piece", "À l'unité (€/pièce)"

    class TypeProfil(models.TextChoices):
        TUBE_CARRE = "tube_carre", "Tube carré"
        TUBE_RECTANGULAIRE = "tube_rectangulaire", "Tube rectangulaire"
        CORNIERE = "corniere", "Cornière"
        PROFIL_I = "profil_i", "Profilé I"
        PROFIL_U = "profil_u", "Profilé U"

    reference = models.CharField("référence", max_length=100, primary_key=True)
    libelle = models.CharField(
        "libellé",
        max_length=200,
        blank=True,
        help_text="Nom/description lisible de l'article, en plus de la référence",
    )
    nature = models.CharField("nature", max_length=20, choices=Nature.choices)
    matiere = models.ForeignKey(
        Matiere,
        verbose_name="matière",
        on_delete=models.PROTECT,
        related_name="articles",
        null=True,
        blank=True,
        help_text="Pertinent pour tôles/profilés",
    )
    unite_cout = models.CharField(
        "unité de coût", max_length=20, choices=UniteCout.choices, null=True, blank=True,
        help_text="Comment le prix d'achat est exprimé. À l'unité, pour une tôle : prix de la feuille entière.",
    )
    epaisseur = models.FloatField("épaisseur", null=True, blank=True, help_text="Tôle")
    type_profil = models.CharField(
        "type de profil", max_length=20, choices=TypeProfil.choices, null=True, blank=True
    )
    poids_lineique = models.FloatField(
        "poids linéique", null=True, blank=True, help_text="kg/mètre (profilés vendus au poids)"
    )
    cout_unitaire = ChampDecimal(
        "coût unitaire", null=True, blank=True, help_text="Coût d'achat (matière première)", **PRIX,
    )
    taux_marge_defaut = ChampDecimal(
        "taux de marge par défaut (%)",
        null=True,
        blank=True,
        help_text="En pourcentage du coût : 20 = prix de vente × 1,20. Marge par défaut sur le coût matière (articles fabriqués)", **TAUX,
    )
    taux_tva = models.ForeignKey(
        "commercial.TauxTVA",
        verbose_name="taux de TVA (régime France)",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="articles",
        help_text=(
            "Taux normal appliqué à un client soumis à la TVA française. Un client "
            "exonéré, intracommunautaire ou hors UE applique automatiquement 0 %, quel "
            "que soit ce taux (voir chiffrage.moteur.resoudre_taux_tva)."
        ),
    )
    gere_en_stock = models.BooleanField(
        "géré en stock",
        null=True,
        blank=True,
        help_text=(
            "Vrai par défaut pour une matière première, faux par défaut pour un fabriqué, "
            "modifiable au cas par cas. Laisser vide pour appliquer le défaut."
        ),
    )
    stock_mini = models.FloatField(
        "stock minimum", null=True, blank=True, help_text="Seuil d'alerte de réapprovisionnement"
    )
    quantite_reappro = models.FloatField(
        "quantité de réapprovisionnement",
        null=True,
        blank=True,
        help_text="Quantité suggérée à commander",
    )

    class Meta:
        verbose_name = "Article"
        verbose_name_plural = "Articles"
        ordering = ["reference"]

    def __str__(self):
        if self.libelle:
            return f"{self.reference} — {self.libelle}"
        return self.reference

    def clean(self):
        super().clean()
        if self.nature == self.Nature.FABRIQUE and self.cout_unitaire is not None:
            raise ValidationError(
                {
                    "cout_unitaire": (
                        "Un article fabriqué n'a pas de coût unitaire fixe : son coût est recalculé "
                        "à chaque devis à partir de sa nomenclature et de sa gamme."
                    )
                }
            )

    def save(self, *args, **kwargs):
        if self.gere_en_stock is None:
            # Par défaut seule une matière première se gère en stock — et jamais si la société n'a pas de stock.
            self.gere_en_stock = bool(settings.STOCK_ACTIF and self.nature == self.Nature.MATIERE_PREMIERE)
        super().save(*args, **kwargs)


class RegleCreationTole(models.Model):
    """Règle de création d'une tôle (article matière première) : quand l'imbrication d'un devis ne trouve aucune tôle pour une matière
    et une épaisseur, un clic crée l'article d'après la règle (référence et libellé construits, unité et coût d'achat, TVA, stock).
    La règle la plus précise l'emporte : nuance, puis famille, puis toutes les matières ; à précision égale, la plage d'épaisseurs la
    plus étroite. Une règle sans coût crée la tôle sans prix : le coût matière reste à renseigner."""

    nom = models.CharField("nom", max_length=100, help_text="Ex. « Inox au kilo », « Acier standard »")
    famille = models.ForeignKey(
        FamilleMatiere, verbose_name="famille de matière", on_delete=models.CASCADE, null=True, blank=True, related_name="regles_tole",
        help_text="Vide et sans nuance : toutes les matières.",
    )
    matiere = models.ForeignKey(
        Matiere, verbose_name="nuance précise", on_delete=models.CASCADE, null=True, blank=True, related_name="regles_tole",
        help_text="Ex. 1.4307 : prime sur la règle de sa famille.",
    )
    epaisseur_min = models.FloatField("épaisseur min (mm)", null=True, blank=True, help_text="Vide : sans minimum.")
    epaisseur_max = models.FloatField("épaisseur max (mm)", null=True, blank=True, help_text="Vide : sans maximum.")
    modele_reference = models.CharField(
        "modèle de référence", max_length=80, default="TOLE-{matiere}-{epaisseur}",
        help_text="Variables : {matiere}, {famille}, {epaisseur} (3, 2.5…). Une référence déjà prise reçoit un suffixe -2, -3…",
    )
    modele_libelle = models.CharField(
        "modèle de libellé", max_length=120, default="Tôle {matiere} {epaisseur} mm", blank=True,
        help_text="Mêmes variables ; l'épaisseur s'écrit avec une virgule (2,5).",
    )
    unite_cout = models.CharField(
        "unité de coût", max_length=20, choices=Article.UniteCout.choices, default=Article.UniteCout.POIDS,
        help_text="Poids : €/kg (la tôle est valorisée d'après sa densité et son épaisseur). Surface : €/m². Pièce : prix de la feuille entière.",
    )
    cout_unitaire = ChampDecimal(
        "coût unitaire d'achat", null=True, blank=True, help_text="Dans l'unité ci-dessus. Vide : à renseigner sur chaque tôle créée.", **PRIX,
    )
    taux_tva = models.ForeignKey(
        "commercial.TauxTVA", verbose_name="taux de TVA", on_delete=models.PROTECT, null=True, blank=True, related_name="+",
    )
    gere_en_stock = models.BooleanField("géré en stock", null=True, blank=True, help_text="Vide : défaut d'une matière première.")
    stock_mini = models.FloatField("stock minimum", null=True, blank=True)
    quantite_reappro = models.FloatField("quantité de réapprovisionnement", null=True, blank=True)
    actif = models.BooleanField("active", default=True)

    class Meta:
        verbose_name = "Règle de création de tôle"
        verbose_name_plural = "Règles de création de tôle"
        ordering = ["nom"]

    def __str__(self):
        return self.nom

    def clean(self):
        super().clean()
        if self.matiere_id and self.famille_id and self.matiere.famille_id != self.famille_id:
            raise ValidationError({"matiere": f"« {self.matiere} » n'appartient pas à la famille « {self.famille} »."})
        if self.epaisseur_min is not None and self.epaisseur_max is not None and self.epaisseur_max < self.epaisseur_min:
            raise ValidationError({"epaisseur_max": "L'épaisseur maximale est inférieure à la minimale."})
        for champ in ("modele_reference", "modele_libelle"):
            try:
                self._remplir(getattr(self, champ), "S235", 3.0, "Acier")
            except (KeyError, IndexError, ValueError):
                raise ValidationError({champ: "Variables autorisées : {matiere}, {famille}, {epaisseur}."})

    @staticmethod
    def _remplir(modele, matiere, epaisseur, famille, virgule=False):
        texte = f"{epaisseur:g}"
        return modele.format(matiere=matiere, famille=famille or "", epaisseur=texte.replace(".", ",") if virgule else texte).strip()

    def convient_a(self, matiere, epaisseur):
        if not self.actif:
            return False
        if self.matiere_id and self.matiere_id != matiere.pk:
            return False
        if self.famille_id and self.famille_id != matiere.famille_id:
            return False
        if self.epaisseur_min is not None and epaisseur < self.epaisseur_min - 1e-9:
            return False
        return self.epaisseur_max is None or epaisseur <= self.epaisseur_max + 1e-9

    @classmethod
    def pour(cls, matiere, epaisseur):
        """Règle applicable (la plus précise) ou None."""
        candidates = [r for r in cls.objects.filter(actif=True).select_related("famille", "matiere") if r.convient_a(matiere, epaisseur)]
        if not candidates:
            return None

        def precision(r):
            largeur = (r.epaisseur_max if r.epaisseur_max is not None else 1e9) - (r.epaisseur_min if r.epaisseur_min is not None else 0)
            return (0 if r.matiere_id else 1 if r.famille_id else 2, largeur, r.pk)

        return min(candidates, key=precision)

    def reference_pour(self, matiere, epaisseur):
        """Référence demandée par le modèle, rendue unique (suffixe -2, -3…) parmi les articles existants."""
        base = self._remplir(self.modele_reference, matiere.nom, epaisseur, matiere.famille.nom if matiere.famille_id else "")[:90].strip()
        reference, n = base, 1
        while Article.objects.filter(pk=reference).exists():
            n += 1
            reference = f"{base}-{n}"
        return reference

    def libelle_pour(self, matiere, epaisseur):
        return self._remplir(self.modele_libelle, matiere.nom, epaisseur, matiere.famille.nom if matiere.famille_id else "", virgule=True)[:200]

    def creer_tole(self, matiere, epaisseur):
        """Crée et renvoie l'article tôle (matière première) de cette matière et de cette épaisseur."""
        return Article.objects.create(
            reference=self.reference_pour(matiere, epaisseur), libelle=self.libelle_pour(matiere, epaisseur),
            nature=Article.Nature.MATIERE_PREMIERE, matiere=matiere, epaisseur=float(epaisseur), unite_cout=self.unite_cout,
            cout_unitaire=self.cout_unitaire, taux_tva=self.taux_tva, gere_en_stock=self.gere_en_stock,
            stock_mini=self.stock_mini, quantite_reappro=self.quantite_reappro,
        )


class PosteTravail(models.Model):
    """Un centre de charge logique (ex. "Mazak"), même si plusieurs machines identiques le composent."""

    class ModeCalcul(models.TextChoices):
        HORAIRE = "horaire", "Horaire"
        FORFAITAIRE = "forfaitaire", "Forfaitaire"

    nom = models.CharField("nom", max_length=100, primary_key=True)
    type_operation = models.CharField("type d'opération", max_length=100, blank=True)
    mode_calcul = models.CharField("mode de calcul", max_length=20, choices=ModeCalcul.choices)
    nombre_machines = models.PositiveIntegerField(
        "nombre de machines", default=1, help_text="Capacité agrégée (usage planning)"
    )
    temps_mise_en_place_min = models.FloatField(
        "mise en place d'une tôle (min)", null=True, blank=True,
        help_text="Machines de découpe (laser, jet d'eau) : temps de réglage appliqué UNE fois par tôle posée sur la machine, quel que soit le "
                  "nombre de pièces qu'elle contient. Peut être précisé par épaisseur dans les paramètres de coupe.",
    )
    taux_marge_defaut = ChampDecimal(
        "taux de marge par défaut (%)",
        null=True,
        blank=True,
        help_text="En pourcentage du coût : 20 = prix de vente × 1,20. Marge par défaut sur les opérations de ce poste", **TAUX,
    )

    class Meta:
        verbose_name = "Poste de travail"
        verbose_name_plural = "Postes de travail"
        ordering = ["nom"]

    def __str__(self):
        return self.nom


class DateRangeHistoriqueMixin:
    """Empêche le chevauchement de deux enregistrements actifs sur le même périmètre.

    Une sous-classe doit définir `historique_scope_fields`, la liste des champs
    identifiant le "créneau" historisé (ex. le poste, ou l'article + le poste + l'ordre).
    """

    historique_scope_fields = ()

    def clean(self):
        super().clean()
        if self.date_debut is not None and self.date_fin is not None and self.date_fin < self.date_debut:
            raise ValidationError({"date_fin": "La date de fin doit être postérieure à la date de début."})

        if self.date_debut is None:
            return

        scope = {field: getattr(self, field) for field in self.historique_scope_fields}
        if any(value is None for value in scope.values()):
            return

        qs = type(self).objects.filter(**scope)
        if self.pk is not None:
            qs = qs.exclude(pk=self.pk)

        for other in qs:
            starts_before_other_ends = other.date_fin is None or self.date_debut <= other.date_fin
            other_starts_before_self_ends = self.date_fin is None or other.date_debut <= self.date_fin
            if starts_before_other_ends and other_starts_before_self_ends:
                raise ValidationError(
                    "Cette période chevauche une période existante "
                    f"({other.date_debut} → {other.date_fin or '…'}) pour le même périmètre."
                )


class TarifPoste(DateRangeHistoriqueMixin, models.Model):
    """Historise le coût horaire d'un poste — permet de recalculer un ancien devis avec les taux d'époque."""

    historique_scope_fields = ("poste",)

    poste = models.ForeignKey(
        PosteTravail, verbose_name="poste", on_delete=models.CASCADE, related_name="tarifs"
    )
    cout_horaire = ChampDecimal("coût horaire", help_text="€/heure", **PRIX)
    date_debut = models.DateField("date de début")
    date_fin = models.DateField("date de fin", null=True, blank=True)

    class Meta:
        verbose_name = "Tarif de poste"
        verbose_name_plural = "Tarifs de poste"
        ordering = ["poste", "-date_debut"]

    def __str__(self):
        return f"{self.poste} : {self.cout_horaire} €/h ({self.date_debut} → {self.date_fin or '…'})"


class GammeType(models.Model):
    """Gamme type (« Pliage + soudure », « Ébavurage + traitement »…) : une suite d'opérations réutilisable, qu'on ajoute
    en un clic à la gamme d'un article fabriqué. Les étapes sont copiées dans la gamme de l'article : modifier ensuite la
    gamme type ne change pas les gammes déjà créées."""

    nom = models.CharField("nom", max_length=100, unique=True)
    description = models.CharField("description", max_length=255, blank=True)

    class Meta:
        verbose_name = "Gamme type"
        verbose_name_plural = "Gammes types"
        ordering = ["nom"]

    def __str__(self):
        return self.nom


class GammeTypeEtape(models.Model):
    gamme_type = models.ForeignKey(GammeType, verbose_name="gamme type", on_delete=models.CASCADE, related_name="etapes")
    ordre = models.PositiveIntegerField("ordre")
    poste = models.ForeignKey(PosteTravail, verbose_name="poste", on_delete=models.PROTECT, related_name="gamme_type_etapes")
    temps_fixe = models.FloatField("temps fixe (min)", null=True, blank=True, help_text="Réglage (poste horaire)")
    temps_variable = models.FloatField("temps par pièce (min)", null=True, blank=True, help_text="Poste horaire")
    cout_forfaitaire = ChampDecimal("coût forfaitaire par pièce", null=True, blank=True, help_text="Poste forfaitaire (sous-traitance)", **PRIX)

    class Meta:
        verbose_name = "Étape de gamme type"
        verbose_name_plural = "Étapes de gamme type"
        ordering = ["gamme_type", "ordre"]

    def __str__(self):
        return f"{self.gamme_type} — étape {self.ordre} ({self.poste})"

    def clean(self):
        super().clean()
        if not self.poste_id:
            return
        if self.poste.mode_calcul == PosteTravail.ModeCalcul.HORAIRE:
            if self.temps_fixe is None or self.temps_variable is None:
                raise ValidationError("Un poste en mode horaire requiert un temps fixe et un temps par pièce.")
            if self.cout_forfaitaire is not None:
                raise ValidationError({"cout_forfaitaire": "Non applicable pour un poste en mode horaire."})
        elif self.cout_forfaitaire is None:
            raise ValidationError({"cout_forfaitaire": "Un poste en mode forfaitaire requiert un coût forfaitaire."})
        elif self.temps_fixe is not None or self.temps_variable is not None:
            raise ValidationError("Temps fixe/variable non applicables pour un poste en mode forfaitaire.")


class Nomenclature(models.Model):
    """Ce qu'un article fabriqué consomme."""

    article_parent = models.ForeignKey(
        Article, verbose_name="article parent", on_delete=models.CASCADE, related_name="composants"
    )
    article_composant = models.ForeignKey(
        Article, verbose_name="article composant", on_delete=models.PROTECT, related_name="utilise_dans"
    )
    longueur_mm = models.FloatField(
        "longueur (mm)", null=True, blank=True, help_text="Tôle (avec largeur) ou profilé (seule)"
    )
    largeur_mm = models.FloatField("largeur (mm)", null=True, blank=True, help_text="Tôle uniquement")
    quantite = models.FloatField("quantité", help_text="Nombre de pièces/découpes identiques")

    class Meta:
        verbose_name = "Ligne de nomenclature"
        verbose_name_plural = "Nomenclatures"
        ordering = ["article_parent", "article_composant"]

    def __str__(self):
        return f"{self.article_parent} ← {self.quantite} × {self.article_composant}"

    def clean(self):
        super().clean()
        if self.article_parent_id and self.article_parent.nature != Article.Nature.FABRIQUE:
            raise ValidationError(
                {"article_parent": "Seul un article fabriqué peut porter une nomenclature."}
            )
        if self.article_parent_id and self.article_composant_id and self.article_parent_id == self.article_composant_id:
            raise ValidationError(
                {"article_composant": "Un article ne peut pas être son propre composant."}
            )


class Gamme(DateRangeHistoriqueMixin, models.Model):
    """Suite d'opérations (postes) suivie par un article fabriqué, historisée."""

    historique_scope_fields = ("article", "poste", "ordre")

    article = models.ForeignKey(
        Article, verbose_name="article", on_delete=models.CASCADE, related_name="gamme_etapes"
    )
    poste = models.ForeignKey(
        PosteTravail, verbose_name="poste", on_delete=models.PROTECT, related_name="gamme_etapes"
    )
    ordre = models.PositiveIntegerField("ordre")
    temps_fixe = models.FloatField("temps fixe", null=True, blank=True, help_text="Réglage (mode horaire)")
    temps_variable = models.FloatField(
        "temps variable", null=True, blank=True, help_text="Temps unitaire (mode horaire)"
    )
    cout_forfaitaire = ChampDecimal(
        "coût forfaitaire", null=True, blank=True, help_text="Mode forfaitaire (sous-traitance)", **PRIX,
    )
    date_debut = models.DateField("date de début")
    date_fin = models.DateField(
        "date de fin", null=True, blank=True, help_text="Historisation de la révision"
    )
    origine = models.CharField(
        "origine", max_length=12, default="manuelle", choices=[("manuelle", "Saisie à la main"), ("decoupe", "Calculée depuis la pièce à découper")],
        help_text="Une étape « calculée » est remise à jour depuis la pièce à découper (temps de coupe) ; la modifier à la main la fait revenir en « saisie à la main ».",
    )

    class Meta:
        verbose_name = "Étape de gamme"
        verbose_name_plural = "Gammes"
        ordering = ["article", "ordre", "-date_debut"]

    def __str__(self):
        return f"{self.article} — étape {self.ordre} ({self.poste})"

    def clean(self):
        super().clean()
        if self.article_id and self.article.nature != Article.Nature.FABRIQUE:
            raise ValidationError({"article": "Seul un article fabriqué peut porter une gamme."})

        if self.poste_id:
            if self.poste.mode_calcul == PosteTravail.ModeCalcul.HORAIRE:
                if self.temps_fixe is None or self.temps_variable is None:
                    raise ValidationError(
                        "Un poste en mode horaire requiert un temps fixe et un temps variable."
                    )
                if self.cout_forfaitaire is not None:
                    raise ValidationError(
                        {"cout_forfaitaire": "Non applicable pour un poste en mode horaire."}
                    )
            elif self.poste.mode_calcul == PosteTravail.ModeCalcul.FORFAITAIRE:
                if self.cout_forfaitaire is None:
                    raise ValidationError(
                        {"cout_forfaitaire": "Un poste en mode forfaitaire requiert un coût forfaitaire."}
                    )
                if self.temps_fixe is not None or self.temps_variable is not None:
                    raise ValidationError(
                        "Temps fixe/variable non applicables pour un poste en mode forfaitaire."
                    )
