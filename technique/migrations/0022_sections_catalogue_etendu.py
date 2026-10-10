"""Catalogue de sections de profilés étendu à l'usage d'un petit atelier : plats, ronds et carrés pleins, cornières à ailes inégales, IPE, HEA, HEB,
plus de tubes ; masses des tubes carrés et rectangulaires recalculées avec les angles arrondis de la norme (EN 10219). Seules les lignes
**non vérifiées** livrées par l'application sont recalculées ; ce que l'utilisateur a saisi ou vérifié n'est jamais modifié, et une désignation
déjà présente n'est pas recréée."""

from django.db import migrations

from decoupe.profiles_data import lignes

ANCIENNE_SOURCE = "masse calculée, angles vifs"


def etendre(apps, schema_editor):
    Section = apps.get_model("technique", "ProfileSection")
    for famille, designation, cotes, masse, source, ordre in lignes():
        existante = Section.objects.filter(designation=designation).first()
        if existante is None:
            Section.objects.create(designation=designation, famille=famille, dimensions=cotes, masse_lineique=masse, source=source, ordre=ordre, verifie=False)
        elif not existante.verifie and existante.source == ANCIENNE_SOURCE:
            existante.masse_lineique, existante.source = masse, source
            existante.save(update_fields=["masse_lineique", "source"])


class Migration(migrations.Migration):

    dependencies = [("technique", "0021_sections_familles_etendues")]

    operations = [migrations.RunPython(etendre, migrations.RunPython.noop)]
