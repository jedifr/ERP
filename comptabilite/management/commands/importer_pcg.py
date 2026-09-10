from django.core.management.base import BaseCommand

from comptabilite.pcg import PCG_MILLESIME, importer_pcg


class Command(BaseCommand):
    help = f"Importe (ou met à jour) le plan comptable général français, millésime {PCG_MILLESIME}."

    def handle(self, *args, **options):
        crees, maj = importer_pcg()
        self.stdout.write(
            self.style.SUCCESS(f"Plan comptable {PCG_MILLESIME} : {crees} compte(s) créé(s), {maj} mis à jour.")
        )
