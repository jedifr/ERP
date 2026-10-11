# Les sections de profilés sont désormais définies dans l'app « technique » (voir technique 0019 et 0020) : état seulement, la table ne bouge pas ici.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("decoupe", "0033_priorite_formats_et_seuil_chutes"),
        ("technique", "0019_profilesection"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(database_operations=[], state_operations=[
        migrations.AlterField(
            model_name="pieceprofile",
            name="section",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="pieces",
                to="technique.profilesection",
                verbose_name="section",
            ),
        ),
        migrations.DeleteModel(
            name="ProfileSection",
        ),
        ]),
    ]
