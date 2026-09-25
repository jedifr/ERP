from django.db import migrations


def backfill(apps, schema_editor):
    PieceDecoupe = apps.get_model("decoupe", "PieceDecoupe")
    PieceDecoupe.objects.filter(rotation_autorisee=True).update(pas_rotation_deg=90)
    PieceDecoupe.objects.filter(rotation_autorisee=False).update(pas_rotation_deg=None)


def backfill_inverse(apps, schema_editor):
    PieceDecoupe = apps.get_model("decoupe", "PieceDecoupe")
    PieceDecoupe.objects.filter(pas_rotation_deg__isnull=False).update(rotation_autorisee=True)
    PieceDecoupe.objects.filter(pas_rotation_deg__isnull=True).update(rotation_autorisee=False)


class Migration(migrations.Migration):
    dependencies = [
        ("decoupe", "0002_profilimportdecoupe_imbricationplacement_miroir_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill, backfill_inverse),
    ]
