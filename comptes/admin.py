from django.contrib import admin, messages
from django.contrib.admin import site
from django.contrib.auth.admin import GroupAdmin as DjangoGroupAdmin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.models import Group, User
from unfold.admin import ModelAdmin
from unfold.forms import UserChangeForm
from unfold.forms import UserCreationForm as UnfoldUserCreationForm

from . import connexions
from .models import EvenementConnexion


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


class UserAdmin(DjangoUserAdmin, ModelAdmin):
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


class GroupAdmin(DjangoGroupAdmin, ModelAdmin):
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
