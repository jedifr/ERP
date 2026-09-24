from django.db import migrations

REGLES_PAR_DEFAUT = [
    ("devis", "DEV-"),
    ("commande", "CDE-"),
    ("ordre_fabrication", "OF-"),
    ("commande_fournisseur", "CDEF-"),
    ("reception", "REC-"),
    ("facture", "FAC-"),
    ("envoi_sous_traitance", "ENVST-"),
    ("retour_sous_traitance", "RETST-"),
    ("tiers", "TIERS-"),
    ("emplacement", "EMP-"),
]


def creer_regles_par_defaut(apps, schema_editor):
    RegleCodification = apps.get_model("codification", "RegleCodification")
    for entite, prefixe in REGLES_PAR_DEFAUT:
        RegleCodification.objects.get_or_create(entite=entite, defaults={"prefixe": prefixe})


def supprimer_regles_par_defaut(apps, schema_editor):
    RegleCodification = apps.get_model("codification", "RegleCodification")
    RegleCodification.objects.filter(entite__in=[entite for entite, _ in REGLES_PAR_DEFAUT]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("codification", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(creer_regles_par_defaut, supprimer_regles_par_defaut),
    ]
