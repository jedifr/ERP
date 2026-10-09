"""Lecture d'un fichier STEP (ISO 10303) : dimensions, volume, surface, masse estimée.

Dépendance optionnelle : `cadquery` (OpenCASCADE). Elle est importée à l'appel pour que l'ERP
démarre même si le paquet n'est pas installé (roues OCP absentes sur certains NAS ARM).
"""

from dataclasses import dataclass


class ErreurLectureStep(Exception):
    """Fichier STEP illisible, vide, ou bibliothèque de lecture indisponible."""


@dataclass
class AnalyseStep:
    longueur_mm: float
    largeur_mm: float
    hauteur_mm: float
    volume_mm3: float
    surface_mm2: float
    nb_solides: int
    nb_faces: int

    def masse_kg(self, densite_kg_dm3):
        """Masse estimée : 1 dm³ = 1 000 000 mm³."""
        return self.volume_mm3 / 1_000_000 * densite_kg_dm3


def analyser_step(chemin):
    """Lit un .step/.stp et renvoie une `AnalyseStep` (unités du fichier, supposées en mm)."""
    try:
        import cadquery as cq
    except ImportError as exc:
        raise ErreurLectureStep(
            "La lecture des fichiers STEP nécessite le paquet 'cadquery' (pip install cadquery)."
        ) from exc

    try:
        forme = cq.importers.importStep(str(chemin))
    except Exception as exc:  # OpenCASCADE lève des types variés (ValueError, OCP.Standard_*)
        raise ErreurLectureStep(f"Fichier STEP invalide ou illisible : {exc}") from exc

    solides = forme.solids().vals()
    if not solides:
        raise ErreurLectureStep("Le fichier STEP ne contient aucun solide.")

    boite = forme.val().BoundingBox()
    # Dimensions triées : la plus grande en longueur, indépendamment de l'orientation dans la CAO.
    longueur, largeur, hauteur = sorted((boite.xlen, boite.ylen, boite.zlen), reverse=True)
    return AnalyseStep(
        longueur_mm=round(longueur, 3),
        largeur_mm=round(largeur, 3),
        hauteur_mm=round(hauteur, 3),
        volume_mm3=round(sum(s.Volume() for s in solides), 3),
        surface_mm2=round(sum(s.Area() for s in solides), 3),
        nb_solides=len(solides),
        nb_faces=len(forme.faces().vals()),
    )
