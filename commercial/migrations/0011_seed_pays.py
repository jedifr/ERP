from django.db import migrations

# UE-27 (2026) + quelques partenaires hors UE courants pour une entreprise
# française — jeu de départ, l'admin permet d'en ajouter librement.
PAYS_UE = [
    ("FR", "France"), ("DE", "Allemagne"), ("BE", "Belgique"), ("IT", "Italie"),
    ("ES", "Espagne"), ("PT", "Portugal"), ("NL", "Pays-Bas"), ("LU", "Luxembourg"),
    ("IE", "Irlande"), ("AT", "Autriche"), ("PL", "Pologne"), ("CZ", "Tchéquie"),
    ("SK", "Slovaquie"), ("HU", "Hongrie"), ("RO", "Roumanie"), ("BG", "Bulgarie"),
    ("HR", "Croatie"), ("SI", "Slovénie"), ("GR", "Grèce"), ("CY", "Chypre"),
    ("MT", "Malte"), ("DK", "Danemark"), ("SE", "Suède"), ("FI", "Finlande"),
    ("EE", "Estonie"), ("LV", "Lettonie"), ("LT", "Lituanie"),
]
PAYS_HORS_UE = [
    ("GB", "Royaume-Uni"), ("CH", "Suisse"), ("US", "États-Unis"), ("CA", "Canada"),
    ("MA", "Maroc"), ("TN", "Tunisie"), ("CN", "Chine"), ("NO", "Norvège"),
]


def creer_pays_par_defaut(apps, schema_editor):
    Pays = apps.get_model("commercial", "Pays")
    for code, nom in PAYS_UE:
        Pays.objects.get_or_create(code=code, defaults={"nom": nom, "est_ue": True})
    for code, nom in PAYS_HORS_UE:
        Pays.objects.get_or_create(code=code, defaults={"nom": nom, "est_ue": False})


def supprimer_pays_par_defaut(apps, schema_editor):
    Pays = apps.get_model("commercial", "Pays")
    codes = [c for c, _ in PAYS_UE + PAYS_HORS_UE]
    Pays.objects.filter(code__in=codes).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("commercial", "0010_conditionpaiement_pays_remove_contact_telephone_and_more"),
    ]

    operations = [
        migrations.RunPython(creer_pays_par_defaut, supprimer_pays_par_defaut),
    ]
