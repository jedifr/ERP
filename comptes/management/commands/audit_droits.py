from django.core.management.base import BaseCommand

from comptes.audit_droits import rapport, synthese


class Command(BaseCommand):
    help = "Affiche qui a accès à quoi, les cumuls de droits incompatibles et les comptes à revoir."

    def handle(self, *args, **options):
        lignes = rapport()
        resume = synthese(lignes)
        self.stdout.write(
            f"{resume['comptes_actifs']} compte(s) actif(s) du personnel — {resume['superutilisateurs']} superutilisateur(s), "
            f"{resume['sans_groupe']} sans groupe, {resume['avec_conflits']} cumulant des droits incompatibles."
        )
        for ligne in lignes:
            groupes = ", ".join(ligne["groupes"]) or "aucun"
            self.stdout.write(f"\n{ligne['identifiant']} ({ligne['nom'] or '—'}) — groupes : {groupes}")
            for constat in ligne["constats"]:
                self.stdout.write(self.style.WARNING(f"    ! {constat}"))
            for conflit in ligne["conflits"]:
                self.stdout.write(self.style.ERROR(f"    ⚠ Cumul : {conflit}"))
