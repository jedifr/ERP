from django.db import migrations


def corriger(apps, schema_editor):
    """Au laser, une vitesse n'est jamais « calculée depuis l'usinabilité » : les épaisseurs absentes du tableau du constructeur
    (0,5 mm) sont extrapolées de leurs voisines."""
    ParametreCoupe = apps.get_model("decoupe", "ParametreCoupe")
    ParametreCoupe.objects.filter(procede="laser", origine="calcule").update(origine="extrapole")


class Migration(migrations.Migration):

    dependencies = [("decoupe", "0028_origine_extrapole")]

    operations = [migrations.RunPython(corriger, migrations.RunPython.noop)]
