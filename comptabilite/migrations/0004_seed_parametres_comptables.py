from django.db import migrations


def creer_parametres_par_defaut(apps, schema_editor):
    # Seul journal_ventes est préconfiguré ici : les comptes (client, vente,
    # TVA collectée) dépendent du plan comptable, importé séparément (voir
    # comptabilite.pcg) — ParametresComptables.charger() retombe sur les
    # codes PCG usuels (411/706/44571) s'ils existent, sans qu'il soit
    # nécessaire de le figer ici.
    JournalComptable = apps.get_model("comptabilite", "JournalComptable")
    ParametresComptables = apps.get_model("comptabilite", "ParametresComptables")
    journal_ventes = JournalComptable.objects.filter(code="VT").first()
    ParametresComptables.objects.get_or_create(pk=1, defaults={"journal_ventes": journal_ventes})


def supprimer_parametres_par_defaut(apps, schema_editor):
    ParametresComptables = apps.get_model("comptabilite", "ParametresComptables")
    ParametresComptables.objects.filter(pk=1).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("comptabilite", "0003_ecriturecomptable_ligneecriture_parametrescomptables"),
    ]

    operations = [
        migrations.RunPython(creer_parametres_par_defaut, supprimer_parametres_par_defaut),
    ]
