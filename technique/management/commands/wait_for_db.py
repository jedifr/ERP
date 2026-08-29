import time

from django.core.management.base import BaseCommand
from django.db import connections
from django.db.utils import OperationalError


class Command(BaseCommand):
    """Attend que la base de données soit disponible avant de continuer.

    Utile au démarrage du conteneur : le conteneur PostgreSQL peut mettre
    quelques secondes à accepter les connexions après son propre démarrage.
    """

    help = "Attend que la base de données réponde"

    def add_arguments(self, parser):
        parser.add_argument("--timeout", type=int, default=30, help="Délai maximum en secondes")

    def handle(self, *args, **options):
        self.stdout.write("Attente de la base de données...")
        deadline = time.monotonic() + options["timeout"]
        while True:
            try:
                connections["default"].cursor()
            except OperationalError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(1)
            else:
                break
        self.stdout.write(self.style.SUCCESS("Base de données disponible."))
