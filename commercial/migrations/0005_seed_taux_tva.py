from django.db import migrations

TAUX_PAR_DEFAUT = [
    ("Taux normal", 20, True),
    ("Taux intermédiaire", 10, False),
    ("Taux réduit", 5.5, False),
    ("Taux particulier", 2.1, False),
]


def creer_taux_par_defaut(apps, schema_editor):
    TauxTVA = apps.get_model("commercial", "TauxTVA")
    for nom, taux, est_defaut in TAUX_PAR_DEFAUT:
        TauxTVA.objects.get_or_create(nom=nom, defaults={"taux": taux, "est_defaut": est_defaut})


def supprimer_taux_par_defaut(apps, schema_editor):
    TauxTVA = apps.get_model("commercial", "TauxTVA")
    TauxTVA.objects.filter(nom__in=[nom for nom, _, _ in TAUX_PAR_DEFAUT]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("commercial", "0004_tauxtva"),
    ]

    operations = [
        migrations.RunPython(creer_taux_par_defaut, supprimer_taux_par_defaut),
    ]
