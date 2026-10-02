from django.apps import AppConfig
from django.db.models.signals import post_migrate

# Rôles métier proposés par défaut pour le module devis/commandes. Créés une
# seule fois (jamais réécrits ensuite) : les permissions d'un groupe existant
# restent entièrement ajustables dans l'admin (Utilisateurs → Groupes).
GROUPES_PAR_DEFAUT = {
    "Commercial": [
        "view_devis", "add_devis", "change_devis",
        "view_devisligne", "add_devisligne", "change_devisligne", "delete_devisligne",
        "view_devisligneoperation",
        "view_commande", "add_commande", "change_commande",
        "view_commandeligne", "add_commandeligne", "change_commandeligne",
        "view_commandelignemodification",
        "view_livraison", "view_livraisonligne",
    ],
    "Responsable commercial": [
        "view_devis", "add_devis", "change_devis", "delete_devis", "valider_devis",
        "view_devisligne", "add_devisligne", "change_devisligne", "delete_devisligne",
        "view_devisligneoperation",
        "view_commande", "add_commande", "change_commande", "annuler_commande",
        "view_commandeligne", "add_commandeligne", "change_commandeligne",
        "view_commandelignemodification",
        "view_livraison", "add_livraison", "change_livraison",
        "view_livraisonligne", "add_livraisonligne", "change_livraisonligne",
    ],
    "Direction": [
        "view_devis", "add_devis", "change_devis", "delete_devis", "valider_devis", "valider_vente_sous_cout",
        "view_devisligne", "add_devisligne", "change_devisligne", "delete_devisligne",
        "view_devisligneoperation",
        "view_commande", "add_commande", "change_commande", "annuler_commande",
        "view_commandeligne", "add_commandeligne", "change_commandeligne",
        "view_commandelignemodification",
        "view_livraison", "add_livraison", "change_livraison",
        "view_livraisonligne", "add_livraisonligne", "change_livraisonligne",
    ],
    "Atelier": [
        "view_commande", "view_commandeligne",
        "view_ordrefabrication", "change_ordrefabrication",
        "view_operationof", "change_operationof",
    ],
}


def creer_groupes_par_defaut(sender, **kwargs):
    from django.contrib.auth.management import create_permissions
    from django.contrib.auth.models import Group, Permission

    # Les permissions d'une app sont créées par un autre handler de post_migrate ;
    # on s'assure qu'elles existent avant de les rattacher aux groupes.
    create_permissions(sender, verbosity=0, using=kwargs.get("using", "default"))
    for nom, codes in GROUPES_PAR_DEFAUT.items():
        groupe, cree = Group.objects.get_or_create(name=nom)
        if cree:
            groupe.permissions.set(
                Permission.objects.filter(content_type__app_label="chiffrage", codename__in=codes)
            )


class ChiffrageConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "chiffrage"
    verbose_name = "Chiffrage et planning"

    def ready(self):
        post_migrate.connect(creer_groupes_par_defaut, sender=self, dispatch_uid="chiffrage_groupes_par_defaut")
