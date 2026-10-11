from django.db import migrations

from decoupe.profiles_data import lignes


def charger(apps, schema_editor):
    ProfileSection = apps.get_model("decoupe", "ProfileSection")
    for famille, designation, cotes, masse, source, ordre in lignes():
        ProfileSection.objects.get_or_create(
            designation=designation,
            defaults={"famille": famille, "dimensions": cotes, "masse_lineique": masse, "source": source, "ordre": ordre, "verifie": False},
        )


class Migration(migrations.Migration):

    dependencies = [("decoupe", "0026_profiles")]

    operations = [migrations.RunPython(charger, migrations.RunPython.noop)]
