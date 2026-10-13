from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("comptes", "0009_societe_logo_connexion"),
    ]

    operations = [
        migrations.AlterField(
            model_name="preferencecolonnes",
            name="masquees",
            field=models.JSONField(blank=True, default=None, null=True, verbose_name="colonnes masquées"),
        ),
        migrations.AddField(
            model_name="preferencecolonnes",
            name="largeurs",
            field=models.JSONField(blank=True, default=dict, verbose_name="largeurs des colonnes (px)"),
        ),
    ]
