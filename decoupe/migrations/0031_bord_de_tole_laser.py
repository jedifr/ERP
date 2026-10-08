from django.db import migrations


def regler(apps, schema_editor):
    """Plancher du bord de tôle : jet d'eau 5 mm (valeur par défaut), laser 10 mm."""
    ReglageProcede = apps.get_model("decoupe", "ReglageProcede")
    ReglageProcede.objects.filter(procede="laser").update(bord_tole_minimum_mm=10)


class Migration(migrations.Migration):

    dependencies = [("decoupe", "0030_bord_de_tole")]

    operations = [migrations.RunPython(regler, migrations.RunPython.noop)]
