from django.db import migrations

from decoupe.cotes_normalisees import lignes


def charger(apps, schema_editor):
    NormeCote = apps.get_model("decoupe", "NormeCote")
    for famille, designation, valeurs, source, ordre in lignes():
        NormeCote.objects.get_or_create(
            famille=famille, designation=designation,
            defaults={"valeurs": valeurs, "source": source, "ordre": ordre, "verifie": False},
        )


class Migration(migrations.Migration):

    dependencies = [("decoupe", "0023_formes_parametriques")]

    operations = [migrations.RunPython(charger, migrations.RunPython.noop)]
