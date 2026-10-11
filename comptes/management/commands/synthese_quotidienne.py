from django.core.management.base import BaseCommand, CommandError

from comptes import synthese


class Command(BaseCommand):
    help = (
        "Envoie la synthèse quotidienne (factures en retard, devis à relancer, alertes de stock, ordres de "
        "fabrication non transmis). À planifier chaque matin (Planificateur de tâches DSM)."
    )

    def add_arguments(self, parser):
        parser.add_argument("--afficher", action="store_true", help="Affiche la synthèse sans l'envoyer.")
        parser.add_argument("--destinataires", help="Adresses séparées par des virgules (sinon réglage ou groupe Direction).")
        parser.add_argument("--si-non-vide", action="store_true", help="N'envoie rien s'il n'y a rien à signaler.")

    def handle(self, *args, **options):
        sections = synthese.construire()
        if options["afficher"]:
            self.stdout.write(synthese.composer(sections) if sections else "Rien à signaler.")
            return
        if not sections and options["si_non_vide"]:
            self.stdout.write("Rien à signaler : aucun message envoyé.")
            return
        a = [e.strip() for e in (options["destinataires"] or "").split(",") if e.strip()] or synthese.destinataires()
        if not a:
            raise CommandError(
                "Aucun destinataire : renseignez DJANGO_SYNTHESE_DESTINATAIRES ou une adresse e-mail "
                "sur les utilisateurs du groupe Direction."
            )
        synthese.envoyer(sections, a)
        self.stdout.write(self.style.SUCCESS(f"Synthèse envoyée à {', '.join(a)} ({len(sections)} point(s))."))
