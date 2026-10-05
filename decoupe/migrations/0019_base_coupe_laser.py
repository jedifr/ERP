"""Réglages par procédé (écart minimal entre pièces, pondération des vitesses) et base de coupe laser fibre 6 kW du
constructeur (vitesses de production, consommations, temps de perçage), avec les épaisseurs de 0,5 mm extrapolées."""

import json
from pathlib import Path

from django.db import migrations


def charger(apps, schema_editor):
    Reglage = apps.get_model("decoupe", "ReglageProcede")
    Parametre = apps.get_model("decoupe", "ParametreCoupe")
    Famille = apps.get_model("technique", "FamilleMatiere")
    Reglage.objects.get_or_create(procede="jet_eau", defaults={"coefficient_vitesse": 1, "espacement_minimum_mm": 6})
    Reglage.objects.get_or_create(procede="laser", defaults={"coefficient_vitesse": 0.85, "espacement_minimum_mm": 10})
    familles = {f.nom: f for f in Famille.objects.all()}
    chemin = Path(__file__).resolve().parent.parent / "data" / "base_coupe_laser.json"
    for e in json.loads(chemin.read_text(encoding="utf-8")):
        famille = familles.get(e["famille"])
        if famille is None or Parametre.objects.filter(
            procede="laser", famille=famille, gaz=e["gaz"], epaisseur_mm=e["epaisseur"]
        ).exists():
            continue
        Parametre.objects.create(
            procede="laser", famille=famille, gaz=e["gaz"], epaisseur_mm=e["epaisseur"],
            origine="calcule" if e["interpolee"] else "machine",
            vitesse_coupe_max_m_min=e["vmax"], vitesse_coupe_production_m_min=e["vprod"],
            consommation_gaz_m3_h=e["gaz_m3h"], puissance_kw=e["kw"], remarque=e["remarque"],
            mode_percage="stationnaire_hp", percage_stationnaire_hp_s=e["percage_s"], facteur_percage=1,
            temporisation_pointage_s=0, percement_lineaire_mm=0, chevauchement_mm=0,
            intervalle_pieces_mm=max(10.0, e["epaisseur"]), deplacement_par_contour_s=1.0,
        )


class Migration(migrations.Migration):

    dependencies = [
        ("decoupe", "0018_laser"),
        ("technique", "0012_familles_laser"),
    ]

    operations = [migrations.RunPython(charger, migrations.RunPython.noop)]
