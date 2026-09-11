from django.db import migrations

NOM = "Taux zéro"


def creer_taux_zero(apps, schema_editor):
    TauxTVA = apps.get_model("commercial", "TauxTVA")
    TauxTVA.objects.get_or_create(nom=NOM, defaults={"taux": 0, "est_defaut": False})


def supprimer_taux_zero(apps, schema_editor):
    TauxTVA = apps.get_model("commercial", "TauxTVA")
    TauxTVA.objects.filter(nom=NOM).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("commercial", "0017_alter_adresse_options_remove_adresse_type_adresse_and_more"),
    ]

    operations = [
        migrations.RunPython(creer_taux_zero, supprimer_taux_zero),
    ]
