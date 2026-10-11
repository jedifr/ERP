from django.core.management.base import BaseCommand

from comptes.groupes import synchroniser_groupes


class Command(BaseCommand):
    help = (
        "Ajoute aux groupes métier par défaut les permissions qui leur manquent "
        "(ne retire jamais rien) — utile après une mise à jour qui étend ces rôles."
    )

    def handle(self, *args, **options):
        for nom, nombre in synchroniser_groupes().items():
            self.stdout.write(f"{nom} : {nombre} permission(s) ajoutée(s)")
