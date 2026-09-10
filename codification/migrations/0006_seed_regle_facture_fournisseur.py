from django.db import migrations


def creer_regle_facture_fournisseur(apps, schema_editor):
    RegleCodification = apps.get_model("codification", "RegleCodification")
    RegleCodification.objects.get_or_create(entite="facture_fournisseur", defaults={"prefixe": "FACF-"})


def supprimer_regle_facture_fournisseur(apps, schema_editor):
    RegleCodification = apps.get_model("codification", "RegleCodification")
    RegleCodification.objects.filter(entite="facture_fournisseur").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("codification", "0005_alter_reglecodification_entite"),
    ]

    operations = [
        migrations.RunPython(creer_regle_facture_fournisseur, supprimer_regle_facture_fournisseur),
    ]
