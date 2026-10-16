"""Bibliothèque de formes décoratives du configurateur : silhouettes de game-icons.net (licence CC BY 3.0, crédit affiché
sur le site), réduites aux formes d'un seul tenant et préparées par web/donnees/generer_game_icons.py.

Le client choisit une forme et sa largeur ; la hauteur suit les proportions. Règle de l'atelier : aucun trait ni jour plus
fin que l'épaisseur de la tôle. Chaque forme porte le plus grand rapport épaisseur / largeur qui la respecte, d'où sa
largeur mini à une épaisseur donnée."""

import json
import math
from functools import lru_cache
from pathlib import Path

FICHIER = Path(__file__).resolve().parent.parent / "donnees" / "game_icons.json"
LARGEUR_MAX = 3000  # mm ; au-delà, le format des tôles refuse de toute façon la pièce


class ErreurBibliotheque(Exception):
    pass


@lru_cache(maxsize=1)
def _donnees():
    donnees = json.loads(FICHIER.read_text(encoding="utf-8"))
    donnees["index"] = {f["cle"]: f for f in donnees["formes"]}
    return donnees


def credit():
    d = _donnees()
    return {k: d[k] for k in ("source", "url", "licence", "licence_url", "credit")}


def resume():
    """Pour le catalogue : le crédit de la source et le nombre de formes (la liste complète a son propre appel)."""
    return {**credit(), "nombre": len(_donnees()["formes"])}


def liste():
    """Ce que le site affiche : crédit, puis pour chaque forme sa clé, son nom, ses proportions, son rapport
    épaisseur / largeur maxi et une vignette (chemin SVG, viewBox « 0 0 100 proportion × 100 »)."""
    d = _donnees()
    return {
        **credit(),
        "formes": [
            {"cle": f["cle"], "nom": f["nom"], "proportion": round(f["hauteur"] / d["largeur"], 4), "rapport": f["rapport"], "apercu": f["apercu"]}
            for f in sorted(d["formes"], key=lambda f: f["nom"].lower())
        ],
    }


def forme(cle):
    f = _donnees()["index"].get(str(cle or ""))
    if f is None:
        raise ErreurBibliotheque("Cette forme n'existe pas dans la bibliothèque.")
    return f


def largeur_mini(cle, epaisseur):
    """Largeur mini (mm, arrondie au mm supérieur) pour qu'aucun trait ni jour ne soit plus fin que l'épaisseur."""
    return math.ceil(float(epaisseur) / forme(cle)["rapport"] - 1e-9)


def _decoder(chaine, k):
    valeurs = [int(v) for v in chaine.split()]
    x = y = 0
    points = []
    for i in range(0, len(valeurs), 2):
        x += valeurs[i]
        y += valeurs[i + 1]
        points.append((round(x * k, 4), round(y * k, 4)))
    return points


def construire(cle, largeur):
    """Contour {exterieur, trous} de la forme à `largeur` mm, et sa désignation."""
    f = forme(cle)
    try:
        largeur = float(largeur)
    except (TypeError, ValueError):
        raise ErreurBibliotheque("Largeur de la forme : un nombre est attendu.")
    if not 0 < largeur <= LARGEUR_MAX:
        raise ErreurBibliotheque(f"Largeur de la forme entre 1 et {LARGEUR_MAX} mm.")
    d = _donnees()
    k = largeur / d["largeur"] / d["resolution"]
    anneaux = [_decoder(c, k) for c in f["contour"]]
    hauteur = f["hauteur"] * largeur / d["largeur"]
    cote = lambda v: f"{round(v, 1):g}".replace(".", ",")
    designation = f"Forme « {f['nom']} » {cote(largeur)} × {cote(hauteur)} mm"
    return {"exterieur": anneaux[0], "trous": anneaux[1:]}, designation
