from collections import Counter

from django.db import migrations


def reprendre(apps, schema_editor):
    """Le réglage d'une découpe est désormais « par tôle » (temps de mise en place du poste) : les temps de réglage saisis jusqu'ici
    sur les étapes de découpe en cours deviennent le temps de mise en place de leur poste (valeur la plus fréquente, si le poste n'en
    a pas encore), puis ces étapes sont remises à 0. Les étapes closes (historique) ne sont pas touchées."""
    Gamme = apps.get_model("technique", "Gamme")
    PosteTravail = apps.get_model("technique", "PosteTravail")
    en_cours = Gamme.objects.filter(origine="decoupe", date_fin__isnull=True)
    valeurs = {}
    for poste_id, temps in en_cours.filter(temps_fixe__gt=0).values_list("poste_id", "temps_fixe"):
        valeurs.setdefault(poste_id, []).append(temps)
    for poste_id, temps in valeurs.items():
        PosteTravail.objects.filter(pk=poste_id, temps_mise_en_place_min__isnull=True).update(temps_mise_en_place_min=Counter(temps).most_common(1)[0][0])
    en_cours.exclude(temps_fixe=0).update(temps_fixe=0)


class Migration(migrations.Migration):
    dependencies = [("technique", "0015_reglage_par_tole")]
    operations = [migrations.RunPython(reprendre, migrations.RunPython.noop)]
