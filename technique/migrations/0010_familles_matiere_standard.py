"""Familles de matière standard (usinabilités fournies) + rattachement des matières existantes d'après leur nom."""

import re
import unicodedata

from django.db import migrations

# (ordre, nom, nom dans materials.lua, usinabilité, mots-clés)
FAMILLES = [
    (1, "Acier trempé", "", 80.4, "hardox, trempe, hardened, creusabro, quard, armox"),
    (2, "Inox", "Stainless Steel", 81.9,
     "inox, stainless, 304, 316, 310, 321, 430, 1.4301, 1.4307, 1.4401, 1.4404, 1.4571, duplex"),
    (3, "Aluminium", "Aluminium", 213.0,
     "alu, aluminium, aluminum, 1050, 1100, 2017, 2024, 3003, 5005, 5052, 5083, 5086, 5754, 6060, 6061, 6082, 7020, 7075, en aw"),
    (4, "Cuivre", "Copper", 110.0, "cuivre, copper, cu-, bronze"),
    (5, "Laiton", "Brass", 110.0, "laiton, brass, cuzn"),
    (6, "Titane", "Titanium", 115.0, "titane, titanium, ta6v"),
    (7, "Alliage de zinc", "", 136.0, "zinc, zamak"),
    (10, "Acier", "Steel", 87.6,
     "acier, steel, s185, s235, s275, s355, s420, s460, s690, e24, e28, e36, a37, a42, a52, c22, c35, c45, 42cd4, 25cd4, dc01, dd11, corten"),
    (20, "Granit", "Granite", 322.0, "granit, granite"),
    (21, "Marbre", "Marble", 535.0, "marbre, marble"),
    (22, "Verre", "Glas", 596.0, "verre, glass, glas"),
    (23, "Nylon", "Nilo", 538.0, "nylon, polyamide, pa6"),
    (24, "Plexiglas", "Plexiglas", 690.0, "plexi, plexiglas, pmma, acrylique"),
    (25, "Graphite", "Grafite", 879.0, "graphite, grafite"),
    (26, "Polypropylène", "", 985.0, "polypropylene"),
]


def _norm(texte):
    return "".join(c for c in unicodedata.normalize("NFD", (texte or "").lower()) if unicodedata.category(c) != "Mn")


def _famille_pour(nom, familles):
    jetons = re.findall(r"[a-z0-9]+(?:\.[0-9]+)*", _norm(nom))
    for famille in familles:
        for mot in (m.strip() for m in famille.mots_cles.replace("\n", ",").split(",")):
            mot = _norm(mot)
            if not mot:
                continue
            if " " in mot or "-" in mot:
                if mot in _norm(nom):
                    return famille
            elif any(j.startswith(mot) for j in jetons):
                return famille
    return None


def creer_familles(apps, schema_editor):
    Famille = apps.get_model("technique", "FamilleMatiere")
    Matiere = apps.get_model("technique", "Matiere")
    for ordre, nom, nom_igems, usinabilite, mots in FAMILLES:
        Famille.objects.get_or_create(
            nom=nom, defaults={"ordre": ordre, "nom_igems": nom_igems, "usinabilite": usinabilite, "mots_cles": mots}
        )
    familles = list(Famille.objects.order_by("ordre", "nom"))
    for matiere in Matiere.objects.filter(famille__isnull=True):
        famille = _famille_pour(matiere.nom, familles)
        if famille:
            matiere.famille = famille
            matiere.save(update_fields=["famille"])


class Migration(migrations.Migration):

    dependencies = [("technique", "0009_familles_matiere")]

    operations = [migrations.RunPython(creer_familles, migrations.RunPython.noop)]
