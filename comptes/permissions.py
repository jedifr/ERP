from rest_framework.permissions import BasePermission, DjangoModelPermissions


class ModelPermissionsAvecLecture(DjangoModelPermissions):
    """Comme DjangoModelPermissions (écriture selon add/change/delete), mais la
    lecture exige elle aussi la permission « view » : sans cela, n'importe quel
    compte connecté lisait toutes les données via l'API, que l'admin lui
    interdit."""

    perms_map = {
        **DjangoModelPermissions.perms_map,
        "GET": ["%(app_label)s.view_%(model_name)s"],
        "HEAD": ["%(app_label)s.view_%(model_name)s"],
    }


class PeutVoirLePilotage(BasePermission):
    """Marges réelles et taux de charge : données de pilotage réservées aux comptes
    habilités (permission `chiffrage.voir_marges`) — pas à tout compte connecté."""

    message = "Vous n'avez pas la permission de consulter les données de pilotage."

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.has_perm("chiffrage.voir_marges"))
