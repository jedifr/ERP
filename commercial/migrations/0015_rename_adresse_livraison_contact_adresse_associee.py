from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("commercial", "0014_alter_contacttelephone_type_telephone"),
    ]

    operations = [
        migrations.RenameField(
            model_name="contact",
            old_name="adresse_livraison",
            new_name="adresse_associee",
        ),
        migrations.AlterField(
            model_name="contact",
            name="adresse_associee",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "Optionnel : associe ce contact à une adresse précise du tiers — de "
                    "livraison (ex. le contact sur place à un site) ou de facturation (ex. "
                    "le contact comptabilité). Proposé en priorité sur cette adresse, avant "
                    "le contact principal du tiers."
                ),
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="contacts",
                to="commercial.adresse",
                verbose_name="adresse associée",
            ),
        ),
    ]
