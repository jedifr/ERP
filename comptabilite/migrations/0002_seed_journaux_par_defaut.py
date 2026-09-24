from django.db import migrations

# Jeu de journaux standard (mêmes codes/natures que le dictionnaire par
# défaut de Dolibarr). Les journaux de banque/caisse propres à l'entreprise
# (un par compte bancaire, ex. BQ1/BQ2) ne sont volontairement pas
# préremplis : l'admin permet d'en ajouter librement.
JOURNAUX_PAR_DEFAUT = [
    ("AC", "Journal des achats", "achats"),
    ("VT", "Journal des ventes", "ventes"),
    ("BQ1", "Banque", "banque"),
    ("CA", "Caisse", "caisse"),
    ("ER", "Notes de frais", "notes_de_frais"),
    ("OD", "Opérations diverses", "operations_diverses"),
    ("AN", "À nouveaux", "reports_a_nouveau"),
]


def creer_journaux_par_defaut(apps, schema_editor):
    JournalComptable = apps.get_model("comptabilite", "JournalComptable")
    for code, libelle, nature in JOURNAUX_PAR_DEFAUT:
        JournalComptable.objects.get_or_create(code=code, defaults={"libelle": libelle, "nature": nature})


def supprimer_journaux_par_defaut(apps, schema_editor):
    JournalComptable = apps.get_model("comptabilite", "JournalComptable")
    JournalComptable.objects.filter(code__in=[code for code, _, _ in JOURNAUX_PAR_DEFAUT]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("comptabilite", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(creer_journaux_par_defaut, supprimer_journaux_par_defaut),
    ]
