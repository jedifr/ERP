import datetime

from django.conf import settings
from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import path, reverse
from django.utils.html import escape, format_html
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_http_methods
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import action as unfold_action

from comptes.colonnes import ColonnesPersonnalisablesMixin
from comptes.exports import ExportCsvMixin
from comptes.lots_admin import ModificationParLotsMixin, vue_lot
from achats.models import ArticleFournisseur
from comptabilite.models import ArticleCompteAchat, ArticleCompteVente

from .models import Article, FamilleMatiere, Gamme, GammeType, GammeTypeEtape, Matiere, Nomenclature, PosteTravail, ProfileSection, RegleCreationTole, TarifPoste
from . import lots_champs
from .services import DuplicationError, dupliquer_article, renommer_article


class NomenclatureInline(TabularInline):
    model = Nomenclature
    fk_name = "article_parent"
    extra = 1
    autocomplete_fields = ["article_composant"]


class GammeInline(TabularInline):
    model = Gamme
    extra = 1
    autocomplete_fields = ["poste"]
    readonly_fields = ["origine"]


class ArticleFournisseurInline(TabularInline):
    # Ajout rapide des fournisseurs d'un article achetable depuis sa fiche ;
    # l'historique des tarifs (TarifAchatArticle), lui, se gère sur la fiche
    # ArticleFournisseur dédiée (achats/admin.py — même principe que
    # CommandeLigne, inlinée ET dotée de sa propre fiche).
    model = ArticleFournisseur
    extra = 0
    autocomplete_fields = ["fournisseur"]


class ArticleCompteVenteInline(TabularInline):
    # OneToOneField : Django limite automatiquement à un seul formulaire.
    # Surcharge, pour cet article, le compte de vente par défaut utilisé
    # par la génération des écritures comptables (comptabilite.generation).
    model = ArticleCompteVente
    extra = 1
    autocomplete_fields = ["poste_gestion", "compte_vente", "code_analytique"]


class ArticleCompteAchatInline(TabularInline):
    # Même principe côté achat — purement déclaratif pour l'instant, voir
    # ArticleCompteAchat (aucune génération d'écriture d'achat automatique
    # n'existe encore).
    model = ArticleCompteAchat
    extra = 1
    autocomplete_fields = ["poste_gestion", "compte_achat", "code_analytique"]


@admin.register(FamilleMatiere)
class FamilleMatiereAdmin(ModelAdmin):
    """Matières génériques (Acier, Inox, Aluminium…) : les nuances précises (S235, 5754…) s'y rattachent et en héritent
    l'usinabilité et les paramètres de coupe (temps de perçage, vitesses… de la base du logiciel de la machine)."""

    list_display = ["nom", "nom_igems", "usinabilite", "nb_nuances", "nb_parametres", "ordre"]
    search_fields = ["nom", "nom_igems", "mots_cles"]
    ordering = ["ordre", "nom"]
    actions = ["action_rattacher_nuances"]
    fieldsets = [
        (None, {"fields": ["nom", "nom_igems", "usinabilite", "ordre"]}),
        ("Rattachement automatique des nuances", {"fields": ["mots_cles"]}),
    ]

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_nuances=Count("nuances", distinct=True), _parametres=Count("parametres_coupe", distinct=True))

    @admin.display(description="Nuances", ordering="_nuances")
    def nb_nuances(self, obj):
        return obj._nuances

    @admin.display(description="Paramètres de coupe", ordering="_parametres")
    def nb_parametres(self, obj):
        return obj._parametres

    @admin.action(description="Rattacher les matières sans famille (d'après leur nom)")
    def action_rattacher_nuances(self, request, queryset):
        rattacher_matieres(self, request, Matiere.objects.filter(famille__isnull=True))


def rattacher_matieres(modele_admin, request, matieres):
    faites, inconnues = 0, []
    for matiere in matieres:
        famille = FamilleMatiere.pour_nom(matiere.nom)
        if famille is None:
            inconnues.append(matiere.nom)
            continue
        matiere.famille = famille
        matiere.save(update_fields=["famille"])
        faites += 1
    modele_admin.message_user(request, f"{faites} matière(s) rattachée(s) à une famille.", level=messages.SUCCESS)
    if inconnues:
        modele_admin.message_user(
            request, "Aucune famille ne correspond à : " + ", ".join(inconnues) + " (complétez les mots-clés d'une famille).", level=messages.WARNING
        )


@admin.register(Matiere)
class MatiereAdmin(ModificationParLotsMixin, ModelAdmin):
    list_display = ["nom", "famille", "densite", "usinabilite"]
    list_filter = ["famille"]
    search_fields = ["nom", "famille__nom"]
    autocomplete_fields = ["famille"]
    actions = ["action_modifier_par_lots", "action_usinabilite_standard", "action_rattacher_famille"]
    actions_list = ["action_catalogue"]
    champs_lot = lots_champs.champs_matiere()

    @unfold_action(description="Base matières rapide", url_path="catalogue", icon="library_add")
    def action_catalogue(self, request):
        """Crée d'un coup matières, tôles, paramètres jet d'eau et règles : on coche des nuances et des épaisseurs, on valide."""
        from decimal import Decimal, InvalidOperation

        from django.core.exceptions import PermissionDenied
        from django.template.response import TemplateResponse

        from . import catalogue_matieres as cat

        if not (request.user.has_perm("technique.add_matiere") and request.user.has_perm("technique.add_article")):
            raise PermissionDenied
        rapport = None
        post = request.POST if request.method == "POST" else {}
        if request.method == "POST":
            selection = []
            for i, (nom, famille, densite) in enumerate(cat.CATALOGUE):
                if post.get(f"n_{i}"):
                    try:
                        prix = Decimal(post.get(f"p_{i}", "").replace(",", ".")) if post.get(f"p_{i}", "").strip() else None
                        dens = float(post.get(f"d_{i}", "").replace(",", ".") or densite)
                    except (InvalidOperation, ValueError):
                        messages.error(request, f"{nom} : prix ou densité invalide.")
                        selection = None
                        break
                    selection.append({"nom": nom, "famille": famille, "densite": dens, "prix": prix})
            if selection is not None:
                selection += [{"nom": n, "famille": "", "densite": d, "prix": p} for n, d, p in cat.lire_tableau(post.get("tableau", ""))]
                epaisseurs = [v for v in request.POST.getlist("epaisseur")] + [x for x in post.get("autres_epaisseurs", "").replace(";", ",").split(",") if x.strip()]
                if not selection or not cat._epaisseurs(epaisseurs):
                    messages.error(request, "Cochez au moins une nuance et une épaisseur.")
                else:
                    try:
                        rapport = cat.creer_base(
                            selection, epaisseurs, request.user, unite_cout=post.get("unite_cout", "poids"),
                            modele_reference=post.get("modele_reference") or "TOLE-{matiere}-{epaisseur}",
                            modele_libelle=post.get("modele_libelle", "Tôle {matiere} {epaisseur} mm"), creer_regle=bool(post.get("creer_regle")),
                            jet_eau=bool(post.get("jet_eau")),
                        )
                    except (KeyError, IndexError, ValueError):
                        messages.error(request, "Modèle de référence ou de libellé invalide (variables : {matiere}, {epaisseur}, {famille}).")
        return TemplateResponse(request, "admin/technique/catalogue_matieres.html", {
            **self.admin_site.each_context(request), "title": "Base matières rapide", "opts": self.model._meta, "rapport": rapport,
            "catalogue": [{"i": i, "nom": n, "famille": f, "densite": d, "coche": bool(post.get(f"n_{i}")), "prix": post.get(f"p_{i}", ""),
                           "existe": Matiere.objects.filter(pk=n).exists()} for i, (n, f, d) in enumerate(cat.CATALOGUE)],
            "epaisseurs": [{"valeur": e, "coche": str(e) in post.getlist("epaisseur") if post else e in cat.PRESETS["courantes"]} for e in cat.EPAISSEURS],
            "presets": cat.PRESETS, "post": post,
        })

    @admin.action(description="Rattacher à une famille (d'après le nom : S235 → Acier, 5754 → Aluminium…)")
    def action_rattacher_famille(self, request, queryset):
        rattacher_matieres(self, request, queryset)

    @admin.action(description="Renseigner l'usinabilité standard (d'après le nom de la matière)")
    def action_usinabilite_standard(self, request, queryset):
        from decoupe.services.vitesses import usinabilite_standard_pour

        faites, inconnues = 0, []
        for matiere in queryset.filter(usinabilite__isnull=True):
            valeur = usinabilite_standard_pour(matiere.nom)
            if valeur is None and matiere.famille_id:
                continue  # héritera de l'usinabilité de sa famille
            if valeur is None:
                inconnues.append(matiere.nom)
                continue
            matiere.usinabilite = valeur
            matiere.save(update_fields=["usinabilite"])
            faites += 1
        self.message_user(request, f"Usinabilité renseignée pour {faites} matière(s) (celles déjà renseignées sont inchangées).", level=messages.SUCCESS)
        if inconnues:
            self.message_user(request, "Matière non reconnue, à saisir à la main : " + ", ".join(inconnues), level=messages.WARNING)


@admin.register(RegleCreationTole)
class RegleCreationToleAdmin(ModelAdmin):
    """Règles qui créent une tôle en un clic depuis l'imbrication d'un devis (référence, libellé, unité et coût d'achat, TVA, stock)."""

    list_display = ["nom", "perimetre", "epaisseurs", "unite_cout", "cout_unitaire", "exemple", "actif"]
    list_filter = ["actif", "famille"]
    search_fields = ["nom", "famille__nom", "matiere__nom"]
    autocomplete_fields = ["famille", "matiere"]
    fieldsets = [
        (None, {"fields": ["nom", "actif"]}),
        ("Quand l'appliquer", {"fields": ["famille", "matiere", "epaisseur_min", "epaisseur_max"]}),
        ("Article créé", {"fields": ["modele_reference", "modele_libelle", "unite_cout", "cout_unitaire", "taux_tva", "gere_en_stock", "stock_mini", "quantite_reappro"]}),
    ]

    @admin.display(description="S'applique à")
    def perimetre(self, obj):
        return obj.matiere.nom if obj.matiere_id else obj.famille.nom if obj.famille_id else "Toutes les matières"

    @admin.display(description="Épaisseurs")
    def epaisseurs(self, obj):
        if obj.epaisseur_min is None and obj.epaisseur_max is None:
            return "toutes"
        return f"{'' if obj.epaisseur_min is None else format(obj.epaisseur_min, 'g')} → {'' if obj.epaisseur_max is None else format(obj.epaisseur_max, 'g')} mm"

    @admin.display(description="Exemple (3 mm)")
    def exemple(self, obj):
        matiere = obj.matiere or Matiere(nom="S235", famille=obj.famille)
        return obj._remplir(obj.modele_reference, matiere.nom, 3.0, obj.famille.nom if obj.famille_id else "")


@staff_member_required
@require_http_methods(["POST"])
def dupliquer_article_view(request, reference):
    article = get_object_or_404(Article, pk=reference)
    try:
        copie = dupliquer_article(article)
    except DuplicationError as exc:
        messages.error(request, f"Duplication impossible : {exc}")
        return redirect("admin:technique_article_change", reference)

    messages.success(request, f"« {article} » dupliqué en « {copie} ». Vous pouvez modifier la copie.")
    return redirect("admin:technique_article_change", copie.pk)


@staff_member_required
@require_http_methods(["POST"])
def renommer_article_view(request, reference):
    article = get_object_or_404(Article, pk=reference)
    if not request.user.has_perm("technique.change_article"):
        messages.error(request, "Vous n'avez pas le droit de modifier les articles.")
        return redirect("admin:technique_article_change", reference)
    try:
        nouveau = renommer_article(article, request.POST.get("nouvelle_reference"))
    except DuplicationError as exc:
        messages.error(request, f"Renommage impossible : {exc}")
        return redirect("admin:technique_article_change", reference)
    messages.success(request, f"« {reference} » renommé en « {nouveau.reference} » (devis, commandes, nomenclatures et stock suivent).")
    return redirect("admin:technique_article_change", nouveau.pk)


@admin.register(Article)
class ArticleAdmin(ModificationParLotsMixin, ColonnesPersonnalisablesMixin, ExportCsvMixin, ModelAdmin):
    list_display = [
        "reference",
        "libelle",
        "nature",
        "matiere",
        "unite_cout",
        "cout_unitaire",
        "gere_en_stock",
        "stock_mini",
    ]
    list_filter = ["nature", "unite_cout", "type_profil", "gere_en_stock"]
    actions = ["action_modifier_par_lots", "action_ajouter_gamme_type", "action_affecter_compte"]
    champs_lot = lots_champs.champs_article()
    search_fields = ["reference", "libelle"]
    autocomplete_fields = ["matiere"]
    inlines = [
        NomenclatureInline,
        GammeInline,
        ArticleFournisseurInline,
        ArticleCompteVenteInline,
        ArticleCompteAchatInline,
    ]

    class Media:
        js = ["technique/article_admin.js"]

    def save_formset(self, request, form, formset, change):
        # Une étape de gamme calculée depuis la pièce à découper, retouchée à la main, devient « saisie à la main » :
        # un recalcul ultérieur ne l'écrase plus.
        if formset.model is Gamme:
            for f in formset.forms:
                if f.instance.pk and f.has_changed() and f.instance.origine == "decoupe":
                    f.instance.origine = "manuelle"
        super().save_formset(request, form, formset, change)

    _CHAMPS_STOCK = ("gere_en_stock", "stock_mini", "quantite_reappro")

    # Fiche en deux colonnes : saisie à gauche, récapitulatif (calculé, en lecture seule) à droite.
    CHAMPS_RECAPITULATIF = [
        "composition_recap", "achat_recap", "stock_recap", "utilise_dans_recap", "activite_recap",
    ]
    readonly_fields = CHAMPS_RECAPITULATIF

    def get_readonly_fields(self, request, obj=None):
        # La référence est la clé de l'article : la modifier dans le formulaire créerait un second article. On la change par « Renommer ».
        return [*self.readonly_fields, "reference"] if obj is not None and obj.pk else self.readonly_fields

    def get_fieldsets(self, request, obj=None):
        recap = [c for c in self.CHAMPS_RECAPITULATIF if settings.STOCK_ACTIF or c != "stock_recap"]
        saisie = [c for c in self.get_fields(request, obj) if c not in self.CHAMPS_RECAPITULATIF]
        return [
            (None, {"fields": saisie, "classes": ["fiche-saisie"]}),
            ("Récapitulatif", {"fields": recap, "classes": ["fiche-recap"]}),
        ]

    @staticmethod
    def _vide(obj):
        return obj is None or not obj.pk

    @admin.display(description="Composition")
    def composition_recap(self, obj):
        if self._vide(obj):
            return "—"
        if obj.nature != Article.Nature.FABRIQUE:
            return "Article acheté : pas de nomenclature ni de gamme"
        nb_composants = obj.composants.count()
        nb_etapes = obj.gamme_etapes.count()
        lignes = [f"{nb_composants} composant(s) en nomenclature", f"{nb_etapes} étape(s) de gamme"]
        if not nb_composants or not nb_etapes:
            lignes.append("À compléter avant de pouvoir chiffrer cet article")
        return mark_safe("<br>".join(escape(l) for l in lignes))

    @admin.display(description="Prix d'achat actuel")
    def achat_recap(self, obj):
        if self._vide(obj):
            return "—"
        if obj.nature == Article.Nature.FABRIQUE:
            return "Article fabriqué : coût recalculé à chaque devis"
        lignes = []
        for af in obj.fournisseurs.select_related("fournisseur"):
            tarif = af.tarif_actuel
            prix = f"{tarif.prix_unitaire:,.4f} €".replace(",", " ").replace(".", ",") if tarif else "pas de tarif en vigueur"
            lignes.append(f"{af.fournisseur.raison_sociale} : {prix}")
        if obj.cout_unitaire is not None:
            lignes.insert(0, "Coût retenu : " + f"{obj.cout_unitaire:,.4f} €".replace(",", " ").replace(".", ","))
        return mark_safe("<br>".join(escape(l) for l in lignes)) if lignes else "Aucun fournisseur renseigné"

    @admin.display(description="Stock")
    def stock_recap(self, obj):
        if self._vide(obj):
            return "—"
        from stock.models import AlerteStock, stock_actif_pour, stock_total

        if not stock_actif_pour(obj):
            return "Article non géré en stock"
        total = stock_total(obj)
        lignes = [f"En stock : {total:g}"]
        if obj.stock_mini is not None:
            lignes.append(f"Seuil d'alerte : {obj.stock_mini:g}")
        if AlerteStock.objects.filter(article=obj, statut=AlerteStock.Statut.ACTIVE).exists():
            lignes.append("⚠ Alerte de réapprovisionnement active")
        return mark_safe("<br>".join(escape(l) for l in lignes))

    @admin.display(description="Utilisé dans")
    def utilise_dans_recap(self, obj):
        if self._vide(obj):
            return "—"
        parents = list(obj.utilise_dans.select_related("article_parent").order_by("article_parent_id")[:10])
        if not parents:
            return "Aucune nomenclature"
        liens = [
            format_html(
                '<a href="{}">{}</a> (× {})',
                reverse("admin:technique_article_change", args=[n.article_parent_id]),
                n.article_parent,
                f"{n.quantite:g}",
            )
            for n in parents
        ]
        plus = obj.utilise_dans.count() - len(parents)
        if plus > 0:
            liens.append(escape(f"… et {plus} autre(s)"))
        return mark_safe("<br>".join(liens))

    @admin.display(description="Activité")
    def activite_recap(self, obj):
        if self._vide(obj):
            return "—"
        lignes = [
            f"{obj.devis_lignes.count()} ligne(s) de devis",
            f"{obj.lignes_commande.count()} ligne(s) de commande",
        ]
        if obj.nature == Article.Nature.FABRIQUE:
            lignes.append(f"{obj.ordres_fabrication.count()} ordre(s) de fabrication")
        return mark_safe("<br>".join(escape(l) for l in lignes))

    def get_exclude(self, request, obj=None):
        exclus = list(super().get_exclude(request, obj) or [])
        return exclus + list(self._CHAMPS_STOCK) if not settings.STOCK_ACTIF else exclus or None

    def get_list_display(self, request):
        colonnes = super().get_list_display(request)
        return colonnes if settings.STOCK_ACTIF else [c for c in colonnes if c not in self._CHAMPS_STOCK]

    def get_list_filter(self, request):
        filtres = super().get_list_filter(request)
        return filtres if settings.STOCK_ACTIF else [f for f in filtres if f not in self._CHAMPS_STOCK]

    @admin.action(description="Ajouter une gamme type…", permissions=["change"])
    def action_ajouter_gamme_type(self, request, queryset):
        """Ajoute les étapes d'une gamme type à la gamme de chaque article fabriqué sélectionné."""
        from django.template.response import TemplateResponse

        from comptes import lots
        from . import gamme_editeur

        types = GammeType.objects.order_by("nom")
        if "confirmer" in request.POST:
            gamme_type = GammeType.objects.filter(pk=request.POST.get("gamme_type")).first()
            if gamme_type is None:
                messages.error(request, "Choisissez une gamme type.")
            else:
                crees, ignores = [], []
                for article in queryset.order_by("reference"):
                    avant = set(Gamme.objects.filter(article=article).values_list("pk", flat=True))
                    try:
                        gamme_editeur.ajouter_gamme_type(article, gamme_type)
                    except gamme_editeur.ErreurGamme as exc:
                        ignores.append(f"{article.reference} : {exc}")
                        continue
                    crees += [("technique.Gamme", str(pk)) for pk in Gamme.objects.filter(article=article).values_list("pk", flat=True) if pk not in avant]
                if crees:
                    lots._enregistrer_lot(request.user, Gamme, f"Gamme type « {gamme_type} » ajoutée à {len(set(c[1] for c in crees))} étape(s)", [], crees)
                    self.message_user(request, f"Gamme type « {gamme_type} » ajoutée ({len(crees)} étape(s) créée(s)).", level=messages.SUCCESS)
                for texte in ignores[:10]:
                    self.message_user(request, texte, level=messages.WARNING)
                return None
        return TemplateResponse(request, "admin/comptes/lot_choix.html", {
            **self.admin_site.each_context(request), "title": "Ajouter une gamme type", "opts": self.model._meta, "action": "action_ajouter_gamme_type",
            "ids": request.POST.getlist("_selected_action"), "nombre": queryset.count(), "bouton": "Ajouter à la gamme",
            "intro": "Les étapes de la gamme type sont copiées à la fin de la gamme de chaque article fabriqué (les autres natures sont ignorées).",
            "champs": [{"nom": "gamme_type", "libelle": "Gamme type", "options": [(t.pk, f"{t.nom} ({t.etapes.count()} étapes)") for t in types], "valeur": request.POST.get("gamme_type", "")}],
        })

    @admin.action(description="Affecter un compte de vente ou d'achat…", permissions=["change"])
    def action_affecter_compte(self, request, queryset):
        """Crée ou met à jour le compte de vente (ou d'achat) et le code analytique des articles sélectionnés."""
        from django.template.response import TemplateResponse

        from comptabilite.models import CodeAnalytique, CompteComptable
        from comptes import lots

        if "confirmer" in request.POST:
            vente = request.POST.get("sens") != "achat"
            modele, champ = (ArticleCompteVente, "compte_vente") if vente else (ArticleCompteAchat, "compte_achat")
            nature_compte = "de vente" if vente else "d'achat"
            compte = CompteComptable.objects.filter(pk=request.POST.get("compte")).first()
            code = CodeAnalytique.objects.filter(pk=request.POST.get("code_analytique")).first() if request.POST.get("code_analytique") else None
            if compte is None:
                messages.error(request, "Choisissez un compte.")
            else:
                crees, modifications = [], []
                for article in queryset:
                    existant = modele.objects.filter(article=article).first()
                    if existant is None:
                        objet = modele.objects.create(article=article, code_analytique=code, **{champ: compte})
                        crees.append((modele._meta.label, str(objet.pk)))
                    else:
                        avant = {champ: existant.pk and getattr(existant, champ + "_id"), "code_analytique": existant.code_analytique_id}
                        setattr(existant, champ, compte)
                        existant.code_analytique = code
                        existant.save()
                        modifications.append({"pk": str(existant.pk), "avant": avant, "apres": {champ: compte.pk, "code_analytique": code.pk if code else None}})
                lots._enregistrer_lot(request.user, modele, f"Compte {nature_compte} {compte} affecté à {queryset.count()} article(s)", modifications, crees)
                self.message_user(request, f"Compte {compte} affecté à {queryset.count()} article(s) ({len(crees)} créé(s), {len(modifications)} mis à jour).", level=messages.SUCCESS)
                return None
        comptes = CompteComptable.objects.order_by("code")
        return TemplateResponse(request, "admin/comptes/lot_choix.html", {
            **self.admin_site.each_context(request), "title": "Affecter un compte", "opts": self.model._meta, "action": "action_affecter_compte",
            "ids": request.POST.getlist("_selected_action"), "nombre": queryset.count(), "bouton": "Affecter",
            "intro": "Pour ces articles, le compte remplace celui déjà affecté ; les articles qui n'en avaient pas en reçoivent un.",
            "champs": [
                {"nom": "sens", "libelle": "Compte de", "options": [("vente", "Vente"), ("achat", "Achat")], "valeur": request.POST.get("sens", "vente")},
                {"nom": "compte", "libelle": "Compte comptable", "options": [(c.pk, f"{c.pk} — {c.libelle}") for c in comptes], "valeur": request.POST.get("compte", "")},
                {"nom": "code_analytique", "libelle": "Code analytique (facultatif)", "vide": True, "options": [(c.pk, f"{c.pk} — {c.libelle}") for c in CodeAnalytique.objects.order_by("code")], "valeur": request.POST.get("code_analytique", "")},
            ],
        })

    def get_urls(self):
        urls = [
            path(
                "<str:reference>/dupliquer/",
                self.admin_site.admin_view(dupliquer_article_view),
                name="technique_article_dupliquer",
            ),
            path(
                "<str:reference>/renommer/",
                self.admin_site.admin_view(renommer_article_view),
                name="technique_article_renommer",
            ),
        ]
        return urls + super().get_urls()


class TarifPosteInline(TabularInline):
    """Tarifs horaires du poste, avec leur période (historique : un ancien devis se recalcule avec les taux d'époque)."""

    model = TarifPoste
    extra = 0
    fields = ["cout_horaire", "date_debut", "date_fin"]
    ordering = ["-date_debut"]
    verbose_name = "tarif"
    verbose_name_plural = "Tarifs (coût horaire, du plus récent au plus ancien — laissez la date de fin vide pour le tarif en cours)"


@admin.register(ProfileSection)
class ProfileSectionAdmin(ModelAdmin):
    """Sections de profilés (cornières, UPN, tubes) : une forme particulière de matière première, avec son article d'achat. Elles alimentent
    la bibliothèque de formes du devis (débits de profilés)."""

    list_display = ["designation", "famille", "masse_lineique", "longueur_barre_mm", "article", "verifie"]
    list_filter = ["famille", "verifie"]
    search_fields = ["designation"]
    autocomplete_fields = ["article"]
    actions = ["creer_articles", "marquer_verifie"]

    class Media:
        js = ["technique/section_admin.js"]

    @admin.action(description="Créer les articles d'achat manquants (coût à renseigner)")
    def creer_articles(self, request, queryset):
        crees = 0
        for section in queryset.filter(article__isnull=True):
            reference = f"PROF-{section.designation}"[:100]
            article, cree = Article.objects.get_or_create(
                reference=reference,
                defaults={
                    "libelle": f"{section.get_famille_display()} {section.designation}", "nature": Article.Nature.MATIERE_PREMIERE,
                    "unite_cout": Article.UniteCout.LONGUEUR, "poids_lineique": section.masse_lineique,
                },
            )
            section.article = article
            section.save(update_fields=["article"])
            crees += cree
        self.message_user(
            request, f"{crees} article(s) créé(s) en « longueur » avec le poids linéique : saisissez leur coût (€/kg).", messages.SUCCESS,
        )

    @admin.action(description="Marquer comme vérifié")
    def marquer_verifie(self, request, queryset):
        self.message_user(request, f"{queryset.update(verifie=True)} section(s) marquée(s) comme vérifiée(s).", messages.SUCCESS)


@admin.register(PosteTravail)
class PosteTravailAdmin(ModificationParLotsMixin, ModelAdmin):
    list_display = ["nom", "type_operation", "mode_calcul", "nombre_machines", "cout_horaire_actuel", "taux_marge_defaut"]
    list_filter = ["mode_calcul"]
    search_fields = ["nom"]
    inlines = [TarifPosteInline]
    actions = ["action_modifier_par_lots", "action_hausse_tarifs"]
    champs_lot = lots_champs.champs_poste()

    @admin.action(description="Modifier le coût horaire avec effet à une date (hausse)…", permissions=["change"])
    def action_hausse_tarifs(self, request, queryset):
        tarifs = TarifPoste.objects.filter(poste__in=queryset)
        return vue_lot(self, request, tarifs, lots_champs.champs_tarif_poste(), "action_hausse_tarifs", "Coût horaire des postes sélectionnés",
                       periode=True, description_lot="Coût horaire", modele=TarifPoste)

    @admin.display(description="Coût horaire actuel")
    def cout_horaire_actuel(self, obj):
        aujourdhui = datetime.date.today()
        tarif = obj.tarifs.filter(date_debut__lte=aujourdhui).filter(Q(date_fin__isnull=True) | Q(date_fin__gte=aujourdhui)).order_by("-date_debut").first()
        if tarif is None:
            return format_html('<span style="color:#b91c1c">aucun tarif</span>') if obj.mode_calcul == PosteTravail.ModeCalcul.HORAIRE else "—"
        return f"{tarif.cout_horaire} €/h"


@admin.register(TarifPoste)
class TarifPosteAdmin(ModificationParLotsMixin, ModelAdmin):
    list_display = ["poste", "cout_horaire", "date_debut", "date_fin"]
    actions = ["action_modifier_periode"]
    champs_periode = lots_champs.champs_tarif_poste()
    list_filter = ["poste"]
    autocomplete_fields = ["poste"]


@admin.register(Nomenclature)
class NomenclatureAdmin(ModelAdmin):
    list_display = ["article_parent", "article_composant", "quantite", "longueur_mm", "largeur_mm"]
    search_fields = ["article_parent__reference", "article_composant__reference"]
    autocomplete_fields = ["article_parent", "article_composant"]


@admin.register(Gamme)
class GammeAdmin(ModificationParLotsMixin, ModelAdmin):
    list_display = ["article", "ordre", "poste", "date_debut", "date_fin", "origine"]
    actions = ["action_modifier_periode"]
    champs_periode = lots_champs.champs_gamme()
    readonly_fields = ["origine"]

    def save_model(self, request, obj, form, change):
        if change and obj.origine == "decoupe" and form.has_changed():
            obj.origine = "manuelle"
        super().save_model(request, obj, form, change)
    list_filter = ["poste"]
    search_fields = ["article__reference"]
    autocomplete_fields = ["article", "poste"]


class GammeTypeEtapeInline(TabularInline):
    model = GammeTypeEtape
    extra = 1
    autocomplete_fields = ["poste"]


@admin.register(GammeType)
class GammeTypeAdmin(ModelAdmin):
    """Gammes types : suites d'opérations (pliage, soudure, traitement…) qu'on ajoute en un clic à la gamme d'une pièce."""

    list_display = ["nom", "description", "nombre_etapes"]
    search_fields = ["nom"]
    inlines = [GammeTypeEtapeInline]

    @admin.display(description="Étapes")
    def nombre_etapes(self, obj):
        return obj.etapes.count()

