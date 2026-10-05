from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.urls import path, reverse
from django.views.decorators.http import require_http_methods
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import action as unfold_action

from chiffrage.models import Commande, CommandeLigne
from codification.mixins import CodificationInitialeMixin
from codification.models import RegleCodification
from comptabilite.generation import GenerationEcritureError, generer_ecriture_facture

from comptes.colonnes import ColonnesPersonnalisablesMixin
from comptes.exports import ExportCsvMixin
from comptes.pastilles import A_FAIRE, EN_COURS, NEUTRE, TERMINE, PastillesMixin
from comptes.montants import arrondir, pourcent, somme
from comptes.concurrence import VerrouOptimisteMixin
from comptes.historique import HistoriqueLectureSeule

from .models import CHAMPS_FIGES, Facture, FactureLigne, RelanceFacture, facture_verrouillee
from .relances import RelanceError, relancer_facture
from .services import FacturationError, creer_avoir, lignes_a_facturer, preparer_facture


@staff_member_required
@require_http_methods(["GET"])
def montants_calcules_commande_view(request, numero):
    """Total HT/TTC indicatif de la commande `numero`, depuis ses lignes
    actuelles (Facture.montant_ht_calcule/montant_ttc_calcule) — utilisé
    par le JS de la fiche Facture pour pré-remplir montant_ht/montant_ttc
    dès qu'une commande est choisie sur le formulaire d'ajout, sans jamais
    écraser une valeur déjà saisie (voir facture_admin.js)."""
    commande = get_object_or_404(Commande, pk=numero)
    montants = [l.montant_ht for l in commande.lignes.all() if l.montant_ht is not None]
    montants_ttc = [l.montant_ttc for l in commande.lignes.all() if l.montant_ttc is not None]
    return JsonResponse(
        {
            "montant_ht": float(arrondir(somme(montants))) if montants else None,
            "montant_ttc": float(arrondir(somme(montants_ttc))) if montants_ttc else None,
        }
    )


class FactureLigneInline(TabularInline):
    """Lignes de la facture : ce qui est réellement facturé (quantité d'une ligne de
    commande). Figées dès que la facture est émise ou comptabilisée."""

    model = FactureLigne
    extra = 0
    fields = ["commande_ligne", "quantite", "prix_unitaire_ht", "taux_tva", "montant_ht_display", "montant_ttc_display"]
    readonly_fields = ["montant_ht_display", "montant_ttc_display"]

    @admin.display(description="Montant HT")
    def montant_ht_display(self, obj):
        return f"{pourcent(obj.montant_ht)}" if obj and obj.pk else "—"

    @admin.display(description="Montant TTC")
    def montant_ttc_display(self, obj):
        return f"{pourcent(obj.montant_ttc)}" if obj and obj.pk else "—"

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        # Seules les lignes de la commande de la facture sont proposées (une fois la
        # facture enregistrée ; sur l'ajout la commande n'est pas encore connue).
        if db_field.name == "commande_ligne":
            facture = getattr(request, "_facture_en_cours", None)
            if facture is not None:
                kwargs["queryset"] = CommandeLigne.objects.filter(commande_id=facture.commande_id).select_related("article")
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def has_add_permission(self, request, obj=None):
        return not facture_verrouillee(obj) and super().has_add_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        return not facture_verrouillee(obj) and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return not facture_verrouillee(obj) and super().has_delete_permission(request, obj)


class RelanceFactureInline(TabularInline):
    """Historique des relances de la facture : consultation seule."""

    model = RelanceFacture
    extra = 0
    fields = ["date_envoi", "niveau", "destinataire", "objet", "envoyee", "erreur", "utilisateur"]
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


class RetardFilter(admin.SimpleListFilter):
    title = "échéance"
    parameter_name = "retard"

    def lookups(self, request, model_admin):
        return [("en_retard", "En retard de paiement")]

    def queryset(self, request, queryset):
        if self.value() == "en_retard":
            candidates = queryset.exclude(statut_paiement=Facture.StatutPaiement.PAYE).filter(
                type_document=Facture.TypeDocument.FACTURE
            )
            return queryset.filter(pk__in=[f.pk for f in candidates if f.est_en_retard])
        return queryset


@admin.register(Facture)
class FactureAdmin(ColonnesPersonnalisablesMixin, PastillesMixin, ExportCsvMixin, VerrouOptimisteMixin, CodificationInitialeMixin, HistoriqueLectureSeule, ModelAdmin):
    codification_entite = RegleCodification.Entite.FACTURE

    pastilles = {
        "type_document": {"avoir": NEUTRE},
        "statut_paiement": {"a_payer": A_FAIRE, "partiel": EN_COURS, "paye": TERMINE},
    }
    list_display = [
        "numero",
        "commande",
        "date_facturation",
        "date_echeance_display",
        "montant_ht",
        "montant_ttc",
        "ecart_display",
        "relances_display",
        "type_document",
        "statut_paiement",
        "date_paiement",
        "mode_creation",
    ]
    list_filter = ["type_document", "mode_creation", "statut_paiement", RetardFilter]
    search_fields = ["numero", "reference_tiime", "commande__numero"]
    autocomplete_fields = ["commande"]
    actions = ["action_generer_ecriture", "action_relancer"]
    actions_list = ["action_preparer_facture"]
    actions_detail = ["action_creer_avoir"]
    readonly_fields = [
        "montants_calcules_display", "echeance_recap", "ecart_recap", "relances_recap", "avoirs_recap", "ecriture_recap",
    ]
    inlines = [FactureLigneInline, RelanceFactureInline]

    # Fiche en deux colonnes : saisie à gauche, récapitulatif (échéance, écart, relances, avoirs, écriture) à droite
    # — voir comptes/static/comptes/fiche_deux_colonnes.css.
    CHAMPS_RECAPITULATIF = [
        "echeance_recap", "montants_calcules_display", "ecart_recap", "relances_recap", "avoirs_recap", "ecriture_recap",
    ]

    def get_fieldsets(self, request, obj=None):
        saisie = [c for c in self.get_fields(request, obj) if c not in self.CHAMPS_RECAPITULATIF]
        return [
            (None, {"fields": saisie, "classes": ["fiche-saisie"]}),
            ("Récapitulatif", {"fields": self.CHAMPS_RECAPITULATIF, "classes": ["fiche-recap"]}),
        ]

    @admin.display(description="Échéance")
    def echeance_recap(self, obj):
        if obj is None or not obj.pk or not obj.commande_id:
            return "—"
        return self.date_echeance_display(obj)

    @admin.display(description="Écart avec les lignes")
    def ecart_recap(self, obj):
        if obj is None or not obj.pk:
            return "—"
        return self.ecart_display(obj)

    @admin.display(description="Relances de paiement")
    def relances_recap(self, obj):
        if obj is None or not obj.pk:
            return "—"
        return self.relances_display(obj)

    @admin.display(description="Facture d'origine / avoirs")
    def avoirs_recap(self, obj):
        if obj is None or not obj.pk:
            return "—"
        liens = []
        if obj.facture_origine_id:
            liens.append(format_html(
                "Avoir sur <a href='{}'>{}</a>", reverse("admin:facturation_facture_change", args=[obj.facture_origine_id]),
                obj.facture_origine_id,
            ))
        for avoir in obj.avoirs.order_by("date_facturation", "numero"):
            liens.append(format_html(
                "Avoir <a href='{}'>{}</a> ({} €)", reverse("admin:facturation_facture_change", args=[avoir.pk]),
                avoir.numero, pourcent(avoir.montant_ht) if avoir.montant_ht is not None else "—",
            ))
        return mark_safe("<br>".join(liens)) if liens else "—"

    @admin.display(description="Écriture comptable")
    def ecriture_recap(self, obj):
        if obj is None or not obj.pk:
            return "—"
        ecriture = getattr(obj, "ecriture_comptable", None) if hasattr(obj, "ecriture_comptable") else None
        if ecriture is None:
            return "Pas encore générée"
        return format_html("<a href='{}'>{}</a>", reverse("admin:comptabilite_ecriturecomptable_change", args=[ecriture.pk]), ecriture)

    class Media:
        js = ["facturation/facture_admin.js"]

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("relances")

    def get_formsets_with_inlines(self, request, obj=None):
        request._facture_en_cours = obj
        return super().get_formsets_with_inlines(request, obj)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        # Montants laissés vides : repris du total des lignes (jamais écrasés s'ils sont saisis).
        form.instance.remplir_montants_depuis_les_lignes()

    def get_readonly_fields(self, request, obj=None):
        champs = list(super().get_readonly_fields(request, obj))
        if obj is not None and obj.pk:
            champs.append("numero")
        if not request.user.has_perm("facturation.facturer_avant_livraison"):
            champs.append("anticipee")
        if facture_verrouillee(obj):
            # Émise (référence Tiime) ou comptabilisée : seul le paiement reste libre.
            champs += [champ[:-3] if champ.endswith("_id") else champ for champ in CHAMPS_FIGES]
        return champs

    def has_delete_permission(self, request, obj=None):
        return not facture_verrouillee(obj) and super().has_delete_permission(request, obj)

    @admin.display(description="Échéance")
    def date_echeance_display(self, obj):
        echeance = obj.date_echeance
        if echeance is None:
            return "—"
        texte = echeance.strftime("%d/%m/%Y")
        return f"{texte} (en retard)" if obj.est_en_retard else texte

    @admin.display(description="Relances")
    def relances_display(self, obj):
        envoyees = [r for r in obj.relances.all() if r.envoyee]
        if not envoyees:
            return "—"
        derniere = max(envoyees, key=lambda r: r.date_envoi)
        return f"{len(envoyees)} (dernière le {derniere.date_envoi:%d/%m/%Y})"

    @admin.action(description="Envoyer une relance de paiement", permissions=["relancer"])
    def action_relancer(self, request, queryset):
        envoyees = 0
        for facture in queryset:
            try:
                relance = relancer_facture(facture, utilisateur=request.user)
            except RelanceError as exc:
                self.message_user(request, str(exc), level=messages.ERROR)
                continue
            envoyees += 1
            self.message_user(
                request, f"{facture} : relance de niveau {relance.niveau} envoyée à {relance.destinataire}.",
                level=messages.SUCCESS,
            )
        if not envoyees:
            self.message_user(request, "Aucune relance envoyée.", level=messages.WARNING)

    def has_relancer_permission(self, request):
        return request.user.has_perm("facturation.relancer_facture")

    @admin.display(description="Écart / lignes")
    def ecart_display(self, obj):
        ecart = obj.ecart_avec_les_lignes
        if ecart is None:
            return "—"
        return "0" if ecart == 0 else f"{ecart:+} €"

    @admin.display(description="Montants calculés depuis la commande (indicatif)")
    def montants_calcules_display(self, obj):
        if obj is None or obj.commande_id is None:
            return "—"
        ht = obj.montant_ht_calcule
        if ht is None:
            return "—"
        return f"HT : {pourcent(ht)} € — TTC : {pourcent(obj.montant_ttc_calcule)} €"

    @admin.action(description="Générer l'écriture comptable", permissions=["ecrire"])
    def action_generer_ecriture(self, request, queryset):
        creees = existantes = 0
        for facture in queryset:
            try:
                _, creee = generer_ecriture_facture(facture)
            except GenerationEcritureError as exc:
                self.message_user(request, f"{facture} : {exc}", level=messages.ERROR)
                continue
            creees += creee
            existantes += not creee
        if creees:
            self.message_user(request, f"{creees} écriture(s) comptable(s) générée(s).", level=messages.SUCCESS)
        if existantes:
            self.message_user(
                request, f"{existantes} facture(s) avaient déjà leur écriture.", level=messages.INFO
            )

    def get_urls(self):
        urls = [
            path(
                "<str:numero>/montants-calcules/",
                self.admin_site.admin_view(montants_calcules_commande_view),
                name="facturation_facture_montants_calcules",
            ),
            path("preparer/", self.admin_site.admin_view(self.preparer_view), name="facturation_facture_preparer"),
            path("<str:numero>/avoir/", self.admin_site.admin_view(self.avoir_view), name="facturation_facture_avoir"),
        ]
        return urls + super().get_urls()

    @unfold_action(description="Préparer une facture (livré non facturé)", url_path="preparer-facture")
    def action_preparer_facture(self, request):
        return HttpResponseRedirect(reverse("admin:facturation_facture_preparer"))

    def preparer_view(self, request):
        retour = reverse("admin:facturation_facture_changelist")
        if not request.user.has_perm("facturation.add_facture"):
            self.message_user(request, "Vous n'avez pas la permission de créer une facture.", level=messages.ERROR)
            return HttpResponseRedirect(retour)
        if request.method == "POST":
            commande = get_object_or_404(Commande, pk=request.POST.get("commande"))
            anticipee = bool(request.POST.get("anticipee")) and request.user.has_perm(
                "facturation.facturer_avant_livraison"
            )
            try:
                facture = preparer_facture(commande, anticipee=anticipee)
            except FacturationError as exc:
                self.message_user(request, str(exc), level=messages.ERROR)
                return HttpResponseRedirect(reverse("admin:facturation_facture_preparer"))
            self.message_user(
                request,
                f"Facture {facture} préparée ({pourcent(facture.montant_ht)} € HT). "
                "Renseignez la référence Tiime une fois émise.",
                level=messages.SUCCESS,
            )
            return HttpResponseRedirect(reverse("admin:facturation_facture_change", args=[facture.pk]))
        candidats = []
        for commande in Commande.objects.exclude(statut=Commande.Statut.ANNULEE).select_related("client"):
            lignes = lignes_a_facturer(commande)
            if lignes:
                candidats.append({"commande": commande, "lignes": lignes})
        demandee = request.GET.get("commande", "")
        seule = [c for c in candidats if c["commande"].pk == demandee]
        return TemplateResponse(
            request,
            "admin/facturation/preparer_facture.html",
            {
                **self.admin_site.each_context(request),
                "title": "Préparer une facture",
                "candidats": seule or candidats,
                "commande_filtree": bool(seule),
                "liste_complete_url": reverse("admin:facturation_facture_preparer"),
                "peut_anticiper": request.user.has_perm("facturation.facturer_avant_livraison"),
                "retour": retour,
            },
        )

    @unfold_action(description="Créer un avoir", permissions=["creer_avoir"], url_path="creer-avoir")
    def action_creer_avoir(self, request, object_id):
        return HttpResponseRedirect(reverse("admin:facturation_facture_avoir", args=[object_id]))

    def has_ecrire_permission(self, request):
        return request.user.has_perm("comptabilite.add_ecriturecomptable")

    def has_creer_avoir_permission(self, request, obj=None):
        return request.user.has_perm("facturation.creer_avoir")

    def avoir_view(self, request, numero):
        facture = get_object_or_404(Facture, pk=numero)
        retour = reverse("admin:facturation_facture_change", args=[facture.pk])
        if not request.user.has_perm("facturation.creer_avoir"):
            self.message_user(request, "Vous n'avez pas la permission de créer un avoir.", level=messages.ERROR)
            return HttpResponseRedirect(retour)
        if request.method == "POST":
            try:
                avoir = creer_avoir(facture, request.POST.get("motif", ""), utilisateur=request.user)
            except FacturationError as exc:
                self.message_user(request, str(exc), level=messages.ERROR)
                return HttpResponseRedirect(retour)
            self.message_user(
                request,
                f"Avoir {avoir} créé ({pourcent(avoir.montant_ht)} € HT). Émettez-le dans Tiime puis renseignez sa référence.",
                level=messages.SUCCESS,
            )
            return HttpResponseRedirect(reverse("admin:facturation_facture_change", args=[avoir.pk]))
        return TemplateResponse(
            request,
            "admin/confirmer_avec_motif.html",
            {
                **self.admin_site.each_context(request),
                "title": f"Créer un avoir sur {facture}",
                "explication": (
                    f"Un avoir sera créé pour ce qui reste à créditer sur la facture {facture} "
                    "(brouillon à émettre dans Tiime). Pour un avoir partiel, créez-le à la main."
                ),
                "avertissement": "",
                "bouton": "Créer l'avoir",
                "retour": retour,
            },
        )
