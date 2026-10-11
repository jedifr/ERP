"""Écrit dans un dossier les trois fichiers ISACOMPTA (ventes, achats, banque) d'un mois : manage.py export_comptable 2026-01 [dossier]."""

import datetime
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from comptabilite import export_comptable


class Command(BaseCommand):
    help = "Exporte vers le comptable (format ISACOMPTA) les écritures de ventes, d'achats et de banque d'un mois."

    def add_arguments(self, parser):
        parser.add_argument("mois", help="AAAA-MM")
        parser.add_argument("dossier", nargs="?", default=".")

    def handle(self, *args, **options):
        try:
            annee, mois = (int(x) for x in options["mois"].split("-"))
            debut, fin = export_comptable.bornes_du_mois(annee, mois)
        except ValueError:
            raise CommandError("Le mois s'écrit AAAA-MM (ex. 2026-01).")
        try:
            resultat = export_comptable.exporter(debut, fin, datetime.date.today())
        except export_comptable.ErreurExport as exc:
            raise CommandError(str(exc))
        dossier = Path(options["dossier"])
        dossier.mkdir(parents=True, exist_ok=True)
        for nom, nombre in resultat["comptes"].items():
            chemin = dossier / export_comptable.nom_fichier(nom, debut)
            chemin.write_bytes(resultat[nom].encode("ascii", errors="replace"))
            self.stdout.write(f"{chemin} : {nombre} écriture(s)")
