from django.db import migrations


def renseigner_journal_achats(apps, schema_editor):
    # Même principe que 0004_seed_parametres_comptables côté ventes : seul le
    # journal est préconfiguré ici (le journal "AC" existe depuis
    # 0002_seed_journaux_par_defaut) — les comptes dépendent du plan
    # comptable, importé séparément (ParametresComptables.charger() retombe
    # sur les codes PCG usuels 401/601/44566 s'ils existent, sans qu'il soit
    # nécessaire de les figer ici).
    JournalComptable = apps.get_model("comptabilite", "JournalComptable")
    ParametresComptables = apps.get_model("comptabilite", "ParametresComptables")
    journal_achats = JournalComptable.objects.filter(code="AC").first()
    parametres, _ = ParametresComptables.objects.get_or_create(pk=1)
    if parametres.journal_achats_id is None:
        parametres.journal_achats = journal_achats
        parametres.save()


def retirer_journal_achats(apps, schema_editor):
    ParametresComptables = apps.get_model("comptabilite", "ParametresComptables")
    ParametresComptables.objects.filter(pk=1).update(journal_achats=None)


class Migration(migrations.Migration):

    dependencies = [
        ("comptabilite", "0010_ecriturecomptable_facture_fournisseur_and_more"),
    ]

    operations = [
        migrations.RunPython(renseigner_journal_achats, retirer_journal_achats),
    ]
