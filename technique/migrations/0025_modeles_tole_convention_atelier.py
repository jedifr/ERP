"""Convention de nommage des tôles de l'atelier : « nuance-épaisseur largeur x longueur » (S235-3 1500x3000). Les règles de création qui avaient
encore un modèle par défaut (ancien « TOLE-… » ou la première version avec largeur et longueur) prennent le nouveau ; une règle dont le modèle
a été personnalisé n'est pas touchée."""

from django.db import migrations, models

ANCIENS_MODELES_REFERENCE = ("TOLE-{matiere}-{epaisseur}", "{matiere} - {largeur} x {longueur} x {epaisseur}")
ANCIENS_MODELES_LIBELLE = ("Tôle {matiere} {epaisseur} mm", "Tôle {matiere} - {largeur} x {longueur} x {epaisseur} mm")


def adopter(apps, schema_editor):
    Regle = apps.get_model("technique", "RegleCreationTole")
    Regle.objects.filter(modele_reference__in=ANCIENS_MODELES_REFERENCE).update(modele_reference="{matiere}-{epaisseur} {largeur}x{longueur}")
    Regle.objects.filter(modele_libelle__in=ANCIENS_MODELES_LIBELLE).update(modele_libelle="Tôle {matiere} {epaisseur} mm - {largeur}x{longueur}")


class Migration(migrations.Migration):

    dependencies = [("technique", "0024_modeles_tole_largeur_longueur")]

    operations = [migrations.RunPython(adopter, migrations.RunPython.noop)]
