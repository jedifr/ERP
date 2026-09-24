from django.db import migrations


def creer_regle_livraison(apps, schema_editor):
    RegleCodification = apps.get_model("codification", "RegleCodification")
    RegleCodification.objects.get_or_create(entite="livraison", defaults={"prefixe": "LIV-"})


def supprimer_regle_livraison(apps, schema_editor):
    RegleCodification = apps.get_model("codification", "RegleCodification")
    RegleCodification.objects.filter(entite="livraison").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("codification", "0002_seed_regles_par_defaut"),
    ]

    operations = [
        migrations.RunPython(creer_regle_livraison, supprimer_regle_livraison),
    ]
