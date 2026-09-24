from django.db import migrations

# Jeu de départ limité aux devises les plus probables pour une entreprise
# française qui commence tout juste à facturer hors zone euro — l'admin
# permet d'en ajouter librement.
DEVISES = [
    ("EUR", "Euro", "€"),
    ("USD", "Dollar américain", "$"),
    ("GBP", "Livre sterling", "£"),
    ("CHF", "Franc suisse", "CHF"),
]


def creer_devises_par_defaut(apps, schema_editor):
    Devise = apps.get_model("commercial", "Devise")
    for code, nom, symbole in DEVISES:
        Devise.objects.get_or_create(code=code, defaults={"nom": nom, "symbole": symbole})


def supprimer_devises_par_defaut(apps, schema_editor):
    Devise = apps.get_model("commercial", "Devise")
    Devise.objects.filter(code__in=[c for c, _, _ in DEVISES]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("commercial", "0012_devise_tiers_bic_tiers_iban_tiers_devise"),
    ]

    operations = [
        migrations.RunPython(creer_devises_par_defaut, supprimer_devises_par_defaut),
    ]
