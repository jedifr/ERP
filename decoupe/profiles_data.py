"""Sections de profilés livrées avec l'application (table `ProfileSection`), toutes « non vérifiées ».

Masse linéique **de référence en acier** (7,85 kg/dm³) ; pour l'aluminium (≈ 2,70) et l'inox (≈ 7,90) la masse se déduit par le rapport des
densités (`ProfileSection.masse_pour`), la section étant la même.

- Cornières à ailes égales (EN 10056-1), UPN (EN 10279), IPE (EN 10365), HEA et HEB : masses du tableau usuel des fabricants.
- Cornières à ailes inégales : masse calculée, angles vifs, aire t × (a + b − t) (environ 1 % sous le catalogue).
- Tubes carrés et rectangulaires formés à froid (EN 10219) : aire 2t(h + b − 2t) − (4 − π)(ro² − ri²) avec les rayons de la norme (t ≤ 6 :
  ro = 2 t, ri = t ; t ≤ 10 : ro = 2,5 t, ri = 1,5 t ; au-delà ro = 3 t, ri = 2 t), qui retrouve le catalogue ArcelorMittal 2020 à moins de 2 %
  (40×40×3 = 3,30 kg/m ; 100×100×5 = 14,4 kg/m). Les masses du catalogue lui-même sont reprises par `profiles_catalogue_arcelor.py`.
- Tubes ronds (EN 10220) : π t (D − t) ; plats, ronds et carrés pleins : aire exacte.

Ce ne sont que des valeurs de départ, à contrôler avec le catalogue du fournisseur (coche « Vérifié » ensuite) ; elles se remplacent ou se
complètent dans Socle technique > Sections de profilés."""

import math

DENSITE = 7.85  # kg/dm³ (acier)

# (côté, épaisseur, kg/m)
CORNIERES = [
    (20, 3, 0.882), (25, 3, 1.12), (30, 3, 1.36), (30, 4, 1.78), (35, 4, 2.09), (40, 4, 2.42), (40, 5, 2.97), (45, 5, 3.38),
    (50, 5, 3.77), (50, 6, 4.47), (60, 6, 5.42), (60, 8, 7.09), (70, 7, 7.38), (80, 8, 9.63), (90, 9, 12.2), (100, 10, 15.1), (120, 12, 21.6),
]
# (grande aile a, petite aile b, épaisseur e)
CORNIERES_INEGALES = [
    (30, 20, 3), (40, 20, 4), (40, 25, 4), (50, 30, 5), (60, 30, 5), (60, 40, 6), (65, 50, 5), (75, 50, 6), (80, 40, 6), (80, 60, 7),
    (100, 50, 6), (100, 65, 8), (120, 80, 8), (150, 100, 10),
]
# (hauteur, largeur, âme, semelle, kg/m)
UPN = [
    (50, 38, 5, 7, 5.59), (65, 42, 5.5, 7.5, 7.09), (80, 45, 6, 8, 8.64), (100, 50, 6, 8.5, 10.6), (120, 55, 7, 9, 13.4),
    (140, 60, 7, 10, 16.0), (160, 65, 7.5, 10.5, 18.8), (180, 70, 8, 11, 22.0), (200, 75, 8.5, 11.5, 25.3), (220, 80, 9, 12.5, 29.4),
    (240, 85, 9.5, 13, 33.2), (260, 90, 10, 14, 37.9), (280, 95, 10, 15, 41.8), (300, 100, 10, 16, 46.2),
]
# (désignation, hauteur, largeur, âme, semelle, kg/m)
IPE = [
    ("IPE 80", 80, 46, 3.8, 5.2, 6.0), ("IPE 100", 100, 55, 4.1, 5.7, 8.1), ("IPE 120", 120, 64, 4.4, 6.3, 10.4), ("IPE 140", 140, 73, 4.7, 6.9, 12.9),
    ("IPE 160", 160, 82, 5.0, 7.4, 15.8), ("IPE 180", 180, 91, 5.3, 8.0, 18.8), ("IPE 200", 200, 100, 5.6, 8.5, 22.4), ("IPE 220", 220, 110, 5.9, 9.2, 26.2),
    ("IPE 240", 240, 120, 6.2, 9.8, 30.7), ("IPE 270", 270, 135, 6.6, 10.2, 36.1), ("IPE 300", 300, 150, 7.1, 10.7, 42.2),
]
HEA = [
    ("HEA 100", 96, 100, 5, 8, 16.7), ("HEA 120", 114, 120, 5, 8, 19.9), ("HEA 140", 133, 140, 5.5, 8.5, 24.7), ("HEA 160", 152, 160, 6, 9, 30.4),
    ("HEA 180", 171, 180, 6, 9.5, 35.5), ("HEA 200", 190, 200, 6.5, 10, 42.3),
]
HEB = [
    ("HEB 100", 100, 100, 6, 10, 20.4), ("HEB 120", 120, 120, 6.5, 11, 26.7), ("HEB 140", 140, 140, 7, 12, 33.7), ("HEB 160", 160, 160, 8, 13, 42.6),
    ("HEB 180", 180, 180, 8.5, 14, 51.2), ("HEB 200", 200, 200, 9, 15, 61.3),
]
# (côté, épaisseur)
TUBES_CARRES = [
    (15, 1.5), (20, 1.5), (20, 2), (25, 1.5), (25, 2), (25, 2.5), (30, 1.5), (30, 2), (30, 3), (35, 2), (35, 3), (40, 1.5), (40, 2), (40, 3), (40, 4),
    (50, 2), (50, 3), (50, 4), (50, 5), (60, 2), (60, 3), (60, 4), (60, 5), (70, 3), (70, 4), (80, 3), (80, 4), (80, 5), (100, 3), (100, 4), (100, 5), (100, 6),
    (120, 4), (120, 5), (120, 6), (140, 5), (140, 6), (150, 5), (150, 6), (160, 6), (200, 6), (200, 8),
]
# (hauteur, largeur, épaisseur)
TUBES_RECTANGULAIRES = [
    (30, 20, 2), (40, 20, 1.5), (40, 20, 2), (40, 30, 2), (40, 30, 3), (50, 20, 2), (50, 30, 2), (50, 30, 3), (60, 20, 2), (60, 30, 3), (60, 40, 2), (60, 40, 3),
    (60, 40, 4), (70, 40, 3), (80, 40, 2), (80, 40, 3), (80, 40, 4), (80, 60, 3), (80, 60, 4), (100, 40, 3), (100, 50, 2), (100, 50, 3), (100, 50, 4), (100, 50, 5),
    (100, 60, 3), (100, 60, 4), (100, 60, 5), (120, 40, 3), (120, 60, 3), (120, 60, 4), (120, 60, 5), (120, 80, 4), (120, 80, 5), (120, 80, 6), (140, 80, 4),
    (140, 80, 5), (150, 100, 4), (150, 100, 5), (150, 100, 6), (160, 80, 4), (160, 80, 5), (200, 100, 4), (200, 100, 5), (200, 100, 6), (200, 100, 8),
    (200, 120, 5), (200, 120, 6), (250, 150, 6), (250, 150, 8),
]
# (diamètre extérieur, épaisseur)
TUBES_RONDS = [
    (12, 1.5), (16, 2), (17.2, 2), (20, 2), (21.3, 2), (21.3, 2.6), (25, 2), (26.9, 2), (26.9, 2.6), (26.9, 3.2), (30, 2), (33.7, 2.6), (33.7, 3.2), (33.7, 4),
    (35, 2), (40, 2), (42.4, 2.6), (42.4, 3.2), (42.4, 4), (48.3, 2.6), (48.3, 3.2), (48.3, 4), (50, 2.5), (60.3, 2.9), (60.3, 3.6), (60.3, 4),
    (76.1, 2.9), (76.1, 3.6), (76.1, 4), (88.9, 3.2), (88.9, 4.0), (88.9, 5), (101.6, 3.6), (101.6, 4), (114.3, 3.6), (114.3, 4.5), (114.3, 5),
    (139.7, 4), (139.7, 5.0), (139.7, 6.3), (168.3, 4.5), (168.3, 5.0), (168.3, 6.3), (193.7, 5), (193.7, 6.3), (219.1, 5), (219.1, 6.3), (219.1, 8),
]
# (largeur, épaisseur)
PLATS = [
    (20, 3), (20, 5), (25, 3), (25, 5), (30, 3), (30, 4), (30, 5), (40, 3), (40, 4), (40, 5), (40, 6), (40, 8), (50, 4), (50, 5), (50, 6), (50, 8), (50, 10),
    (60, 5), (60, 6), (60, 8), (60, 10), (80, 5), (80, 6), (80, 8), (80, 10), (100, 5), (100, 6), (100, 8), (100, 10), (100, 12), (120, 6), (120, 8), (120, 10),
    (150, 6), (150, 8), (150, 10), (150, 12), (200, 8), (200, 10), (200, 12), (200, 15), (200, 20),
]
RONDS_PLEINS = [6, 8, 10, 12, 14, 16, 18, 20, 25, 30, 35, 40, 50, 60, 70, 80, 100]
CARRES_PLEINS = [8, 10, 12, 14, 16, 20, 25, 30, 40]


def rayons_tube(e):
    """(rayon extérieur, rayon intérieur) des angles d'un tube carré ou rectangulaire selon l'épaisseur (EN 10219)."""
    if e <= 6:
        return 2.0 * e, 1.0 * e
    if e <= 10:
        return 2.5 * e, 1.5 * e
    return 3.0 * e, 2.0 * e


def aire_tube_rectangulaire(h, b, e):
    """Aire (mm²) de la section d'un tube carré ou rectangulaire, angles arrondis selon EN 10219."""
    ro, ri = rayons_tube(e)
    return 2 * e * (h + b - 2 * e) - (4 - math.pi) * (ro**2 - ri**2)


def _g(x):
    return f"{x:g}"


def masse_aire(aire_mm2):
    """Masse linéique (kg/m) en acier d'une section d'aire donnée (mm²)."""
    return aire_mm2 * DENSITE / 1000


def lignes():
    """(famille, désignation, cotes, masse linéique kg/m en acier, source, ordre)."""
    resultat = []
    ordre = 0

    def ajouter(famille, designation, cotes, masse, source):
        nonlocal ordre
        ordre += 1
        resultat.append((famille, designation, cotes, round(masse, 3), source, ordre))

    for c, e, m in CORNIERES:
        ajouter("corniere", f"L {_g(c)}×{_g(c)}×{_g(e)}", {"a": c, "b": c, "e": e}, m, "EN 10056-1, masse du tableau usuel")
    for a, b, e in CORNIERES_INEGALES:
        ajouter("corniere_inegale", f"L {_g(a)}×{_g(b)}×{_g(e)}", {"a": a, "b": b, "e": e}, masse_aire(e * (a + b - e)), "masse calculée, angles vifs (environ 1 % sous le catalogue)")
    for h, b, tw, tf, m in UPN:
        ajouter("upn", f"UPN {_g(h)}", {"h": h, "b": b, "tw": tw, "tf": tf}, m, "EN 10279, masse du tableau usuel")
    for famille, table, source in (("ipe", IPE, "EN 10365, masse du tableau usuel"), ("hea", HEA, "EN 10365, masse du tableau usuel"), ("heb", HEB, "EN 10365, masse du tableau usuel")):
        for nom, h, b, tw, tf, m in table:
            ajouter(famille, nom, {"h": h, "b": b, "tw": tw, "tf": tf}, m, source)
    for c, e in TUBES_CARRES:
        ajouter("tube_carre", f"Tube {_g(c)}×{_g(c)}×{_g(e)}", {"c": c, "e": e}, masse_aire(aire_tube_rectangulaire(c, c, e)), "masse calculée (EN 10219, formé à froid)")
    for h, b, e in TUBES_RECTANGULAIRES:
        ajouter("tube_rectangulaire", f"Tube {_g(h)}×{_g(b)}×{_g(e)}", {"h": h, "b": b, "e": e}, masse_aire(aire_tube_rectangulaire(h, b, e)), "masse calculée (EN 10219, formé à froid)")
    for d, e in TUBES_RONDS:
        ajouter("tube_rond", f"Tube Ø{_g(d)}×{_g(e)}", {"d": d, "e": e}, masse_aire(math.pi * e * (d - e)), "masse calculée (EN 10220)")
    for largeur, e in PLATS:
        ajouter("plat", f"Plat {_g(largeur)}×{_g(e)}", {"l": largeur, "e": e}, masse_aire(largeur * e), "masse calculée (aire exacte)")
    for d in RONDS_PLEINS:
        ajouter("rond_plein", f"Rond Ø{_g(d)}", {"d": d}, masse_aire(math.pi * d * d / 4), "masse calculée (aire exacte)")
    for c in CARRES_PLEINS:
        ajouter("carre_plein", f"Carré {_g(c)}", {"c": c}, masse_aire(c * c), "masse calculée (aire exacte)")
    return resultat
