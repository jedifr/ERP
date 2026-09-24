from django.core.management.base import BaseCommand

from chiffrage.models import OrdreFabrication
from chiffrage.planning_sync import tenter_synchronisation


class Command(BaseCommand):
    help = (
        "Retente la synchronisation des ordres de fabrication en attente avec le "
        "planning atelier. À exécuter périodiquement (ex. toutes les 15 minutes via "
        "le Planificateur de tâches Synology) — voir docs/DEPLOIEMENT_SYNOLOGY.md."
    )

    def handle(self, *args, **options):
        a_retenter = OrdreFabrication.objects.filter(
            statut_synchro=OrdreFabrication.StatutSynchro.EN_ATTENTE
        )
        total = a_retenter.count()
        if total == 0:
            self.stdout.write("Aucun ordre de fabrication à resynchroniser.")
            return

        reussites = sum(1 for of in a_retenter if tenter_synchronisation(of))
        self.stdout.write(
            self.style.SUCCESS(f"{reussites}/{total} ordre(s) de fabrication synchronisé(s).")
        )
