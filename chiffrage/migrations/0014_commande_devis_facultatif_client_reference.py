import django.db.models.deletion
from django.db import migrations, models


def reporter_client_depuis_devis(apps, schema_editor):
    Commande = apps.get_model("chiffrage", "Commande")
    # Toutes les commandes existantes ont, à ce stade, un devis obligatoire :
    # le client se déduit donc entièrement de devis.client, sans perte.
    for commande in Commande.objects.select_related("devis").all():
        commande.client_id = commande.devis.client_id
        commande.save(update_fields=["client"])


def vider_client(apps, schema_editor):
    Commande = apps.get_model("chiffrage", "Commande")
    Commande.objects.update(client=None)


class Migration(migrations.Migration):

    dependencies = [
        ("commercial", "0017_alter_adresse_options_remove_adresse_type_adresse_and_more"),
        ("chiffrage", "0013_alter_devisligne_options_devisligne_ordre"),
    ]

    operations = [
        migrations.AlterField(
            model_name="commande",
            name="devis",
            field=models.ForeignKey(
                blank=True,
                help_text="Optionnel : une commande peut être créée directement, sans devis d'origine.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="commandes",
                to="chiffrage.devis",
                verbose_name="devis",
            ),
        ),
        migrations.AddField(
            model_name="commande",
            name="client",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="commandes",
                to="commercial.tiers",
                verbose_name="client",
                help_text="Pré-rempli depuis le devis s'il y en a un, modifiable ensuite.",
            ),
        ),
        migrations.RunPython(reporter_client_depuis_devis, vider_client),
        migrations.AlterField(
            model_name="commande",
            name="client",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="commandes",
                to="commercial.tiers",
                verbose_name="client",
                help_text="Pré-rempli depuis le devis s'il y en a un, modifiable ensuite.",
            ),
        ),
        migrations.AddField(
            model_name="commande",
            name="reference_client",
            field=models.CharField(
                default="",
                help_text=(
                    "Référence donnée par le client à sa propre commande "
                    "(numéro de bon de commande, etc.)."
                ),
                max_length=100,
                verbose_name="réf. commande client",
            ),
        ),
    ]
