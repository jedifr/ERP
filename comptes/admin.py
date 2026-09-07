from django.contrib.admin import site
from django.contrib.auth.admin import GroupAdmin as DjangoGroupAdmin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.models import Group, User

from unfold.admin import ModelAdmin
from unfold.forms import UserChangeForm
from unfold.forms import UserCreationForm as UnfoldUserCreationForm


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
