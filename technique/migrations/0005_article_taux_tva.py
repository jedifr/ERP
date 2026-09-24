import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("commercial", "0018_seed_taux_tva_zero"),
        ("technique", "0004_alter_article_nature"),
    ]

    operations = [
        migrations.AddField(
            model_name="article",
            name="taux_tva",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "Taux normal appliqué à un client soumis à la TVA française. Un client "
                    "exonéré, intracommunautaire ou hors UE applique automatiquement 0 %, quel "
                    "que soit ce taux (voir chiffrage.moteur.resoudre_taux_tva)."
                ),
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="articles",
                to="commercial.tauxtva",
                verbose_name="taux de TVA (régime France)",
            ),
        ),
    ]
