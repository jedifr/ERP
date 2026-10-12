from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.db.models import DecimalField, F, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.http import HttpResponse, HttpResponseRedirect
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import action as unfold_action

from comptes.exports import ExportCsvMixin
from comptes.montants import ZERO
from facturation.models import Facture, RelanceFacture
from facturation.relances import RelanceError, relancer_facture

from . import sepa
from .models import (
    EcheanceClient,
    EcheanceFournisseur,
    LigneLotVirement,
    LotVirement,
    Reglement,
    echeance_fournisseur,
    montant_du,
    reste_a_payer,
)

_ZERO = Value(ZERO, output_field=DecimalField(max_digits=14, decimal_places=2))


def _euros(valeur):
    return "—" if valeur is None else f"{valeur:,.2f} €".replace(",", " ").replace(".", ",")


def _message_validation(exc):
    return " ".join(exc.messages) if hasattr(exc, "messages") else str(exc)


def _solder(admin_obj, request, queryset, champ):
    """Enregistre, pour chaque facture choisie, un règlement du reste à payer à la date du jour (virement)."""
    faits = 0
    for facture in queryset:
        reste = reste_a_payer(facture)
        if not reste:
            admin_obj.message_user(request, f"{facture} : rien à régler.", level=messages.WARNING)
            continue
        reglement = Reglement(**{champ: facture}, montant=reste, utilisateur=request.user)
        try:
            reglement.full_clean()
        except ValidationError as exc:
            admin_obj.message_user(request, f"{facture} : {_message_validation(exc)}", level=messages.ERROR)
            continue
        reglement.save()
        faits += 1
    if faits:
        admin_obj.message_user(request, f"{faits} règlement(s) enregistré(s) à la date du jour.", level=messages.SUCCESS)


class TotauxMixin:
    """Bandeau de totaux au-dessus de la liste (sur les lignes filtrées)."""

    list_before_template = "admin/tresorerie/totaux.html"

    def totaux(self, request, queryset):
        return []

    def changelist_view(self, request, extra_context=None):
        reponse = super().changelist_view(request, extra_context)
        if hasattr(reponse, "context_data") and "cl" in reponse.context_data:
            reponse.context_data["totaux"] = self.totaux(request, reponse.context_data["cl"].queryset)
        return reponse


# --- Règlements ---------------------------------------------------------------------------------------------------


@admin.register(Reglement)
class ReglementAdmin(TotauxMixin, ExportCsvMixin, ModelAdmin):
    list_display = ["date_reglement", "sens", "tiers_display", "document_display", "montant", "mode", "reference", "lot_virement"]
    list_filter = ["sens", "mode", "date_reglement"]
    date_hierarchy = "date_reglement"
    search_fields = [
        "reference", "facture__numero", "facture__commande__client__raison_sociale",
        "facture_fournisseur__numero", "facture_fournisseur__reference_fournisseur",
        "facture_fournisseur__commande_fournisseur__fournisseur__raison_sociale",
    ]
    autocomplete_fields = ["facture", "facture_fournisseur"]
    fields = ["facture", "facture_fournisseur", "date_reglement", "montant", "mode", "reference", "notes",
              "sens", "lot_virement", "utilisateur", "date_saisie"]
    readonly_fields = ["sens", "lot_virement", "utilisateur", "date_saisie"]
    list_select_related = ["facture__commande__client", "facture_fournisseur__commande_fournisseur__fournisseur", "lot_virement"]

    def totaux(self, request, queryset):
        encaisse = queryset.filter(sens=Reglement.Sens.ENCAISSEMENT).aggregate(t=Sum("montant"))["t"] or ZERO
        decaisse = queryset.filter(sens=Reglement.Sens.DECAISSEMENT).aggregate(t=Sum("montant"))["t"] or ZERO
        return [("Encaissé", _euros(encaisse), "ok"), ("Décaissé", _euros(decaisse), ""),
                ("Solde", _euros(encaisse - decaisse), "ok" if encaisse >= decaisse else "ko")]

    @admin.display(description="Tiers")
    def tiers_display(self, obj):
        return obj.tiers or "—"

    @admin.display(description="Facture")
    def document_display(self, obj):
        if obj.facture_id:
            return format_html('<a href="{}">{}</a>', reverse("admin:facturation_facture_change", args=[obj.facture_id]), obj.facture_id)
        if obj.facture_fournisseur_id:
            return format_html(
                '<a href="{}">{}</a>', reverse("admin:achats_facturefournisseur_change", args=[obj.facture_fournisseur_id]),
                obj.facture_fournisseur_id,
            )
        return "—"

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        for champ, modele in (("facture", EcheanceClient), ("facture_fournisseur", EcheanceFournisseur)):
            if champ in initial and "montant" not in initial:
                facture = modele.objects.filter(pk=initial[champ]).first()
                reste = reste_a_payer(facture) if facture else None
                if reste:
                    initial["montant"] = reste
        return initial

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.lot_virement_id:
            return self.fields
        return self.readonly_fields

    def has_delete_permission(self, request, obj=None):
        if obj and obj.lot_virement_id:
            return False
        return super().has_delete_permission(request, obj)

    def save_model(self, request, obj, form, change):
        if not change:
            obj.utilisateur = request.user
        super().save_model(request, obj, form, change)

    def delete_queryset(self, request, queryset):
        # Un par un : chaque suppression recalcule le statut de paiement de sa facture.
        for reglement in queryset:
            if reglement.lot_virement_id:
                self.message_user(request, f"{reglement} vient d'un lot de virements : conservé.", level=messages.WARNING)
                continue
            reglement.delete()


# --- Échéancier clients et relances -------------------------------------------------------------------------------


class EcheanceFilter(admin.SimpleListFilter):
    title = "échéance"
    parameter_name = "echeance"

    def lookups(self, request, model_admin):
        return [("en_retard", "En retard"), ("a_echoir", "À échoir"), ("inconnue", "Échéance inconnue")]

    def queryset(self, request, queryset):
        if not self.value():
            return queryset
        aujourdhui = timezone.localdate()
        garder = []
        for facture in queryset:
            echeance = model_echeance(facture)
            if (self.value() == "inconnue" and echeance is None) or (
                echeance is not None and (echeance < aujourdhui) == (self.value() == "en_retard")
            ):
                garder.append(facture.pk)
        return queryset.filter(pk__in=garder)


def model_echeance(facture):
    return facture.date_echeance if isinstance(facture, Facture) else echeance_fournisseur(facture)


def _retard(echeance):
    if echeance is None:
        return "—"
    jours = (timezone.localdate() - echeance).days
    if jours <= 0:
        return format_html('<span class="text-green-700 dark:text-green-400">dans {} j</span>', -jours) if jours else "aujourd'hui"
    return format_html('<span class="text-red-700 dark:text-red-400 font-semibold">{} j de retard</span>', jours)


@admin.register(EcheanceClient)
class EcheanceClientAdmin(TotauxMixin, ExportCsvMixin, ModelAdmin):
    list_display = ["facture_display", "client_display", "date_facturation", "echeance_display", "retard_display",
                    "montant_ttc_display", "regle_display", "reste_display", "relances_display", "saisir_display"]
    list_display_links = None
    list_filter = [EcheanceFilter, "statut_paiement"]
    search_fields = ["numero", "commande__numero", "commande__client__raison_sociale", "commande__client__code"]
    actions = ["action_relancer", "action_solder"]
    ordering = ["date_facturation", "numero"]

    def get_queryset(self, request):
        return (
            super().get_queryset(request)
            .filter(type_document=Facture.TypeDocument.FACTURE)
            .exclude(statut_paiement=Facture.StatutPaiement.PAYE)
            .select_related("commande__client__conditions_paiement")
            .prefetch_related("relances")
            .annotate(regle=Coalesce(Sum("reglements__montant"), _ZERO))
        )

    def totaux(self, request, queryset):
        aujourdhui = timezone.localdate()
        total = retard = ZERO
        for facture in queryset.prefetch_related("commande__lignes", "lignes"):
            reste = reste_a_payer(facture) or ZERO
            total += reste
            if facture.date_echeance and facture.date_echeance < aujourdhui:
                retard += reste
        return [("Reste à encaisser", _euros(total), ""), ("Dont en retard", _euros(retard), "ko" if retard else "ok")]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="Facture", ordering="numero")
    def facture_display(self, obj):
        return format_html('<a href="{}">{}</a>', reverse("admin:facturation_facture_change", args=[obj.pk]), obj.pk)

    @admin.display(description="Client", ordering="commande__client__raison_sociale")
    def client_display(self, obj):
        return obj.commande.client.raison_sociale

    @admin.display(description="Échéance")
    def echeance_display(self, obj):
        return obj.date_echeance or "—"

    @admin.display(description="Retard")
    def retard_display(self, obj):
        return _retard(obj.date_echeance)

    @admin.display(description="TTC")
    def montant_ttc_display(self, obj):
        return _euros(montant_du(obj))

    @admin.display(description="Réglé", ordering="regle")
    def regle_display(self, obj):
        return _euros(obj.regle) if obj.regle else "—"

    @admin.display(description="Reste à payer")
    def reste_display(self, obj):
        return _euros(reste_a_payer(obj))

    @admin.display(description="Relances")
    def relances_display(self, obj):
        envoyees = [r for r in obj.relances.all() if r.envoyee]
        if not envoyees:
            return "aucune"
        derniere = max(envoyees, key=lambda r: r.date_envoi)
        return f"{len(envoyees)} (niv. {derniere.niveau}, {timezone.localtime(derniere.date_envoi):%d/%m/%Y})"

    @admin.display(description="")
    def saisir_display(self, obj):
        url = reverse("admin:tresorerie_reglement_add") + f"?facture={obj.pk}"
        return format_html('<a class="text-primary-600" href="{}">Saisir un règlement</a>', url)

    @admin.action(description="Envoyer une relance de paiement (niveau suivant)", permissions=["relancer"])
    def action_relancer(self, request, queryset):
        envoyees = 0
        for facture in queryset:
            facture = Facture.objects.get(pk=facture.pk)
            try:
                relance = relancer_facture(facture, utilisateur=request.user)
            except RelanceError as exc:
                self.message_user(request, str(exc), level=messages.ERROR)
                continue
            envoyees += 1
            self.message_user(request, f"{facture} : relance de niveau {relance.niveau} envoyée à {relance.destinataire}.",
                              level=messages.SUCCESS)
        if not envoyees:
            self.message_user(request, "Aucune relance envoyée.", level=messages.WARNING)

    def has_relancer_permission(self, request):
        return request.user.has_perm("facturation.relancer_facture")

    @admin.action(description="Solder : règlement du reste à payer à la date du jour", permissions=["regler"])
    def action_solder(self, request, queryset):
        _solder(self, request, [Facture.objects.get(pk=f.pk) for f in queryset], "facture")

    def has_regler_permission(self, request):
        return request.user.has_perm("tresorerie.add_reglement")


@admin.register(RelanceFacture)
class RelanceFactureAdmin(ModelAdmin):
    """Historique de toutes les relances de paiement envoyées (consultation seule)."""

    list_display = ["date_envoi", "facture_display", "client_display", "niveau_display", "destinataire", "envoyee", "utilisateur"]
    list_filter = ["niveau", "envoyee"]
    search_fields = ["facture__numero", "destinataire", "facture__commande__client__raison_sociale"]
    date_hierarchy = "date_envoi"
    list_select_related = ["facture__commande__client", "utilisateur"]
    readonly_fields = ["facture", "niveau", "destinataire", "objet", "corps", "envoyee", "erreur", "date_envoi", "utilisateur"]

    @admin.display(description="Facture", ordering="facture")
    def facture_display(self, obj):
        return format_html('<a href="{}">{}</a>', reverse("admin:facturation_facture_change", args=[obj.facture_id]), obj.facture_id)

    @admin.display(description="Client")
    def client_display(self, obj):
        return obj.facture.commande.client.raison_sociale

    @admin.display(description="Niveau", ordering="niveau")
    def niveau_display(self, obj):
        return {1: "1 — Rappel", 2: "2 — Relance", 3: "3 — Dernière relance"}.get(obj.niveau, obj.niveau)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


# --- Échéancier fournisseurs et lots de virements -----------------------------------------------------------------


@admin.register(EcheanceFournisseur)
class EcheanceFournisseurAdmin(TotauxMixin, ExportCsvMixin, ModelAdmin):
    list_display = ["facture_display", "fournisseur_display", "reference_fournisseur", "date_facture", "echeance_display",
                    "retard_display", "montant_ttc_display", "regle_display", "reste_display", "iban_display", "lot_display",
                    "saisir_display"]
    list_display_links = None
    list_filter = [EcheanceFilter]
    search_fields = ["numero", "reference_fournisseur", "commande_fournisseur__fournisseur__raison_sociale",
                     "commande_fournisseur__fournisseur__code"]
    actions = ["action_preparer_lot", "action_solder"]
    ordering = ["date_facture", "numero"]

    def get_queryset(self, request):
        return (
            super().get_queryset(request)
            .filter(date_paiement__isnull=True)
            .select_related("commande_fournisseur__fournisseur__conditions_paiement")
            .annotate(regle=Coalesce(Sum("reglements__montant"), _ZERO))
            .filter(Q(montant_ttc__isnull=True) | Q(regle__lt=F("montant_ttc")))
        )

    def totaux(self, request, queryset):
        aujourdhui = timezone.localdate()
        total = retard = ZERO
        for facture in queryset:
            reste = reste_a_payer(facture) or ZERO
            total += reste
            echeance = echeance_fournisseur(facture)
            if echeance and echeance < aujourdhui:
                retard += reste
        return [("Reste à payer", _euros(total), ""), ("Dont échu", _euros(retard), "ko" if retard else "ok")]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="Facture", ordering="numero")
    def facture_display(self, obj):
        return format_html('<a href="{}">{}</a>', reverse("admin:achats_facturefournisseur_change", args=[obj.pk]), obj.pk)

    @admin.display(description="TTC", ordering="montant_ttc")
    def montant_ttc_display(self, obj):
        return _euros(obj.montant_ttc)

    @admin.display(description="Fournisseur", ordering="commande_fournisseur__fournisseur__raison_sociale")
    def fournisseur_display(self, obj):
        return obj.commande_fournisseur.fournisseur.raison_sociale

    @admin.display(description="Échéance")
    def echeance_display(self, obj):
        return echeance_fournisseur(obj) or "—"

    @admin.display(description="Retard")
    def retard_display(self, obj):
        return _retard(echeance_fournisseur(obj))

    @admin.display(description="Réglé", ordering="regle")
    def regle_display(self, obj):
        return _euros(obj.regle) if obj.regle else "—"

    @admin.display(description="Reste à payer")
    def reste_display(self, obj):
        return _euros(reste_a_payer(obj))

    @admin.display(description="IBAN", boolean=True)
    def iban_display(self, obj):
        return bool(obj.commande_fournisseur.fournisseur.iban)

    @admin.display(description="Lot en cours")
    def lot_display(self, obj):
        ligne = obj.lignes_virement.exclude(lot__statut=LotVirement.Statut.EXECUTE).select_related("lot").first()
        if not ligne:
            return "—"
        return format_html('<a href="{}">{}</a>', reverse("admin:tresorerie_lotvirement_change", args=[ligne.lot_id]), ligne.lot)

    @admin.display(description="")
    def saisir_display(self, obj):
        url = reverse("admin:tresorerie_reglement_add") + f"?facture_fournisseur={obj.pk}"
        return format_html('<a class="text-primary-600" href="{}">Saisir un règlement</a>', url)

    @admin.action(description="Préparer un lot de virements avec ces factures", permissions=["virer"])
    def action_preparer_lot(self, request, queryset):
        lot = LotVirement.objects.create(cree_par=request.user)
        for facture in queryset:
            ligne = LigneLotVirement(lot=lot, facture_fournisseur=facture)
            try:
                ligne.full_clean()
            except ValidationError as exc:
                self.message_user(request, f"{facture} : {_message_validation(exc)}", level=messages.WARNING)
                continue
            ligne.save()
        if not lot.lignes.exists():
            lot.delete()
            self.message_user(request, "Aucune facture n'a pu être ajoutée à un lot.", level=messages.ERROR)
            return None
        self.message_user(request, f"{lot} préparé ({lot.lignes.count()} virement(s), {_euros(lot.total)}). "
                                   "Vérifiez la date d'exécution puis générez le fichier XML.", level=messages.SUCCESS)
        return HttpResponseRedirect(reverse("admin:tresorerie_lotvirement_change", args=[lot.pk]))

    def has_virer_permission(self, request):
        return request.user.has_perm("tresorerie.add_lotvirement")

    @admin.action(description="Solder : règlement du reste à payer à la date du jour", permissions=["regler"])
    def action_solder(self, request, queryset):
        from achats.models import FactureFournisseur

        _solder(self, request, [FactureFournisseur.objects.get(pk=f.pk) for f in queryset], "facture_fournisseur")

    def has_regler_permission(self, request):
        return request.user.has_perm("tresorerie.add_reglement")


class LigneLotVirementInline(TabularInline):
    model = LigneLotVirement
    extra = 0
    fields = ["facture_fournisseur", "fournisseur_display", "iban_display", "montant", "libelle"]
    readonly_fields = ["fournisseur_display", "iban_display"]
    autocomplete_fields = ["facture_fournisseur"]

    @admin.display(description="Fournisseur")
    def fournisseur_display(self, obj):
        return obj.fournisseur.raison_sociale if obj and obj.facture_fournisseur_id else "—"

    @admin.display(description="IBAN")
    def iban_display(self, obj):
        return (obj.fournisseur.iban or "manquant") if obj and obj.facture_fournisseur_id else "—"

    def _modifiable(self, obj):
        return obj is None or obj.modifiable

    def has_add_permission(self, request, obj=None):
        return self._modifiable(obj) and super().has_add_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        return self._modifiable(obj) and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return self._modifiable(obj) and super().has_delete_permission(request, obj)


@admin.register(LotVirement)
class LotVirementAdmin(ModelAdmin):
    list_display = ["__str__", "date_execution", "statut", "nombre_display", "total_display", "date_generation", "cree_par"]
    list_filter = ["statut"]
    fields = ["libelle", "date_execution", "statut", "total_display", "message_id", "date_generation", "cree_par", "date_creation"]
    readonly_fields = ["statut", "total_display", "message_id", "date_generation", "cree_par", "date_creation"]
    inlines = [LigneLotVirementInline]
    actions_detail = ["action_generer", "action_telecharger", "action_executer"]

    @admin.display(description="Virements")
    def nombre_display(self, obj):
        return obj.lignes.count()

    @admin.display(description="Total")
    def total_display(self, obj):
        return _euros(obj.total) if obj and obj.pk else "—"

    def get_readonly_fields(self, request, obj=None):
        if obj and not obj.modifiable:
            return self.fields
        return self.readonly_fields

    def has_delete_permission(self, request, obj=None):
        if obj and not obj.modifiable:
            return False
        return super().has_delete_permission(request, obj)

    def save_model(self, request, obj, form, change):
        if not change:
            obj.cree_par = request.user
        elif obj.statut == LotVirement.Statut.GENERE and form.changed_data:
            # Le fichier déjà généré ne reflète plus le lot : à régénérer.
            obj.statut = LotVirement.Statut.BROUILLON
        super().save_model(request, obj, form, change)

    def save_formset(self, request, form, formset, change):
        super().save_formset(request, form, formset, change)
        lot = form.instance
        if lot.statut == LotVirement.Statut.GENERE and formset.has_changed():
            lot.statut = LotVirement.Statut.BROUILLON
            lot.save(update_fields=["statut"])
            self.message_user(request, "Le lot a changé : générez à nouveau le fichier XML.", level=messages.WARNING)

    def _retour(self, object_id):
        return HttpResponseRedirect(reverse("admin:tresorerie_lotvirement_change", args=[object_id]))

    @unfold_action(description="Générer le fichier XML", url_path="generer-xml", permissions=["virer"])
    def action_generer(self, request, object_id):
        lot = LotVirement.objects.get(pk=object_id)
        try:
            xml = sepa.generer_fichier(lot)
        except sepa.VirementError as exc:
            self.message_user(request, str(exc), level=messages.ERROR)
            return self._retour(object_id)
        return self._fichier(lot, xml)

    @unfold_action(description="Télécharger le fichier XML", url_path="telecharger-xml", permissions=["view"])
    def action_telecharger(self, request, object_id):
        lot = LotVirement.objects.get(pk=object_id)
        if not lot.xml:
            self.message_user(request, "Aucun fichier généré pour ce lot.", level=messages.WARNING)
            return self._retour(object_id)
        return self._fichier(lot, lot.xml)

    @unfold_action(description="Marquer comme exécuté par la banque", url_path="executer", permissions=["executer"])
    def action_executer(self, request, object_id):
        lot = LotVirement.objects.get(pk=object_id)
        try:
            sepa.marquer_execute(lot, utilisateur=request.user)
        except sepa.VirementError as exc:
            self.message_user(request, str(exc), level=messages.ERROR)
        else:
            self.message_user(request, f"{lot} exécuté : {lot.lignes.count()} règlement(s) enregistré(s), factures soldées.",
                              level=messages.SUCCESS)
        return self._retour(object_id)

    @staticmethod
    def _fichier(lot, xml):
        reponse = HttpResponse(xml.encode("utf-8"), content_type="application/xml")
        reponse["Content-Disposition"] = f'attachment; filename="{sepa.nom_fichier(lot)}"'
        return reponse

    def has_virer_permission(self, request, obj=None):
        return request.user.has_perm("tresorerie.change_lotvirement")

    def has_executer_permission(self, request, obj=None):
        return request.user.has_perm("tresorerie.executer_lotvirement")
