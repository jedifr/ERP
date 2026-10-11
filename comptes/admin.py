from django.contrib import admin, messages
from django.contrib.admin import site
from django.contrib.auth.admin import GroupAdmin as DjangoGroupAdmin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.models import Group, User
from unfold.admin import ModelAdmin
from unfold.forms import UserChangeForm
from unfold.forms import UserCreationForm as UnfoldUserCreationForm

from django.template.response import TemplateResponse
from django.urls import path

from . import audit_droits, connexions
from .models import EvenementConnexion, LotModification, Societe


class UserCreationForm(UnfoldUserCreationForm):
    """Formulaire d'ajout épuré : identifiant, nom, mot de passe, accès admin.

    Le formulaire par défaut de Django (matrice de permissions, groupes,
    authentification sans mot de passe pour SSO/LDAP...) n'a pas de sens pour
    cet ERP interne : un seul mode d'authentification (mot de passe), et un
    seul réglage utile à la création (l'accès à cette interface).
    """

    class Meta(UnfoldUserCreationForm.Meta):
        model = User
        fields = ("username", "first_name", "last_name")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["first_name"].label = "Prénom"
        self.fields["first_name"].required = False
        self.fields["last_name"].label = "Nom"
        self.fields["last_name"].required = False


class ReserveAuxSuperutilisateurs:
    """Utilisateurs et groupes : réservés aux superutilisateurs. Avec la seule permission
    « modifier les utilisateurs », un compte pouvait se cocher « superutilisateur » ou
    s'ajouter à n'importe quel groupe : élévation de privilèges. Qui peut gérer les accès
    a, de fait, tous les accès."""

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


class UserAdmin(ReserveAuxSuperutilisateurs, DjangoUserAdmin, ModelAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    add_fieldsets = (
        (
            None,
            {
                "fields": (
                    "username",
                    "first_name",
                    "last_name",
                    "password1",
                    "password2",
                    "is_staff",
                ),
            },
        ),
    )
    list_display = ("username", "first_name", "last_name", "is_staff", "is_active")

    def get_urls(self):
        urls = [path("audit-droits/", self.admin_site.admin_view(self.audit_droits_view), name="auth_user_audit_droits")]
        return urls + super().get_urls()

    def audit_droits_view(self, request):
        if not request.user.is_superuser:
            from django.core.exceptions import PermissionDenied

            raise PermissionDenied
        lignes = audit_droits.rapport()
        return TemplateResponse(
            request,
            "admin/comptes/audit_droits.html",
            {**self.admin_site.each_context(request), "title": "Audit des droits", "lignes": lignes,
             "resume": audit_droits.synthese(lignes)},
        )


class GroupAdmin(ReserveAuxSuperutilisateurs, DjangoGroupAdmin, ModelAdmin):
    pass


site.unregister(User)
site.register(User, UserAdmin)
site.unregister(Group)
site.register(Group, GroupAdmin)


@admin.register(EvenementConnexion)
class EvenementConnexionAdmin(ModelAdmin):
    """Journal des connexions : consultation seule (c'est une piste d'audit)."""

    list_display = ["date", "identifiant", "type_evenement", "adresse_ip", "verrouille_display"]
    list_filter = ["type_evenement"]
    search_fields = ["identifiant", "adresse_ip"]
    date_hierarchy = "date"
    actions = ["action_debloquer"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="Verrouillé maintenant")
    def verrouille_display(self, obj):
        # Du texte plutôt qu'une icône booléenne : vert = « oui » serait lu comme « tout va bien ».
        return "VERROUILLÉ" if connexions.compte_verrouille(obj.identifiant) else "—"

    @admin.action(description="Débloquer les comptes sélectionnés", permissions=["debloquer"])
    def action_debloquer(self, request, queryset):
        identifiants = sorted(set(queryset.values_list("identifiant", flat=True)))
        for identifiant in identifiants:
            connexions.enregistrer(identifiant, connexions.Type.DEBLOCAGE, request)
        self.message_user(request, f"{len(identifiants)} compte(s) débloqué(s).", level=messages.SUCCESS)

    def has_debloquer_permission(self, request):
        return request.user.has_perm("comptes.debloquer_compte")


@admin.register(Societe)
class SocieteAdmin(ModelAdmin):
    """Fiche unique : identité de l'entreprise pour les documents PDF."""

    def has_add_permission(self, request):
        return not Societe.objects.exists() and super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        # Une seule fiche : on y va directement.
        from django.shortcuts import redirect

        if request.method == "GET" and "_changelist_filters" not in request.GET:
            fiche = Societe.charger()
            from django.urls import reverse

            return redirect(reverse("admin:comptes_societe_change", args=[fiche.pk]))
        return super().changelist_view(request, extra_context)


@admin.register(LotModification)
class LotModificationAdmin(ModelAdmin):
    """Journal des modifications et créations par lots : consultation, et annulation d'un lot tant que les valeurs n'ont pas changé depuis."""

    list_display = ["date", "utilisateur", "description", "modele", "nombre", "etat"]
    list_filter = ["modele"]
    search_fields = ["description"]
    date_hierarchy = "date"
    actions = ["action_annuler"]
    readonly_fields = ["utilisateur", "date", "modele", "description", "modifications", "crees", "annule_le"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="État")
    def etat(self, obj):
        return f"annulé le {obj.annule_le:%d/%m/%Y %H:%M}" if obj.annule_le else "appliqué"

    @admin.action(description="Annuler les lots sélectionnés", permissions=["annuler"])
    def action_annuler(self, request, queryset):
        from . import lots

        for lot in queryset.order_by("-date"):  # le plus récent d'abord : un lot récent peut dépendre d'un plus ancien
            faits, refus = lots.annuler(lot)
            niveau = messages.SUCCESS if faits and not refus else messages.WARNING
            self.message_user(request, f"« {lot.description} » : {faits} objet(s) rétabli(s)." + (" Non rétablis : " + " ; ".join(refus[:5]) if refus else ""), level=niveau)

    def has_annuler_permission(self, request):
        return request.user.has_perm("comptes.change_lotmodification")
