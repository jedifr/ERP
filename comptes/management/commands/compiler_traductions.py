"""Compile les catalogues de traduction `locale/*/LC_MESSAGES/django.po` en `.mo` (sans gettext). À relancer après toute modification d'un .po."""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from comptes.i18n import compiler_fichier


class Command(BaseCommand):
    help = "Compile les fichiers de traduction .po du dossier locale/ en .mo."

    def handle(self, *args, **options):
        for chemin in sorted(Path(settings.BASE_DIR, "locale").glob("*/LC_MESSAGES/django.po")):
            self.stdout.write(f"{compiler_fichier(chemin)}")
