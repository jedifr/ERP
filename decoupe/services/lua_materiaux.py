"""Import du fichier `materials.lua` du logiciel de la machine (IGEMS) : une fiche de paramètres de coupe par matière et
épaisseur (jet d'eau), avec l'usinabilité, le perçage, le marquage, le percement, le chevauchement, l'intervalle entre
pièces, les paliers et les coefficients de chaque niveau de qualité.

Le fichier ne contient pas les vitesses du jet d'eau : le logiciel les calcule à partir de l'usinabilité et de
l'épaisseur. Les vitesses importées sont donc calculées par notre modèle (voir vitesses.py), calé sur des temps réels,
et marquées « calculées »."""

import re

from django.db import transaction

from decoupe.models import ParametreCoupe, VitesseCoupe
from technique.models import Matiere

from .vitesses import QUALITES, RAYON_PLEINE_VITESSE_MM, vitesses_depuis_usinabilite

NOMS_FRANCAIS = {
    "Steel": "Acier", "Stainless Steel": "Inox", "Aluminium": "Aluminium", "Copper": "Cuivre", "Brass": "Laiton",
    "Titanium": "Titane", "Glas": "Verre", "Granite": "Granit", "Marble": "Marbre", "Grafite": "Graphite",
    "Nilo": "Nylon", "Plexiglas": "Plexiglas",
}

_BLOC = re.compile(r"\n\{\s*\nname=")
_CHAMP = re.compile(r'^([a-z_0-9]+)=("(?:[^"\\]|\\.)*"|[-0-9.eE+]+)', re.M)


class ErreurLua(Exception):
    pass


def lire_materials_lua(texte):
    """Entrées jet d'eau du fichier : [{nom, epaisseur, usinabilite, densite (kg/dm³), …}] (seulement les champs utiles)."""
    if "materials" not in texte[:200]:
        raise ErreurLua("Ce fichier ne ressemble pas à un materials.lua (la table « materials » est introuvable).")
    entrees = []
    for bloc in _BLOC.split("\n" + texte)[1:]:
        champs = {}
        for m in _CHAMP.finditer("name=" + bloc):
            valeur = m.group(2)
            champs[m.group(1)] = valeur.strip('"') if valeur.startswith('"') else float(valeur)
        if not champs.get("awj") or not champs.get("thick") or not champs.get("machinability"):
            continue
        qualites = []
        for i in range(5):
            qualites.append({
                "paliers": max(1, int(champs.get(f"steps{i}", 1))),
                "acc": champs.get(f"acc_dist{i}", 0.0), "dec": champs.get(f"dec_dist{i}", 0.0),
                "haut": champs.get(f"high_fac{i}", 1.0), "bas": champs.get(f"low_fac{i}", 1.0), "arc": champs.get(f"arc_fac{i}", 1.0),
            })
        entrees.append({
            "nom": champs["name"], "epaisseur": champs["thick"], "usinabilite": champs["machinability"],
            "densite": round(champs.get("density", 0) / 1000, 3),
            "percage_hp_s": champs.get("stat_pierce0", 0), "percage_bp_s": champs.get("stat_pierce_low", 0),
            "circ_hp": champs.get("circ_pierce", 0), "circ_bp": champs.get("circ_pierce_low", 0),
            "diametre": champs.get("pierce_diam", 1.4), "pointage_s": champs.get("point_delay0", 0),
            "marquage_s": champs.get("mark_delay0", 0), "vitesse_marquage": champs.get("mark_feed0", 4000),
            "linear": champs.get("linear_dist2", 0), "overcut": champs.get("overcut_dist2", 0),
            "intervalle": champs.get("part_distance", 4), "qualites": qualites,
        })
    if not entrees:
        raise ErreurLua("Aucune matière de jet d'eau trouvée dans ce fichier.")
    return entrees


def resume(entrees):
    """[(nom Lua, nombre d'épaisseurs, usinabilité, épaisseur min, épaisseur max)] pour l'écran de contrôle."""
    par_nom = {}
    for e in entrees:
        par_nom.setdefault(e["nom"], []).append(e)
    return [
        (nom, len(lignes), lignes[0]["usinabilite"], min(l["epaisseur"] for l in lignes), max(l["epaisseur"] for l in lignes))
        for nom, lignes in sorted(par_nom.items())
    ]


@transaction.atomic
def importer_materiaux(entrees, correspondance, poste=None, remplacer_calcules=True):
    """Crée (ou met à jour) un paramètre de coupe par entrée. `correspondance` : {nom du fichier: nom de la matière de l'ERP}
    (une entrée sans correspondance est ignorée). Une matière absente de l'ERP est créée. Les paramètres « relevés sur la
    machine » ne sont jamais touchés. Retourne un dictionnaire de compteurs."""
    stats = {"crees": 0, "mis_a_jour": 0, "ignores": 0, "proteges": 0, "matieres_creees": 0}
    matieres = {}
    for e in entrees:
        nom_erp = (correspondance.get(e["nom"]) or "").strip()
        if not nom_erp:
            stats["ignores"] += 1
            continue
        if nom_erp not in matieres:
            matiere, creee = Matiere.objects.get_or_create(nom=nom_erp, defaults={"densite": e["densite"] or 1, "usinabilite": e["usinabilite"]})
            if not creee and matiere.usinabilite is None:
                matiere.usinabilite = e["usinabilite"]
                matiere.save(update_fields=["usinabilite"])
            stats["matieres_creees"] += creee
            matieres[nom_erp] = matiere
        matiere = matieres[nom_erp]
        valeurs = {
            "poste": poste, "usinabilite": e["usinabilite"], "percage_stationnaire_hp_s": e["percage_hp_s"],
            "percage_stationnaire_bp_s": e["percage_bp_s"], "percage_circulaire_hp_tours": e["circ_hp"],
            "percage_circulaire_bp_tours": e["circ_bp"], "diametre_percage_mm": e["diametre"],
            "temporisation_pointage_s": e["pointage_s"], "temporisation_marquage_s": e["marquage_s"],
            "vitesse_marquage_mm_min": e["vitesse_marquage"], "percement_lineaire_mm": e["linear"], "chevauchement_mm": e["overcut"],
            "intervalle_pieces_mm": e["intervalle"], "rayon_pleine_vitesse_mm": RAYON_PLEINE_VITESSE_MM, "origine": "calcule",
        }
        parametre = ParametreCoupe.objects.filter(
            procede=ParametreCoupe.Procede.JET_EAU, matiere=matiere, epaisseur_mm=e["epaisseur"]
        ).first()
        if parametre is None:
            parametre = ParametreCoupe.objects.create(matiere=matiere, epaisseur_mm=e["epaisseur"], **valeurs)
            stats["crees"] += 1
        elif parametre.origine == "machine":
            stats["proteges"] += 1
            continue
        elif remplacer_calcules:
            for champ, valeur in valeurs.items():
                setattr(parametre, champ, valeur)
            parametre.save()
            stats["mis_a_jour"] += 1
        else:
            stats["ignores"] += 1
            continue
        parametre.vitesses.all().delete()
        calculees = vitesses_depuis_usinabilite(e["usinabilite"], e["epaisseur"])
        VitesseCoupe.objects.bulk_create(
            VitesseCoupe(
                parametre=parametre, qualite=c["qualite"], vitesse_haute_mm_min=c["vitesse_haute_mm_min"],
                vitesse_basse_mm_min=c["vitesse_basse_mm_min"], paliers=q["paliers"],
                distance_acceleration_mm=round(q["acc"] * q["paliers"], 3), distance_deceleration_mm=round(q["dec"] * q["paliers"], 3),
                coefficient_haut=q["haut"], coefficient_bas=q["bas"], facteur_arc=q["arc"],
            )
            for c, q in zip(calculees, e["qualites"])
        )
    return stats
