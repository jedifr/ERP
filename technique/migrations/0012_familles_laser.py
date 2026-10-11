"""Familles propres au laser (aciers galvanisés) et gaz de coupe usuel de chaque famille."""

from django.db import migrations

# nom -> gaz laser usuel
GAZ = {"Acier": "O2", "Acier galvanisé": "N2", "Acier électrozingué": "N2", "Inox": "N2", "Aluminium": "N2", "Laiton": "N2", "Cuivre": "O2"}

NOUVELLES = [
    (8, "Acier galvanisé", 87.6, "galva, galvanise, dx51, sendzimir, z275"),
    (9, "Acier électrozingué", 87.6, "electrozingue, electrozinc, elektro, zintec"),
]


def creer(apps, schema_editor):
    Famille = apps.get_model("technique", "FamilleMatiere")
    for ordre, nom, usinabilite, mots in NOUVELLES:
        Famille.objects.get_or_create(nom=nom, defaults={"ordre": ordre, "usinabilite": usinabilite, "mots_cles": mots})
    for nom, gaz in GAZ.items():
        Famille.objects.filter(nom=nom, gaz_laser_prefere="").update(gaz_laser_prefere=gaz)


class Migration(migrations.Migration):

    dependencies = [("technique", "0011_gaz_laser_famille")]

    operations = [migrations.RunPython(creer, migrations.RunPython.noop)]
