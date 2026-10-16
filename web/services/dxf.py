"""Fichiers DXF envoyés depuis le site client : l'ERP les lit avec le même import que les pièces des devis, garde le
fichier d'origine (pour la fabrication) et rend un jeton. Le prix et la demande désignent ensuite la pièce par ce jeton :
le fichier ne transite qu'une fois. Un fichier = une pièce."""

import hashlib
import json
import re
from pathlib import Path

from django.conf import settings

TAILLE_MAX = 1536 * 1024  # 2 Mo une fois en base64, sous la limite de Django (2,5 Mo)
JETON = re.compile(r"^[0-9a-f]{64}$")


class ErreurDxf(Exception):
    pass


def dossier():
    d = Path(settings.MEDIA_ROOT) / "web" / "dxf"
    d.mkdir(parents=True, exist_ok=True)
    return d


def deposer(nom, contenu):
    """Lit un DXF (octets) et le garde. Retourne la fiche {jeton, nom, contour, largeur_mm, hauteur_mm, avertissements}.
    Lève ErreurDxf (message pour le client) si le fichier n'est pas exploitable."""
    from decoupe.services.geometrie import ErreurImportGeometrie, extraire_geometrie

    nom = Path(str(nom or "piece.dxf")).name[:120]
    if not nom.lower().endswith(".dxf"):
        raise ErreurDxf("Envoyez un fichier .dxf (exportez-le depuis votre logiciel de dessin ; le DWG n'est pas lu en ligne).")
    if not contenu:
        raise ErreurDxf("Le fichier est vide.")
    if len(contenu) > TAILLE_MAX:
        raise ErreurDxf("Fichier trop gros (1,5 Mo au plus) : envoyez-le avec une demande de devis.")
    jeton = hashlib.sha256(contenu).hexdigest()
    d = dossier()
    fiche_chemin = d / f"{jeton}.json"
    if fiche_chemin.exists():
        fiche = json.loads(fiche_chemin.read_text("utf-8"))
        return {**fiche, "nom": nom}
    chemin = d / f"{jeton}.dxf"
    chemin.write_bytes(contenu)
    try:
        g = extraire_geometrie(str(chemin), "dxf")
    except ErreurImportGeometrie as exc:
        chemin.unlink(missing_ok=True)
        # le message de l'import cite le chemin sur le serveur : on n'en montre que le nom du fichier
        raise ErreurDxf(f"Fichier illisible : {str(exc).replace(str(chemin), nom).replace(chemin.name, nom)}")
    if g.autres:
        chemin.unlink(missing_ok=True)
        raise ErreurDxf(f"Le fichier contient {len(g.autres) + 1} pièces séparées : envoyez un fichier par pièce.")
    fiche = {
        "jeton": jeton, "nom": nom, "contour": {"exterieur": g.exterior, "trous": g.holes},
        "largeur_mm": round(g.largeur_mm, 2), "hauteur_mm": round(g.hauteur_mm, 2), "avertissements": g.avertissements,
    }
    fiche_chemin.write_text(json.dumps(fiche), "utf-8")
    return fiche


def charger(jeton):
    """Fiche d'un DXF déjà déposé. Lève ErreurDxf si le jeton est inconnu (fichier à renvoyer)."""
    jeton = str(jeton or "")
    if not JETON.match(jeton):
        raise ErreurDxf("Fichier DXF inconnu : renvoyez-le.")
    chemin = dossier() / f"{jeton}.json"
    if not chemin.exists():
        raise ErreurDxf("Fichier DXF inconnu : renvoyez-le.")
    return json.loads(chemin.read_text("utf-8"))


def chemin_fichier(jeton):
    if not JETON.match(str(jeton or "")):
        return None
    chemin = dossier() / f"{jeton}.dxf"
    return chemin if chemin.exists() else None
