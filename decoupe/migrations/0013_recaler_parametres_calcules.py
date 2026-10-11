from django.db import migrations


def recaler(apps, schema_editor):
    """Applique le calage sur temps réels : rayon de pleine vitesse, et recalcul des vitesses des paramètres « calculés »
    (celles relevées sur la machine ne bougent pas)."""
    from decoupe.services.vitesses import vitesses_depuis_usinabilite

    ParametreCoupe = apps.get_model("decoupe", "ParametreCoupe")
    Matiere = apps.get_model("technique", "Matiere")
    ParametreCoupe.objects.all().update(rayon_pleine_vitesse_coef=2.227)
    for parametre in ParametreCoupe.objects.filter(origine="calcule"):
        usinabilite = parametre.usinabilite or Matiere.objects.get(pk=parametre.matiere_id).usinabilite
        if not usinabilite:
            continue
        for vitesse in parametre.vitesses.all():
            calcule = next(
                (c for c in vitesses_depuis_usinabilite(usinabilite, parametre.epaisseur_mm) if abs(c["qualite"] - float(vitesse.qualite)) < 1e-9),
                None,
            )
            if calcule:
                vitesse.vitesse_haute_mm_min = calcule["vitesse_haute_mm_min"]
                vitesse.vitesse_basse_mm_min = calcule["vitesse_basse_mm_min"]
                vitesse.save(update_fields=["vitesse_haute_mm_min", "vitesse_basse_mm_min"])


class Migration(migrations.Migration):
    dependencies = [("decoupe", "0012_facteur_vitesse_courbe"), ("technique", "0008_matiere_usinabilite")]
    operations = [migrations.RunPython(recaler, migrations.RunPython.noop)]
