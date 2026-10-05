"""Vitesses de coupe calculées depuis l'usinabilité de la matière et l'épaisseur (jet d'eau), pour les matières et
épaisseurs que l'on n'a pas relevées dans le logiciel de la machine.

**Vitesse élevée** : V = K × g(qualité) × U^a / e^b, U = usinabilité, e = épaisseur (mm). Les trois tables relevées
(acier 10 mm U = 87, cuivre 8 mm U = 110, aluminium 20 mm U = 220 ; 15 valeurs) suivent toutes la même courbe
g(qualité). Les constantes (K, a, b), le rayon de pleine vitesse (proportionnel à l'épaisseur), le facteur de vitesse
en courbe et le facteur de perçage sont **calés sur neuf temps de découpe réels** (qualité 3) : une pièce arrondie de
4,5 m de contour en aluminium 10 et 30 mm, inox 20 mm, cuivre 15 mm et acier 5 mm ; une plaque de 1,1 m de coupe,
surtout en lignes droites, en acier 25 et 35 mm et en aluminium 50 mm ; un plan de 20 m de coupe (54 cercles) en inox
25 mm. Le modèle les retrouve à 1,2 % près au maximum.

**Vitesse basse** : rapport vitesse basse / vitesse élevée relevé pour 8, 10 et 20 mm, interpolé sur le logarithme
de l'épaisseur (et prolongé tel quel au-delà) — approximatif : les vitesses basses du logiciel dépendent d'autres
réglages que l'on ne connaît pas. Paliers = ⌈0,3 × e⌉ et distances d'accélération/décélération = 0,3 × e, comme sur
les trois relevés.

Les paramètres produits par ces formules sont marqués « calculé » : ils ne remplacent jamais ceux relevés."""

import math

K_VITESSE = 33.569
EXPOSANT_USINABILITE = 1.0912
EXPOSANT_EPAISSEUR = 1.0988
QUALITES = (1.5, 2.0, 3.0, 4.0, 5.0)
FACTEUR_QUALITE = {1.5: 1.0, 2.0: 0.7184, 3.0: 0.4507, 4.0: 0.3238, 5.0: 0.2505}
# Vitesse basse / vitesse élevée, par épaisseur relevée (mm), pour chaque qualité (1,5 → 5).
RAPPORT_BAS = {
    8: (0.377, 0.451, 0.556, 0.627, 0.679),
    10: (0.345, 0.416, 0.521, 0.595, 0.648),
    20: (0.239, 0.299, 0.396, 0.470, 0.528),
}

# Usinabilités standard (valeur fournie, mots-clés du nom de matière reconnus — sans accents, en minuscules).
USINABILITES_STANDARD = [
    ("Acier trempé", 80.4, ("trempe", "hardened")),
    ("Inox", 81.9, ("inox", "stainless")),
    ("Acier standard", 87.6, ("acier", "steel", "fer", "s235", "s355")),
    ("Cuivre / Laiton", 110.0, ("cuivre", "copper", "laiton", "brass", "bronze")),
    ("Titane", 115.0, ("titane", "titanium")),
    ("Alliage de zinc", 136.0, ("zinc",)),
    ("Aluminium", 213.0, ("alu", "aluminium", "aluminum")),
    ("Granit", 322.0, ("granit",)),
    ("Marbre", 535.0, ("marbre", "marble")),
    ("Nylon", 538.0, ("nylon",)),
    ("Plexiglas", 690.0, ("plexi", "acrylique", "pmma")),
    ("Graphite", 879.0, ("graphite",)),
    ("Polypropylène", 985.0, ("polypropylene", "polypropylène")),
]


def _normaliser(texte):
    import unicodedata

    return "".join(c for c in unicodedata.normalize("NFD", texte.lower()) if unicodedata.category(c) != "Mn")


def usinabilite_standard_pour(nom_matiere):
    """Usinabilité standard déduite du nom de la matière (mots-clés les plus spécifiques d'abord), ou None."""
    nom = _normaliser(nom_matiere)
    for _titre, valeur, mots in USINABILITES_STANDARD:  # l'ordre place « trempé » et « inox » avant « acier »
        if any(_normaliser(m) in nom for m in mots):
            return valeur
    return None


def _rapport_bas(epaisseur_mm, indice_qualite):
    epaisseurs = sorted(RAPPORT_BAS)
    if epaisseur_mm <= epaisseurs[0]:
        return RAPPORT_BAS[epaisseurs[0]][indice_qualite]
    if epaisseur_mm >= epaisseurs[-1]:
        return RAPPORT_BAS[epaisseurs[-1]][indice_qualite]
    for bas, haut in zip(epaisseurs, epaisseurs[1:]):
        if bas <= epaisseur_mm <= haut:
            part = math.log(epaisseur_mm / bas) / math.log(haut / bas)
            return RAPPORT_BAS[bas][indice_qualite] * (1 - part) + RAPPORT_BAS[haut][indice_qualite] * part


def vitesses_depuis_usinabilite(usinabilite, epaisseur_mm):
    """[{qualite, vitesse_haute_mm_min, vitesse_basse_mm_min, paliers, distance_*_mm}] pour les 5 niveaux de qualité."""
    if not usinabilite or usinabilite <= 0 or not epaisseur_mm or epaisseur_mm <= 0:
        raise ValueError("Usinabilité et épaisseur positives requises.")
    base = K_VITESSE * usinabilite ** EXPOSANT_USINABILITE / epaisseur_mm ** EXPOSANT_EPAISSEUR
    resultat = []
    for i, qualite in enumerate(QUALITES):
        haute = base * FACTEUR_QUALITE[qualite]
        basse = haute * _rapport_bas(epaisseur_mm, i)
        resultat.append({
            "qualite": qualite, "vitesse_haute_mm_min": round(haute, 1), "vitesse_basse_mm_min": round(basse, 1),
            "paliers": max(1, math.ceil(0.3 * epaisseur_mm - 1e-9)),
            "distance_acceleration_mm": round(0.3 * epaisseur_mm, 2), "distance_deceleration_mm": round(0.3 * epaisseur_mm, 2),
        })
    return resultat


# Réglages de perçage proportionnels à l'épaisseur (constaté sur les relevés : 10 s en acier 10 mm, 20 s en aluminium 20 mm…).
CHAMPS_PROPORTIONNELS = (
    "percage_stationnaire_hp_s", "percage_stationnaire_bp_s", "percement_lineaire_mm", "chevauchement_mm",
    "percage_circulaire_hp_tours", "percage_circulaire_bp_tours",
)
