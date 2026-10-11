from django.db import migrations

FORMATS = [(3000, 1500), (2500, 1250), (2000, 1000), (4000, 2000), (6000, 2000), (3000, 1250)]  # (longueur, largeur)


def creer(apps, schema_editor):
    FormatTole = apps.get_model("decoupe", "FormatTole")
    for longueur, largeur in FORMATS:
        FormatTole.objects.get_or_create(largeur_mm=largeur, longueur_mm=longueur, defaults={"libelle": f"{longueur} × {largeur}"})


class Migration(migrations.Migration):
    dependencies = [("decoupe", "0014_imbrication_chiffrage")]
    operations = [migrations.RunPython(creer, migrations.RunPython.noop)]
