from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from chiffrage.models import OrdreFabrication
from chiffrage.planning_sync import a_resynchroniser, tenter_synchronisation


class Command(BaseCommand):
    help = (
        "Retente la synchronisation des ordres de fabrication avec le planning atelier : "
        "OF en attente dont le délai de reprise est écoulé, et OF déjà synchronisés mais "
        "modifiés depuis. À exécuter périodiquement (ex. toutes les 15 minutes via le "
        "Planificateur de tâches Synology) — voir docs/DEPLOIEMENT_SYNOLOGY.md."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--inclure-echecs",
            action="store_true",
            help="Retente aussi les OF en échec persistant (compteur remis à zéro).",
        )

    def handle(self, *args, inclure_echecs=False, **options):
        S = OrdreFabrication.StatutSynchro
        maintenant = timezone.now()
        statuts = [S.EN_ATTENTE] + ([S.ECHEC_PERSISTANT] if inclure_echecs else [])
        candidats = list(
            OrdreFabrication.objects.filter(statut_synchro__in=statuts).filter(
                Q(prochaine_tentative__isnull=True) | Q(prochaine_tentative__lte=maintenant)
            )
        )
        # OF déjà synchronisés mais modifiés depuis (quantité, date, gamme…).
        for of in OrdreFabrication.objects.filter(statut_synchro=S.SYNCHRONISE).exclude(empreinte_envoyee=""):
            if a_resynchroniser(of):
                of.statut_synchro = S.EN_ATTENTE
                of.nombre_tentatives = 0
                of.save(update_fields=["statut_synchro", "nombre_tentatives"])
                candidats.append(of)

        if not candidats:
            self.stdout.write("Aucun ordre de fabrication à resynchroniser.")
            return

        reussites = 0
        for of in candidats:
            # Verrou par OF : deux exécutions simultanées ne l'envoient pas deux fois.
            with transaction.atomic():
                verrou = OrdreFabrication.objects.select_for_update(skip_locked=True).filter(pk=of.pk).first()
                if verrou is None:
                    continue
                if inclure_echecs and verrou.statut_synchro == S.ECHEC_PERSISTANT:
                    verrou.nombre_tentatives = 0
                reussites += bool(tenter_synchronisation(verrou))
        self.stdout.write(
            self.style.SUCCESS(f"{reussites}/{len(candidats)} ordre(s) de fabrication synchronisé(s).")
        )
