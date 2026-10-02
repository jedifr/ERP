from rest_framework.permissions import DjangoModelPermissions


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
