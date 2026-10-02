from django.apps import AppConfig
from django.db.models.signals import post_migrate


class ComptesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "comptes"
    verbose_name = "Comptes utilisateurs"

    def ready(self):
        from .groupes import creer_groupes_par_defaut

        post_migrate.connect(creer_groupes_par_defaut, dispatch_uid="comptes_groupes_par_defaut")
