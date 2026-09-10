from django.core.management.base import BaseCommand

from comptabilite.postes_gestion import importer_postes_gestion


class Command(BaseCommand):
    help = "Importe (ou met à jour) les postes de gestion (achat/vente) et les comptes qu'ils référencent."

    def handle(self, *args, **options):
        postes_crees, postes_maj, comptes_crees = importer_postes_gestion()
        self.stdout.write(
            self.style.SUCCESS(
                f"Postes de gestion : {postes_crees} créé(s), {postes_maj} mis à jour "
                f"({comptes_crees} compte(s) comptable(s) créé(s) au passage)."
            )
        )
