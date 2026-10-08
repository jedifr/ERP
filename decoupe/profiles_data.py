"""Sections de profilés livrées avec l'application (table `ProfileSection`), toutes « non vérifiées ».

Cornières à ailes égales (EN 10056-1) et UPN (EN 10279) : masses linéiques du tableau usuel. Tubes carrés, rectangulaires et
ronds : masse calculée à partir des cotes (acier 7,85 kg/dm³, angles vifs), un peu supérieure à celle du catalogue du
fournisseur, qui a des angles arrondis. À remplacer par la base des profilés utilisés (et leurs prix d'achat) quand elle est
fournie : administration > Sections de profilés."""

import math

DENSITE = 7.85  # kg/dm³

# (côté, épaisseur, kg/m)
CORNIERES = [
    (20, 3, 0.882), (25, 3, 1.12), (30, 3, 1.36), (30, 4, 1.78), (35, 4, 2.09), (40, 4, 2.42), (40, 5, 2.97), (45, 5, 3.38),
    (50, 5, 3.77), (50, 6, 4.47), (60, 6, 5.42), (60, 8, 7.09), (70, 7, 7.38), (80, 8, 9.63), (90, 9, 12.2), (100, 10, 15.1), (120, 12, 21.6),
]
# (hauteur, largeur, âme, semelle, kg/m)
UPN = [
    (50, 38, 5, 7, 5.59), (65, 42, 5.5, 7.5, 7.09), (80, 45, 6, 8, 8.64), (100, 50, 6, 8.5, 10.6), (120, 55, 7, 9, 13.4),
    (140, 60, 7, 10, 16.0), (160, 65, 7.5, 10.5, 18.8), (180, 70, 8, 11, 22.0), (200, 75, 8.5, 11.5, 25.3), (220, 80, 9, 12.5, 29.4),
    (240, 85, 9.5, 13, 33.2), (260, 90, 10, 14, 37.9), (280, 95, 10, 15, 41.8), (300, 100, 10, 16, 46.2),
]
TUBES_CARRES = [(20, 2), (25, 2), (30, 2), (30, 3), (40, 2), (40, 3), (40, 4), (50, 3), (50, 4), (60, 3), (60, 4), (60, 5), (80, 4), (80, 5), (100, 4), (100, 5), (120, 5), (150, 6)]
TUBES_RECTANGULAIRES = [
    (40, 20, 2), (40, 25, 2), (50, 30, 2), (50, 30, 3), (60, 30, 3), (60, 40, 3), (80, 40, 3), (80, 40, 4), (80, 60, 4), (100, 50, 3), (100, 50, 4),
    (100, 60, 4), (120, 60, 4), (120, 80, 5), (150, 100, 5), (200, 100, 6),
]
# (diamètre extérieur, épaisseur)
TUBES_RONDS = [(21.3, 2.6), (26.9, 2.6), (33.7, 3.2), (42.4, 3.2), (48.3, 3.2), (60.3, 3.6), (76.1, 3.6), (88.9, 4.0), (114.3, 4.5), (139.7, 5.0), (168.3, 5.0), (219.1, 6.3)]


def _g(x):
    return f"{x:g}"


def lignes():
    """(famille, désignation, cotes, masse linéique kg/m, source, ordre)."""
    resultat = []
    ordre = 0

    def ajouter(famille, designation, cotes, masse, source):
        nonlocal ordre
        ordre += 1
        resultat.append((famille, designation, cotes, round(masse, 3), source, ordre))

    for c, e, m in CORNIERES:
        ajouter("corniere", f"L {_g(c)}×{_g(c)}×{_g(e)}", {"a": c, "b": c, "e": e}, m, "EN 10056-1, masse du tableau usuel")
    for h, b, tw, tf, m in UPN:
        ajouter("upn", f"UPN {_g(h)}", {"h": h, "b": b, "tw": tw, "tf": tf}, m, "EN 10279, masse du tableau usuel")
    for c, e in TUBES_CARRES:
        ajouter("tube_carre", f"Tube {_g(c)}×{_g(c)}×{_g(e)}", {"c": c, "e": e}, 4 * e * (c - e) * DENSITE / 1000, "masse calculée, angles vifs")
    for h, b, e in TUBES_RECTANGULAIRES:
        ajouter("tube_rectangulaire", f"Tube {_g(h)}×{_g(b)}×{_g(e)}", {"h": h, "b": b, "e": e}, 2 * e * (h + b - 2 * e) * DENSITE / 1000, "masse calculée, angles vifs")
    for d, e in TUBES_RONDS:
        ajouter("tube_rond", f"Tube Ø{_g(d)}×{_g(e)}", {"d": d, "e": e}, math.pi * e * (d - e) * DENSITE / 1000, "masse calculée (EN 10220)")
    return resultat
