"""Estimation du temps de découpe d'une pièce (jet d'eau) à partir de sa géométrie et des paramètres de coupe de la
matière/épaisseur (repris du logiciel de la machine : vitesses par niveau de qualité, perçage, marquage).

Modèle (volontairement simple et réglable ; à caler sur les temps réels du logiciel de la machine) :
- **coupe** : chaque contour (extérieur + trous) est découpé segment par segment. Une ligne droite ou un arc de grand
  rayon se coupe à la vitesse élevée ; plus l'arc est serré, plus la vitesse baisse vers la vitesse basse (par
  `paliers` crans). Le rayon de pleine vitesse est un paramètre (`rayon_pleine_vitesse_mm`) ;
- **coins** : à chaque changement de direction brusque (au-delà de `seuil_angle_coin_deg`) la machine ralentit sur
  la distance d'accélération + décélération (A + R) : on compte ce tronçon à la vitesse basse ;
- **amorce et fermeture** : par contour, le percement linéaire et le chevauchement s'ajoutent, à vitesse basse ;
- **perçage** : par contour, temporisation de pointage + temps de perçage selon le mode choisi ;
- **marquage** : longueur des tracés de gravure à la vitesse de marquage + temporisation par tracé ;
- le tout multiplié par `coefficient_ajustement` (le réglage qui permet de caler le calcul sur la machine).

Les déplacements à vide entre contours et le réglage de la machine ne sont pas comptés (le réglage reste le
« temps fixe » de la gamme)."""

import math
from dataclasses import dataclass, field


class ErreurTemps(Exception):
    """Estimation impossible (pièce non importée, matière ou épaisseur sans paramètres, qualité sans vitesses…)."""


@dataclass
class EstimationTemps:
    coupe_s: float
    percage_s: float
    marquage_s: float
    total_s: float
    longueur_coupe_mm: float
    nb_percages: int
    nb_coins: int
    parametre: object = None
    avertissements: list = field(default_factory=list)

    @property
    def total_min(self):
        return self.total_s / 60


def parametre_pour(piece, procede="jet_eau"):
    """Paramètre de coupe de la matière et de l'épaisseur de la pièce (l'épaisseur la plus proche, avec avertissement,
    si l'épaisseur exacte n'existe pas)."""
    from decoupe.models import ParametreCoupe

    if not piece.matiere_id or not piece.epaisseur:
        raise ErreurTemps("Renseignez la matière et l'épaisseur de la pièce pour estimer son temps de découpe.")
    candidats = list(ParametreCoupe.objects.filter(procede=procede, matiere_id=piece.matiere_id))
    if not candidats:
        raise ErreurTemps(f"Aucun paramètre de coupe pour la matière « {piece.matiere} » (menu Paramètres de coupe).")
    meilleur = min(candidats, key=lambda p: abs(p.epaisseur_mm - piece.epaisseur))
    avertissement = None
    if abs(meilleur.epaisseur_mm - piece.epaisseur) > 1e-6:
        avertissement = (
            f"Pas de paramètre pour {piece.epaisseur:g} mm : estimation avec l'épaisseur la plus proche "
            f"({meilleur.epaisseur_mm:g} mm)."
        )
    return meilleur, avertissement


def _fermer(points):
    points = [tuple(p) for p in points]
    if len(points) >= 2 and points[0] != points[-1]:
        points.append(points[0])
    return points


def _angle(a, b, c):
    """Changement de direction (radians, 0 = tout droit) au point b du trajet a → b → c."""
    v1 = (b[0] - a[0], b[1] - a[1])
    v2 = (c[0] - b[0], c[1] - b[1])
    n1, n2 = math.hypot(*v1), math.hypot(*v2)
    if n1 == 0 or n2 == 0:
        return 0.0
    cos = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)))
    return math.acos(cos)


def _vitesse_arc(rayon, vitesse, parametre):
    """Vitesse (mm/min) sur un arc de rayon donné, avec `paliers` crans entre la basse et l'élevée."""
    haute, basse = vitesse.vitesse_haute_mm_min * vitesse.coefficient_haut, vitesse.vitesse_basse_mm_min * vitesse.coefficient_bas
    if rayon == math.inf or rayon >= parametre.rayon_pleine_vitesse_mm:
        return haute
    fraction = max(0.0, rayon / parametre.rayon_pleine_vitesse_mm)
    paliers = max(1, vitesse.paliers)
    fraction = math.floor(fraction * paliers) / paliers  # palier inférieur : estimation prudente
    return (basse + (haute - basse) * fraction) * (vitesse.facteur_arc or 1)


def _temps_contour(points, parametre, vitesse):
    """(secondes de coupe, longueur coupée mm, nombre de coins) d'un contour fermé, hors perçage."""
    points = _fermer(points)
    if len(points) < 3:
        return 0.0, 0.0, 0
    anneau = points[:-1]
    n = len(anneau)
    seuil = math.radians(parametre.seuil_angle_coin_deg)
    angles = [_angle(anneau[i - 1], anneau[i], anneau[(i + 1) % n]) for i in range(n)]
    coins = [a >= seuil for a in angles]
    haute = vitesse.vitesse_haute_mm_min * vitesse.coefficient_haut
    basse = vitesse.vitesse_basse_mm_min * vitesse.coefficient_bas
    temps, longueur = 0.0, 0.0
    for i in range(n):
        j = (i + 1) % n
        long_seg = math.hypot(anneau[j][0] - anneau[i][0], anneau[j][1] - anneau[i][1])
        if long_seg == 0:
            continue
        # courbure moyenne du segment, sans compter les coins (traités à part)
        courbures = [angles[k] for k in (i, j) if not coins[k]]
        theta = sum(courbures) / 2 if courbures else 0.0
        rayon = long_seg / theta if theta > 1e-9 else math.inf
        vitesse_seg = _vitesse_arc(rayon, vitesse, parametre)
        temps += long_seg / (vitesse_seg / 60)
        longueur += long_seg
    nb_coins = sum(coins)
    if nb_coins and haute > basse > 0:
        zone = vitesse.distance_acceleration_mm + vitesse.distance_deceleration_mm
        temps += nb_coins * zone * (1 / (basse / 60) - 1 / (haute / 60))
    # amorce (percement linéaire) et fermeture (chevauchement) : à vitesse basse
    ajout = parametre.percement_lineaire_mm + parametre.chevauchement_mm
    temps += ajout / (basse / 60)
    return temps, longueur + ajout, nb_coins


def _temps_percage(parametre, vitesse):
    mode = parametre.mode_percage
    if mode == parametre.ModePercage.STATIONNAIRE_HP:
        duree = parametre.percage_stationnaire_hp_s
    elif mode == parametre.ModePercage.STATIONNAIRE_BP:
        duree = parametre.percage_stationnaire_bp_s
    else:
        tours = (
            parametre.percage_circulaire_hp_tours if mode == parametre.ModePercage.CIRCULAIRE_HP
            else parametre.percage_circulaire_bp_tours
        )
        basse = vitesse.vitesse_basse_mm_min * vitesse.coefficient_bas
        duree = tours * math.pi * parametre.diametre_percage_mm / (basse / 60)
    return parametre.temporisation_pointage_s + duree


def estimer_temps_decoupe(piece, qualite=None, parametre=None):
    """Temps de découpe d'UNE pièce (secondes, détail et avertissements). Lève ErreurTemps si impossible."""
    if piece.statut != piece.Statut.OK or not piece.contour_json:
        raise ErreurTemps("La géométrie de la pièce n'est pas encore importée.")
    avertissements = []
    if parametre is None:
        parametre, avertissement = parametre_pour(piece)
        if avertissement:
            avertissements.append(avertissement)
    qualite = float(qualite if qualite is not None else piece.qualite_coupe)
    vitesse = next((v for v in parametre.vitesses.all() if abs(float(v.qualite) - qualite) < 1e-9), None)
    if vitesse is None:
        raise ErreurTemps(f"Pas de vitesses de coupe pour la qualité {qualite:g} dans « {parametre} ».")

    contours = [piece.contour_json.get("exterieur") or []] + list(piece.contour_json.get("trous") or [])
    contours = [c for c in contours if len(c) >= 3]
    coupe_s, longueur, nb_coins = 0.0, 0.0, 0
    for contour in contours:
        t, l, c = _temps_contour(contour, parametre, vitesse)
        coupe_s, longueur, nb_coins = coupe_s + t, longueur + l, nb_coins + c
    percage_s = len(contours) * _temps_percage(parametre, vitesse)

    traits = (piece.gravure_json or {}).get("traits") or []
    marquage_s = 0.0
    if traits and parametre.vitesse_marquage_mm_min > 0:
        marquage_s = (piece.longueur_gravure_mm or 0) / (parametre.vitesse_marquage_mm_min / 60)
        marquage_s += len(traits) * parametre.temporisation_marquage_s

    coefficient = parametre.coefficient_ajustement or 1
    return EstimationTemps(
        coupe_s=coupe_s * coefficient, percage_s=percage_s * coefficient, marquage_s=marquage_s * coefficient,
        total_s=(coupe_s + percage_s + marquage_s) * coefficient, longueur_coupe_mm=longueur,
        nb_percages=len(contours), nb_coins=nb_coins, parametre=parametre, avertissements=avertissements,
    )
