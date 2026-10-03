from django.core.management.base import BaseCommand

from comptes.securite import CRITIQUE, diagnostics


class Command(BaseCommand):
    help = "Affiche les réglages de sécurité douteux de la configuration (code de sortie 1 si un constat est critique)."

    def handle(self, *args, **options):
        constats = diagnostics()
        if not constats:
            self.stdout.write(self.style.SUCCESS("Sécurité : aucun constat."))
            return
        for constat in constats:
            style = self.style.ERROR if constat.niveau == CRITIQUE else self.style.WARNING
            self.stdout.write(style(f"[{constat.niveau.upper()}] {constat.titre}"))
            self.stdout.write(f"    {constat.conseil}")
        if any(c.niveau == CRITIQUE for c in constats):
            raise SystemExit(1)
