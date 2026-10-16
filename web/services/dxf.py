"""Fichiers de CAO envoyés depuis le site client (DXF, DWG ou SVG) : l'ERP les lit avec le même import que les pièces des
devis, garde le fichier d'origine et sa version DXF (pour la fabrication) et rend un jeton. Le prix et la demande
désignent ensuite la pièce par ce jeton : le fichier ne transite qu'une fois. Un fichier = une pièce.

Le SVG est converti en DXF ici (courbes aplaties en segments de 0,5 mm, échelle tirée des unités du fichier : mm, cm, in, pt, et
96 px par pouce sinon). Le DWG passe par un convertisseur installé sur le serveur : `dwg2dxf` (LibreDWG) ou ODA File
Converter ; sans convertisseur, le client est invité à envoyer un DXF."""

import hashlib
import io
import json
import math
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from django.conf import settings

TAILLE_MAX = 1536 * 1024  # 2 Mo une fois en base64, sous la limite de Django (2,5 Mo)
FORMATS = (".dxf", ".dwg", ".svg")
PX_PAR_MM = 96 / 25.4  # unité utilisateur SVG (px CSS)
PAS_SVG_MM = 0.5  # longueur des segments qui remplacent courbes et arcs du SVG
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
    extension = Path(nom).suffix.lower()
    if extension not in FORMATS:
        raise ErreurDxf("Envoyez un fichier DXF, DWG ou SVG (exporté depuis votre logiciel de dessin).")
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
    avertissements = []
    if extension == ".dxf":
        chemin.write_bytes(contenu)
    else:
        original = d / f"{jeton}{extension}"
        original.write_bytes(contenu)  # l'original, tel quel
        try:
            if extension == ".svg":
                dxf, avertissements = svg_vers_dxf(contenu)
            else:
                dxf = dwg_vers_dxf(original)
        except ErreurDxf:
            original.unlink(missing_ok=True)
            raise
        chemin.write_bytes(dxf)
    try:
        g = extraire_geometrie(str(chemin), "dxf")
    except ErreurImportGeometrie as exc:
        chemin.unlink(missing_ok=True)
        (d / f"{jeton}{extension}").unlink(missing_ok=True)
        # le message de l'import cite le chemin sur le serveur : on n'en montre que le nom du fichier
        raise ErreurDxf(f"Fichier illisible : {str(exc).replace(str(chemin), nom).replace(chemin.name, nom)}")
    if g.autres:
        chemin.unlink(missing_ok=True)
        (d / f"{jeton}{extension}").unlink(missing_ok=True)
        raise ErreurDxf(f"Le fichier contient {len(g.autres) + 1} pièces séparées : envoyez un fichier par pièce.")
    fiche = {
        "jeton": jeton, "nom": nom, "contour": {"exterieur": g.exterior, "trous": g.holes},
        "largeur_mm": round(g.largeur_mm, 2), "hauteur_mm": round(g.hauteur_mm, 2), "avertissements": avertissements + g.avertissements,
        "format": extension[1:],
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


def chemin_fichier(jeton, original=False):
    """DXF de fabrication du fichier déposé ; `original=True` : le fichier envoyé (SVG, DWG ou DXF)."""
    if not JETON.match(str(jeton or "")):
        return None
    for extension in ((".svg", ".dwg", ".dxf") if original else (".dxf",)):
        chemin = dossier() / f"{jeton}{extension}"
        if chemin.exists():
            return chemin
    return None


# ------------------------------------------------------------------ SVG
def svg_vers_dxf(contenu):
    """Convertit un SVG en DXF (octets, mm). Chaque tracé visible (path, rect, circle, ellipse, line, polyline, polygon)
    devient une polyligne ; les textes et images sont ignorés. Retourne (dxf, avertissements)."""
    import ezdxf
    from svgelements import SVG, Close, Image, Line, Move, Path, Shape, Text

    try:
        svg = SVG.parse(io.BytesIO(contenu), reify=True, ppi=96)
    except Exception:
        raise ErreurDxf("Fichier SVG illisible.")
    avertissements = []
    # Échelle : les unités de width / height priment ; sans unité, 96 px par pouce (norme CSS).
    echelle = 1 / PX_PAR_MM  # svgelements rend les coordonnées en px (96 par pouce), viewBox et unités appliquées
    if not re.search(r"(mm|cm|in|pt|pc)\s*$", str(svg.values.get("width") or "")):
        avertissements.append("Échelle du SVG déduite à 96 px par pouce : vérifiez les cotes affichées.")
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 4  # mm
    msp = doc.modelspace()
    traces = textes = 0
    for e in svg.elements():
        if isinstance(e, Text):
            textes += 1
            continue
        if isinstance(e, Image) or not isinstance(e, Shape):
            continue
        if e.values.get("visibility") == "hidden" or e.values.get("display") == "none":
            continue
        chemin = Path(e) if not isinstance(e, Path) else e
        for sous in chemin.as_subpaths():
            points, ferme = [], False
            for seg in sous:
                if isinstance(seg, Move):
                    points.append((seg.end.x, seg.end.y))
                elif isinstance(seg, (Line, Close)):
                    if seg.end is not None:
                        points.append((seg.end.x, seg.end.y))
                    ferme = ferme or isinstance(seg, Close)
                else:
                    longueur = seg.length(error=1e-3) * echelle
                    n = min(max(int(math.ceil(longueur / PAS_SVG_MM)), 8), 2000)
                    points.extend((p.x, p.y) for p in (seg.point(k / n) for k in range(1, n + 1)))
            points = [(x * echelle, -y * echelle) for x, y in points]
            if len(points) >= 2 and math.dist(points[0], points[-1]) < 1e-3:
                points.pop()
                ferme = True
            if len(points) >= 2:
                msp.add_lwpolyline(points, close=ferme)
                traces += 1
    if not traces:
        raise ErreurDxf("Le SVG ne contient aucun tracé (les textes doivent être convertis en tracés).")
    if textes:
        avertissements.append(f"{textes} texte(s) du SVG ignoré(s) : convertissez-les en tracés pour les graver ou découper.")
    flux = io.StringIO()
    doc.write(flux)
    return flux.getvalue().encode("utf-8"), avertissements


# ------------------------------------------------------------------ DWG
def dwg_lisible():
    if shutil.which("dwg2dxf"):
        return True
    try:
        from ezdxf.addons import odafc

        return odafc.is_installed()
    except ImportError:
        return False


def dwg_vers_dxf(chemin):
    """Convertit un DWG en DXF avec le convertisseur installé sur le serveur (LibreDWG, sinon ODA File Converter)."""
    sans = ErreurDxf("Le format DWG n'est pas encore lu en ligne : exportez votre pièce en DXF depuis votre logiciel de dessin.")
    if shutil.which("dwg2dxf"):
        with tempfile.TemporaryDirectory() as tmp:
            sortie = Path(tmp) / "piece.dxf"
            try:
                subprocess.run(["dwg2dxf", "-y", "-o", str(sortie), str(chemin)], capture_output=True, timeout=60, check=False)
            except subprocess.TimeoutExpired:
                raise ErreurDxf("Fichier DWG trop long à convertir : envoyez-le en DXF.")
            if not sortie.exists() or not sortie.stat().st_size:
                raise ErreurDxf("Fichier DWG illisible (version non prise en charge ?) : envoyez-le en DXF.")
            return sortie.read_bytes()
    try:
        from ezdxf.addons import odafc
    except ImportError:
        raise sans
    if not odafc.is_installed():
        raise sans
    try:
        doc = odafc.readfile(str(chemin))
    except Exception:
        raise ErreurDxf("Fichier DWG illisible : envoyez-le en DXF.")
    flux = io.StringIO()
    doc.write(flux)
    return flux.getvalue().encode("utf-8")
