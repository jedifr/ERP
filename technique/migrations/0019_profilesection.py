# Les sections de profilés quittent l'app « decoupe » pour « technique » (forme particulière de matière première) : la table existe déjà
# (decoupe_profilesection) et garde ses données ; seul l'état de Django change ici, la table est renommée à la migration 0020.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("technique", "0018_libelles_unite_cout"),
        ("decoupe", "0033_priorite_formats_et_seuil_chutes"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(database_operations=[], state_operations=[
        migrations.CreateModel(
            name="ProfileSection",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "famille",
                    models.CharField(
                        choices=[
                            ("corniere", "Cornière"),
                            ("upn", "UPN"),
                            ("tube_carre", "Tube carré"),
                            ("tube_rectangulaire", "Tube rectangulaire"),
                            ("tube_rond", "Tube rond"),
                        ],
                        max_length=20,
                        verbose_name="famille",
                    ),
                ),
                (
                    "designation",
                    models.CharField(
                        help_text="« L 50×50×5 », « UPN 100 », « Tube 40×40×3 »…",
                        max_length=60,
                        unique=True,
                        verbose_name="désignation",
                    ),
                ),
                (
                    "dimensions",
                    models.JSONField(
                        default=dict,
                        help_text="Cornière : a, b, e. UPN : h, b, tw, tf. Tube carré : c, e. Tube rectangulaire : h, b, e. Tube rond : d, e.",
                        verbose_name="cotes (mm)",
                    ),
                ),
                (
                    "masse_lineique",
                    models.FloatField(verbose_name="masse linéique (kg/m)"),
                ),
                (
                    "longueur_barre_mm",
                    models.FloatField(
                        default=6000,
                        help_text="Longueur de barre achetée, dont sont tirés les débits.",
                        verbose_name="longueur de barre (mm)",
                    ),
                ),
                (
                    "verifie",
                    models.BooleanField(
                        default=False,
                        help_text="Masses et cotes contrôlées avec le catalogue du fournisseur ou la norme.",
                        verbose_name="vérifié",
                    ),
                ),
                (
                    "source",
                    models.CharField(blank=True, max_length=200, verbose_name="source"),
                ),
                ("ordre", models.PositiveIntegerField(default=0, verbose_name="ordre")),
                (
                    "article",
                    models.ForeignKey(
                        blank=True,
                        help_text="Matière première dont le coût sert au prix des débits : coût au mètre, au kilo (avec la masse linéique) ou à la barre.",
                        limit_choices_to={"nature": "matiere_premiere"},
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="sections_profile",
                        to="technique.article",
                        verbose_name="article d'achat",
                    ),
                ),
            ],
            options={
                "verbose_name": "Section de profilé",
                "verbose_name_plural": "Sections de profilés",
                "ordering": ["famille", "ordre", "designation"],
                "db_table": "decoupe_profilesection",
            },
        ),
        ]),
    ]
