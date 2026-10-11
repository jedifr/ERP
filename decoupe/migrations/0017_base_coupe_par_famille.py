"""Paramètres de coupe par famille de matière : reprise des paramètres déjà liés à une matière générique (« Acier »,
« Aluminium »…) puis chargement de la base de coupe jet d'eau du logiciel de la machine (materials.lua)."""

import json
import unicodedata
from pathlib import Path

from django.db import migrations


def _norm(texte):
    return "".join(c for c in unicodedata.normalize("NFD", (texte or "").strip().lower()) if unicodedata.category(c) != "Mn")


def reprendre_matieres_generiques(apps, schema_editor):
    """Un paramètre lié à une matière qui porte le nom de sa famille passe au niveau de la famille (sauf doublon)."""
    Parametre = apps.get_model("decoupe", "ParametreCoupe")
    for p in Parametre.objects.filter(matiere__isnull=False, matiere__famille__isnull=False).select_related("matiere__famille"):
        famille = p.matiere.famille
        if _norm(p.matiere.nom) != _norm(famille.nom):
            continue
        if Parametre.objects.filter(procede=p.procede, famille=famille, epaisseur_mm=p.epaisseur_mm).exists():
            continue
        p.famille, p.matiere = famille, None
        p.save(update_fields=["famille", "matiere"])


def charger_base_jet_eau(apps, schema_editor):
    Parametre = apps.get_model("decoupe", "ParametreCoupe")
    Vitesse = apps.get_model("decoupe", "VitesseCoupe")
    Famille = apps.get_model("technique", "FamilleMatiere")
    familles = {f.nom_igems: f for f in Famille.objects.exclude(nom_igems="")}
    chemin = Path(__file__).resolve().parent.parent / "data" / "base_coupe_jet_eau.json"
    for e in json.loads(chemin.read_text(encoding="utf-8")):
        famille = familles.get(e["nom"])
        if famille is None or Parametre.objects.filter(procede="jet_eau", famille=famille, epaisseur_mm=e["epaisseur"]).exists():
            continue
        parametre = Parametre.objects.create(
            procede="jet_eau", famille=famille, epaisseur_mm=e["epaisseur"], usinabilite=e["usinabilite"], origine="calcule",
            percage_stationnaire_hp_s=e["percage_hp_s"], percage_stationnaire_bp_s=e["percage_bp_s"],
            percage_circulaire_hp_tours=e["circ_hp"], percage_circulaire_bp_tours=e["circ_bp"], diametre_percage_mm=e["diametre"],
            temporisation_pointage_s=e["pointage_s"], temporisation_marquage_s=e["marquage_s"],
            vitesse_marquage_mm_min=e["vitesse_marquage"], percement_lineaire_mm=e["linear"], chevauchement_mm=e["overcut"],
            intervalle_pieces_mm=e["intervalle"],
        )
        Vitesse.objects.bulk_create(
            Vitesse(
                parametre=parametre, qualite=v["qualite"], vitesse_haute_mm_min=v["haute"], vitesse_basse_mm_min=v["basse"],
                paliers=q["paliers"], distance_acceleration_mm=round(q["acc"] * q["paliers"], 3),
                distance_deceleration_mm=round(q["dec"] * q["paliers"], 3), coefficient_haut=q["haut"], coefficient_bas=q["bas"],
                facteur_arc=q["arc"],
            )
            for v, q in zip(e["vitesses"], e["qualites"])
        )


class Migration(migrations.Migration):

    dependencies = [("decoupe", "0016_parametre_famille")]

    operations = [
        migrations.RunPython(reprendre_matieres_generiques, migrations.RunPython.noop),
        migrations.RunPython(charger_base_jet_eau, migrations.RunPython.noop),
    ]
