"""Les sections de profilés vivent dans l'app « technique » : la table est renommée (technique_profilesection) et le type de contenu
d'administration suit, de sorte que les droits déjà donnés aux groupes (view/add/change/delete profilesection) restent valables."""

from django.db import migrations


def deplacer_type_contenu(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    ancien = ContentType.objects.filter(app_label="decoupe", model="profilesection")
    if ContentType.objects.filter(app_label="technique", model="profilesection").exists():
        ancien.delete()  # déjà recréé : on ne garde qu'un type de contenu
    else:
        ancien.update(app_label="technique")


def remettre_type_contenu(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    ContentType.objects.filter(app_label="technique", model="profilesection").update(app_label="decoupe")


class Migration(migrations.Migration):

    dependencies = [
        ("technique", "0019_profilesection"),
        ("decoupe", "0034_alter_pieceprofile_section_delete_profilesection"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        migrations.AlterModelTable(name="profilesection", table=None),
        migrations.RunPython(deplacer_type_contenu, remettre_type_contenu),
    ]
