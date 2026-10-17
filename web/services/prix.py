"""Prix instantané du configurateur web : une pièce (forme paramétrée, forme décorative de la bibliothèque, pièce libre,
fichier déposé, ou profil / boîte pliés), une tôle vendue en
ligne et une quantité donnent un prix HT, sans rien enregistrer.

Le calcul reprend les briques du chiffrage des devis, pour que le prix en ligne et le prix d'un devis ne divergent pas :
- contour : bibliothèque de formes (decoupe.services.formes), ou flan développé d'une pièce pliée (rectangle pour un
  profil, flan aux coins crantés pour une boîte) ;
- procédé : laser si un paramètre de coupe existe à cette épaisseur exacte, sinon jet d'eau ;
- matière : imbrication rapide (rectangles, sans imbrication en vraie forme) dans chaque format de la tôle, le moins cher ;
- découpe : temps estimé par pièce + mise en place par tôle, au taux horaire du poste du paramètre de coupe ;
- pliage : réglages + plis (temps de série dès 10 pièces), au taux horaire du poste de la plieuse ;
- prix : coût de revient × (1 + marge web). Le minimum de commande s'applique au panier, pas à la pièce.

Statut : « ok » (prix ferme), « validation » (prix affiché, commande vérifiée par un technicien avant lancement),
« refus » (pas de prix, demande de devis)."""

import datetime
import math
from dataclasses import dataclass, field
from decimal import Decimal

from comptes.montants import D, arrondir


class ErreurPrixWeb(Exception):
    pass


@dataclass
class PrixWeb:
    statut: str = "ok"
    refus: list = field(default_factory=list)
    validations: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    modifications: list = field(default_factory=list)  # ce que l'atelier change à la pièce du client (montré au client)
    designation: str = ""
    matiere: str = ""
    epaisseur_mm: float = None
    procede: str = ""
    quantite: int = 0
    largeur_mm: float = None
    hauteur_mm: float = None
    poids_kg: float = None
    contour: dict = None
    pliage: dict = None
    nb_feuilles: int = None
    format_tole: str = ""
    tole: str = ""  # référence de la tôle retenue (interne : reprise par le devis créé depuis la demande)
    lignes_pli: list = None  # interne : lignes de pli du flan (calque PLIAGE du DXF de fabrication)
    cout_matiere: Decimal = None
    cout_decoupe: Decimal = None
    cout_pliage: Decimal = None
    cout_revient: Decimal = None
    taux_marge_pct: Decimal = None
    prix_total_ht: Decimal = None
    prix_unitaire_ht: Decimal = None
    taux_tva: Decimal = None
    prix_total_ttc: Decimal = None
    prix_unitaire_ttc: Decimal = None
    minimum_commande_ht: Decimal = None

    def finaliser(self):
        self.statut = "refus" if self.refus else "validation" if self.validations else "ok"
        if self.refus:
            self.prix_total_ht = self.prix_unitaire_ht = self.prix_total_ttc = self.prix_unitaire_ttc = None
        return self

    def en_dict(self):
        return {k: (float(v) if isinstance(v, Decimal) else v) for k, v in self.__dict__.items()}

    def en_dict_public(self):
        """Ce que voit le client : ni coûts de revient, ni marge, ni notes internes, ni outillage."""
        d = self.en_dict()
        public = {k: d[k] for k in PUBLIC}
        if self.pliage:
            public["pliage"] = {k: self.pliage.get(k) for k in ("nb_plis", "angle_mini", "developpe_mm", "flan_mm")}
        return public


PUBLIC = ("statut", "refus", "validations", "modifications", "designation", "matiere", "epaisseur_mm", "procede", "quantite", "largeur_mm", "hauteur_mm",
          "poids_kg", "contour", "pliage", "prix_total_ht", "prix_unitaire_ht", "taux_tva", "prix_total_ttc", "prix_unitaire_ttc",
          "minimum_commande_ht")


def taux_tva():
    """Taux de TVA du prix TTC affiché en ligne : le taux coché « par défaut » dans le référentiel, sinon 20 %."""
    from commercial.models import TauxTVA

    defaut = TauxTVA.objects.filter(est_defaut=True).first()
    return D(defaut.taux) if defaut else D("20")


def ttc(montant_ht, taux):
    return arrondir(D(montant_ht) * (1 + D(taux) / 100))


def toles_en_ligne():
    from technique.models import Article

    return (
        Article.objects.filter(nature=Article.Nature.MATIERE_PREMIERE, vendu_en_ligne=True, matiere__isnull=False, epaisseur__isnull=False, format_tole__isnull=False)
        .select_related("matiere", "matiere__famille", "format_tole")
        .order_by("matiere__nom", "epaisseur", "reference")
    )


ECHELLE_MIN, ECHELLE_MAX = 0.01, 100


def _echelle(valeur):
    """Facteur d'agrandissement d'un fichier déposé (1 par défaut), arrondi au dix-millième."""
    if valeur in (None, ""):
        return 1
    try:
        k = round(float(valeur), 4)
    except (TypeError, ValueError):
        raise ErreurPrixWeb("Échelle : un nombre est attendu.")
    if not ECHELLE_MIN <= k <= ECHELLE_MAX:
        raise ErreurPrixWeb(f"Échelle entre {ECHELLE_MIN * 100:g} % et {ECHELLE_MAX * 100:g} %.")
    return k


def formats_fichier():
    """Formats de fichier que le site peut proposer : le DWG seulement si un convertisseur est installé, le STEP si
    OpenCascade l'est."""
    from .dxf import dwg_lisible

    from .step import lisible

    return ["dxf"] + (["dwg"] if dwg_lisible() else []) + ["svg"] + (["step", "stp"] if lisible() else [])


def catalogue():
    """Ce que le configurateur peut proposer : matières et épaisseurs vendues en ligne, formes, pliage, grille web."""
    from decoupe.services import formes

    from web.models import ReglageWeb

    from .bibliotheque import resume as resume_bibliotheque

    matieres = {}
    for tole in toles_en_ligne():
        m = matieres.setdefault(tole.matiere_id, {"matiere": tole.matiere.nom, "famille": str(tole.matiere.famille or ""), "epaisseurs": []})
        if tole.epaisseur not in m["epaisseurs"]:
            m["epaisseurs"].append(tole.epaisseur)
    reglage = ReglageWeb.charger()
    return {
        "matieres": list(matieres.values()),
        "formes": [f for f in formes.catalogue()["familles"] if not f["normalisee"]],  # cotes normalisées : sur devis
        "pliage": {
            "formes": ["profil", "boite"],
            "aide": "Cotes extérieures. Profil : faces et angles (90 = équerre, signe = sens du pli). Boîte : fond et bords A, B, C, D.",
        },
        "minimum_commande_ht": float(reglage.minimum_commande_ht),
        "taux_tva": float(taux_tva()),
        "quantite_max": reglage.quantite_max,
        "formats_fichier": formats_fichier(),
        "bibliotheque": resume_bibliotheque(),
    }


def _contour_flan_boite(x, y, crans):
    """Flan de boîte x × y, coins crantés : crans [A (bas), B (droite), C (haut), D (gauche)]."""
    a, b, c, d = crans
    points = [(d, 0), (x - b, 0), (x - b, a), (x, a), (x, y - c), (x - b, y - c), (x - b, y), (d, y), (d, y - c), (0, y - c), (0, a), (d, a)]
    sans_doublons = [p for i, p in enumerate(points) if p != points[i - 1]]
    return [(round(px, 4), round(py, 4)) for px, py in sans_doublons]


def _piece(matiere, epaisseur, procede, exterieur, trous=()):
    """Pièce à découper non enregistrée, avec ce dont ont besoin le temps de découpe et l'imbrication."""
    from shapely.geometry import Polygon

    from decoupe.models import PieceDecoupe
    from decoupe.services.matiere import bord_tole_mm

    poly = Polygon(exterieur, list(trous))
    minx, miny, maxx, maxy = poly.bounds
    return PieceDecoupe(
        nom="Configurateur web", matiere=matiere, epaisseur=epaisseur, procede=procede, statut=PieceDecoupe.Statut.OK,
        contour_json={"exterieur": [list(p) for p in exterieur], "trous": [[list(p) for p in t] for t in trous], "autres": []},
        surface_mm2=poly.area, perimetre_decoupe_mm=poly.exterior.length + sum(i.length for i in poly.interiors),
        largeur_mm=maxx - minx, hauteur_mm=maxy - miny, nb_contours_interieurs=len(trous),
        marge_bord_mm=bord_tole_mm(matiere, epaisseur, procede), taux_chute_recuperable=0, imbrication_forme=False,
    )


def _procede(matiere, epaisseur):
    from decoupe.models import ProcedeCoupe
    from decoupe.services.parametres import meilleur_parametre

    if meilleur_parametre(matiere, epaisseur, ProcedeCoupe.LASER) is not None:
        return ProcedeCoupe.LASER
    return ProcedeCoupe.JET_EAU


def controle_trous(exterieur, trous, epaisseur, procede):
    """Trous trop petits pour le procédé (Ø mini selon l'épaisseur, réglage du procédé) ou trop près du bord de la pièce :
    messages de validation technicien. Le Ø d'un trou est le petit côté de son rectangle englobant minimal."""
    from shapely.geometry import LineString, Polygon

    from decoupe.models import ProcedeCoupe, ReglageProcede

    reglage = ReglageProcede.pour(procede)
    mini, coef_bord = reglage.trou_mini(epaisseur), reglage.distance_trou_bord_coef
    nom = "jet d'eau" if procede == ProcedeCoupe.JET_EAU else "laser"
    bord = LineString(list(exterieur) + [exterieur[0]])
    petits, proches = [], []
    for t in trous:
        trou = Polygon(t)
        rect = trou.minimum_rotated_rectangle
        cotes = [LineString(rect.exterior.coords[i:i + 2]).length for i in range(2)] if rect.geom_type == "Polygon" else [0]
        diametre = min(cotes)
        if mini and diametre < mini - 1e-6:
            petits.append(diametre)
        if coef_bord and trou.exterior.distance(bord) < coef_bord * epaisseur - 1e-6:
            proches.append(trou.exterior.distance(bord))
    def mm(v):
        return f"{v:.1f}".rstrip("0").rstrip(".").replace(".", ",")

    messages = []
    if petits:
        liste = ", ".join(f"Ø{mm(d)}" for d in sorted(set(round(d, 1) for d in petits)))
        messages.append(f"Trou(s) {liste} mm : minimum Ø{mm(mini)} mm en {epaisseur:g} mm au {nom}, faisabilité vérifiée par l'atelier.")
    if proches:
        messages.append(f"Trou(s) à {mm(min(proches))} mm du bord : minimum {mm(coef_bord * epaisseur)} mm ({coef_bord:g} × l'épaisseur) "
                        f"au {nom}, faisabilité vérifiée par l'atelier.")
    return messages


def decharges(contour, epaisseur, bord_mini):
    """Trou trop près d'un pli (bord du trou à moins du bord mini de la ligne de pli) : oblong de décharge sur la ligne
    de pli, large de l'épaisseur et long du diamètre du trou (son étendue le long du pli), centré en face du trou, pour
    que le trou ne se déforme pas. Retourne (contour avec les oblongs, messages pour le client)."""
    from shapely.geometry import LineString, Point, Polygon
    from shapely.ops import unary_union

    trous = [Polygon(t) for t in contour["trous"]]
    oblongs, messages = [], []
    for pli in contour.get("plis") or []:
        ligne = LineString([pli["p1"], pli["p2"]])
        (x1, y1), (x2, y2) = pli["p1"], pli["p2"]
        longueur = math.dist((x1, y1), (x2, y2)) or 1
        ux, uy = (x2 - x1) / longueur, (y2 - y1) / longueur
        for trou in trous:
            distance = trou.distance(ligne)
            if distance >= bord_mini:
                continue
            le_long = [(px - x1) * ux + (py - y1) * uy for px, py in trou.exterior.coords]
            diametre = max(le_long) - min(le_long)
            centre = ligne.interpolate(ligne.project(trou.centroid))
            demi = max(diametre - epaisseur, 0) / 2
            axe = LineString([(centre.x - ux * demi, centre.y - uy * demi), (centre.x + ux * demi, centre.y + uy * demi)])
            oblongs.append(axe.buffer(epaisseur / 2) if demi else Point(centre.x, centre.y).buffer(epaisseur / 2))
            messages.append(f"{pli['nom']} : trou à {distance:.1f} mm de la ligne de pli (bord mini {bord_mini:.1f} mm), "
                         f"oblong de décharge {diametre:.1f} × {epaisseur:g} mm ajouté sur le pli pour qu'il ne se déforme pas.")
    if not oblongs:
        return contour, []
    piece = Polygon(contour["exterieur"], contour["trous"]).difference(unary_union(oblongs))
    if piece.geom_type == "MultiPolygon":
        piece = max(piece.geoms, key=lambda g: g.area)
    arrondi = lambda anneau: [(round(px, 4), round(py, 4)) for px, py in list(anneau.coords)[:-1]]  # noqa: E731
    return {**contour, "exterieur": arrondi(piece.exterior), "trous": [arrondi(i) for i in piece.interiors]}, messages


def _piece_step(r, fiche, matiere, epaisseur, echelle):
    """Pièce d'un fichier STEP : contrôle de l'épaisseur, pliage des plis lus dans le fichier, flan déplié avec l'outillage
    retenu. Retourne (contour, résultat de pliage ou None) ; contour None si le pliage est refusé."""
    from decoupe.services.pliage import calculer_plis

    from .step import flan_atelier

    if _echelle(echelle) != 1:
        raise ErreurPrixWeb("Une pièce en 3D (STEP) se fabrique à la taille du fichier.")
    e = fiche["epaisseur_mm"]
    if abs(e - epaisseur) > 0.05:
        raise ErreurPrixWeb(f"Votre pièce est en {e:g} mm d'épaisseur : choisissez {e:g} mm.")
    plis = fiche.get("plis") or []
    r.designation = f"Pièce du fichier {fiche['nom']}" + (f" ({len(plis)} pli{'s' if len(plis) > 1 else ''})" if plis else "")
    if fiche.get("avertissements"):
        r.validations.append("Fichier à vérifier par l'atelier : " + " ".join(fiche["avertissements"]))
    if not plis:
        return fiche["contour"], None
    resultat = calculer_plis(matiere, epaisseur, plis)
    if resultat.statut == "refus":
        return None, resultat
    ri = resultat.rayon_interieur
    grands = sorted({p["rayon_mm"] for p in plis if p["rayon_mm"] > 2 * ri + epaisseur})
    if grands:
        r.validations.append(f"Rayon de pli de {' / '.join(f'{v:g}' for v in grands)} mm dans le fichier (atelier : {ri:g} mm au vé) : "
                             "cintrage ou pli en plusieurs coups à valider.")
    contour = flan_atelier(fiche["jeton"], float(matiere.famille.coef_k), ri)
    if resultat.bord_mini and contour.get("plis"):
        contour, modifications = decharges(contour, epaisseur, resultat.bord_mini)
        r.modifications += modifications
    xs, ys = [p[0] for p in contour["exterieur"]], [p[1] for p in contour["exterieur"]]
    resultat.developpe_mm, resultat.flan_mm = None, [round(max(xs) - min(xs), 2), round(max(ys) - min(ys), 2)]
    return contour, resultat


def _preparer(matiere_nom, epaisseur, quantite, forme=None, cotes=None, pliage=None, libre=None, dxf=None, echelle=None, bibliotheque=None):
    """Pièce à chiffrer, avant matière et coûts : (résultat, contexte), contexte None si la pièce est déjà refusée.
    Voir `chiffrer` pour les arguments."""
    from decoupe.services import formes
    from decoupe.services.pliage import ErreurPliage, calculer_boite, calculer_pliage, lignes_pli_boite, lignes_pli_profil
    from decoupe.services.temps import ErreurTemps, estimer_temps_decoupe

    from web.models import ReglageWeb

    from .bibliotheque import ErreurBibliotheque, construire as construire_bibliotheque, largeur_mini
    from .dxf import ErreurDxf, charger
    from .libre import ErreurLibre, construire as construire_libre

    reglage = ReglageWeb.charger()
    try:
        quantite, epaisseur = int(quantite), float(epaisseur)
    except (TypeError, ValueError):
        raise ErreurPrixWeb("Quantité et épaisseur sont des nombres.")
    if quantite < 1:
        raise ErreurPrixWeb("La quantité est d'au moins une pièce.")
    toles = [t for t in toles_en_ligne() if t.matiere.nom == matiere_nom and abs(t.epaisseur - epaisseur) < 1e-6]
    if not toles:
        raise ErreurPrixWeb(f"{matiere_nom} en {epaisseur:g} mm n'est pas vendu en ligne.")
    matiere = toles[0].matiere
    r = PrixWeb(matiere=matiere.nom, epaisseur_mm=epaisseur, quantite=quantite, taux_marge_pct=reglage.taux_marge_pct,
                minimum_commande_ht=reglage.minimum_commande_ht)
    if quantite > reglage.quantite_max:
        r.refus.append(f"Au-delà de {reglage.quantite_max} pièces, demandez un devis.")
        return r.finaliser(), None

    # Contour à découper : forme plane, ou flan de la pièce pliée.
    resultat_pliage = None
    contour_libre = None
    try:
        if libre:
            contour_libre, r.designation, validations = construire_libre(libre, epaisseur)
            r.validations += validations
        elif bibliotheque:
            if not isinstance(bibliotheque, dict):
                raise ErreurPrixWeb("Forme de la bibliothèque : clé et largeur attendues.")
            contour_libre, r.designation = construire_bibliotheque(bibliotheque.get("cle"), bibliotheque.get("largeur"))
            mini = largeur_mini(bibliotheque["cle"], epaisseur)
            if float(bibliotheque["largeur"]) < mini:
                r.refus.append(f"En {epaisseur:g} mm, cette forme se découpe à partir de {mini} mm de large : "
                               "aucun trait ni jour ne doit être plus fin que l'épaisseur de la tôle.")
                return r.finaliser(), None
        elif dxf and charger(dxf).get("format") == "step":
            contour_libre, resultat_pliage = _piece_step(r, charger(dxf), matiere, epaisseur, echelle)
        elif dxf:
            fiche = charger(dxf)
            contour_libre = fiche["contour"]
            r.designation = f"Pièce du fichier {fiche['nom']}"
            k = _echelle(echelle)
            if k != 1:
                contour_libre = {"exterieur": [(x * k, y * k) for x, y in contour_libre["exterieur"]],
                                 "trous": [[(x * k, y * k) for x, y in t] for t in contour_libre["trous"]]}
                r.designation += f" à l'échelle {k * 100:g} %"
            if fiche.get("avertissements"):
                r.validations.append("Fichier à vérifier par l'atelier : " + " ".join(fiche["avertissements"]))
        elif pliage:
            if pliage.get("forme") == "boite":
                longueur, largeur = float(pliage["longueur"]), float(pliage["largeur"])
                resultat_pliage = calculer_boite(matiere, epaisseur, longueur, largeur, [float(h or 0) for h in pliage["bords"]],
                                                 [float(a) for a in pliage.get("angles") or []] or None)
                r.designation = f"Boîte {longueur:g} × {largeur:g}, bords " + " / ".join(f"{float(h or 0):g}" for h in pliage["bords"])
            else:
                faces, angles = [float(f) for f in pliage["faces"]], [float(a) for a in pliage["angles"]]
                if len(faces) < 2:
                    raise ErreurPrixWeb(f"Profil : {len(faces)} face saisie, il en faut au moins deux (ex. 50 ; 50 pour une cornière).")
                if len(angles) != len(faces) - 1:
                    raise ErreurPrixWeb(f"Profil : {len(faces)} faces demandent {len(faces) - 1} angle(s), {len(angles)} saisi(s) (ex. 90 ; 90 pour un U).")
                if any(f <= 0 for f in faces) or any(not 0 < abs(a) < 180 for a in angles):
                    raise ErreurPrixWeb("Profil : chaque face est positive et chaque angle compris entre 0 et 180° (90 = équerre).")
                longueur = float(pliage["longueur"])
                resultat_pliage = calculer_pliage(matiere, epaisseur, faces, angles, longueur)
                r.designation = f"Profil plié {' / '.join(f'{f:g}' for f in faces)} × {longueur:g}"
        elif forme:
            contour = formes.construire(forme, cotes or {})
            r.designation = formes.nom_suggere(forme, cotes or {})
        else:
            raise ErreurPrixWeb("Indiquez une forme ou une pièce pliée.")
    except (KeyError, TypeError, ValueError):
        raise ErreurPrixWeb("Cotes de pliage incomplètes ou non numériques.")
    except (formes.ErreurForme, ErreurLibre, ErreurDxf, ErreurBibliotheque) as exc:
        raise ErreurPrixWeb(str(exc))
    except ErreurPliage as exc:
        r.refus.append(str(exc))
        return r.finaliser(), None

    if resultat_pliage is not None:
        r.pliage = resultat_pliage.en_dict()
        r.refus += resultat_pliage.refus
        r.validations += resultat_pliage.validations
        if resultat_pliage.statut == "refus":
            return r.finaliser(), None
    if contour_libre is not None:
        exterieur, trous = contour_libre["exterieur"], contour_libre["trous"]
    elif resultat_pliage is not None:
        if resultat_pliage.flan_mm:
            exterieur = _contour_flan_boite(*resultat_pliage.flan_mm, resultat_pliage.crans_mm)
        else:
            d = resultat_pliage.developpe_mm
            exterieur = [(0, 0), (d, 0), (d, longueur), (0, longueur)]
        trous = []
    else:
        exterieur, trous = contour.exterieur, contour.trous

    if contour_libre is not None:
        r.lignes_pli = contour_libre.get("plis") or []
    elif resultat_pliage is not None:
        ri, k = resultat_pliage.rayon_interieur, float(matiere.famille.coef_k)
        if pliage.get("forme") == "boite":
            r.lignes_pli = lignes_pli_boite([float(h or 0) for h in pliage["bords"]], [float(a) for a in pliage.get("angles") or []] or None,
                                            epaisseur, ri, k, resultat_pliage.flan_mm, resultat_pliage.crans_mm)
        else:
            r.lignes_pli = lignes_pli_profil(faces, angles, epaisseur, ri, k, longueur)

    procede = _procede(matiere, epaisseur)
    r.procede = procede
    r.validations += controle_trous(exterieur, trous, epaisseur, procede)
    piece = _piece(matiere, epaisseur, procede, exterieur, trous)
    r.largeur_mm, r.hauteur_mm = round(piece.largeur_mm, 2), round(piece.hauteur_mm, 2)
    r.contour = piece.contour_json
    r.poids_kg = round(piece.surface_mm2 * epaisseur * matiere.densite / 1e6, 3)

    try:
        temps = estimer_temps_decoupe(piece)
    except ErreurTemps as exc:
        r.refus.append(str(exc))
        return r.finaliser(), None
    r.notes += temps.avertissements
    return r, {"piece": piece, "temps": temps, "toles": toles, "matiere": matiere, "quantite": quantite, "reglage": reglage,
               "pliage": resultat_pliage}


def chiffrer(matiere_nom, epaisseur, quantite, forme=None, cotes=None, pliage=None, libre=None, dxf=None, echelle=None,
             bibliotheque=None, aujourdhui=None):
    """Prix instantané. `forme` + `cotes` : pièce plane de la bibliothèque. `pliage` : {"forme": "profil", "faces": [...],
    "angles": [...], "longueur": L} ou {"forme": "boite", "longueur": L, "largeur": l, "bords": [A, B, C, D], "angles": [...]}.
    `libre` : recette d'une pièce libre (web.services.libre). `dxf` : jeton d'un fichier déposé (web.services.dxf),
    agrandi ou réduit par `echelle` (1 = taille du fichier). `bibliotheque` : {"cle": forme décorative, "largeur": mm}
    (web.services.bibliotheque), refusée en dessous de sa largeur mini à cette épaisseur.
    Lève ErreurPrixWeb pour une demande mal formée ; une pièce infaisable donne un résultat « refus »."""
    from chiffrage.moteur import ChiffrageError, cout_etape_gamme
    from chiffrage.reglage import minutes_mise_en_place
    from decoupe.models import Plieuse
    from decoupe.services.matiere import ErreurMatiere, cout_matiere_imbrication
    from technique.models import Gamme

    aujourdhui = aujourdhui or datetime.date.today()
    r, ctx = _preparer(matiere_nom, epaisseur, quantite, forme=forme, cotes=cotes, pliage=pliage, libre=libre, dxf=dxf, echelle=echelle,
                       bibliotheque=bibliotheque)
    if ctx is None:
        return r
    piece, temps, toles, quantite, reglage, resultat_pliage = (ctx[k] for k in ("piece", "temps", "toles", "quantite", "reglage", "pliage"))
    parametre = temps.parametre

    # Matière : le format le moins cher parmi les tôles vendues en ligne de cette matière et de cette épaisseur.
    meilleur, erreurs = None, []
    for tole in toles:
        try:
            cout = cout_matiere_imbrication(piece, quantite, tole=tole, format_tole=tole.format_tole)
        except ErreurMatiere as exc:
            erreurs.append(str(exc))
            continue
        if meilleur is None or cout.cout_total < meilleur[1].cout_total:
            meilleur = (tole, cout)
    if meilleur is None:
        r.refus.append(erreurs[0] if erreurs else "Aucune tôle ne convient.")
        return r.finaliser()
    tole, cout = meilleur
    r.nb_feuilles, r.format_tole, r.cout_matiere = cout.nb_feuilles, str(tole.format_tole), cout.cout_total
    r.tole = tole.pk

    try:
        if parametre.poste_id is None:
            raise ChiffrageError(f"Le paramètre de coupe « {parametre} » n'a pas de poste de travail.")
        decoupe = Gamme(poste=parametre.poste, temps_fixe=cout.nb_feuilles * minutes_mise_en_place(parametre), temps_variable=temps.total_min)
        r.cout_decoupe = cout_etape_gamme(decoupe, quantite, aujourdhui)
        r.cout_pliage = D(0)
        if resultat_pliage is not None:
            plieuse = Plieuse.objects.select_related("poste").get(pk=resultat_pliage.plieuse_id)
            if plieuse.poste is None:
                raise ChiffrageError(f"La plieuse « {plieuse} » n'a pas de poste de travail.")
            etape = Gamme(poste=plieuse.poste, temps_fixe=resultat_pliage.temps_reglage_min,
                          temps_variable=resultat_pliage.temps_pli_min, origine="pliage")
            r.cout_pliage = cout_etape_gamme(etape, quantite, aujourdhui)
    except ChiffrageError as exc:
        # Paramétrage incomplet côté atelier : pas de prix en ligne, la demande part en devis.
        r.refus.append("Prix indisponible en ligne pour cette pièce : demandez un devis.")
        r.notes.append(f"Paramétrage incomplet : {exc}")
        return r.finaliser()

    r.cout_revient = arrondir(r.cout_matiere + r.cout_decoupe + r.cout_pliage)
    r.prix_total_ht = arrondir(r.cout_revient * (1 + D(reglage.taux_marge_pct) / 100))
    r.prix_unitaire_ht = arrondir(r.prix_total_ht / quantite)
    r.taux_tva = taux_tva()
    r.prix_total_ttc = ttc(r.prix_total_ht, r.taux_tva)
    r.prix_unitaire_ttc = ttc(r.prix_unitaire_ht, r.taux_tva)
    return r.finaliser()


@dataclass
class PrixJeu:
    """Prix d'un jeu de pièces découpées ensemble dans la même tôle (un dessin vectorisé en plusieurs pièces) : une seule
    imbrication, le temps de coupe cumulé et une seule mise en place par tôle. `pieces` : le résultat de chaque pièce, avec
    sa part du prix du jeu (ou {"erreur"} pour une demande mal formée)."""
    statut: str = "ok"
    refus: list = field(default_factory=list)
    validations: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    pieces: list = field(default_factory=list)
    matiere: str = ""
    epaisseur_mm: float = None
    procede: str = ""
    quantite: int = 0
    poids_kg: float = None
    nb_feuilles: int = None
    format_tole: str = ""
    tole: str = ""
    taux_utilisation_pct: float = None
    cout_matiere: Decimal = None
    cout_decoupe: Decimal = None
    cout_pliage: Decimal = None
    cout_revient: Decimal = None
    taux_marge_pct: Decimal = None
    prix_total_ht: Decimal = None
    prix_unitaire_ht: Decimal = None
    taux_tva: Decimal = None
    prix_total_ttc: Decimal = None
    prix_unitaire_ttc: Decimal = None
    minimum_commande_ht: Decimal = None

    def finaliser(self):
        for i, p in enumerate(self.pieces, start=1):
            if isinstance(p, dict):
                self.refus.append(f"Pièce {i} : {p['erreur']}")
            else:
                self.refus += [f"Pièce {i} : {m}" for m in p.refus]
                self.validations += [f"Pièce {i} : {m}" for m in p.validations]
        self.statut = "refus" if self.refus else "validation" if self.validations else "ok"
        if self.refus:
            self.prix_total_ht = self.prix_unitaire_ht = self.prix_total_ttc = self.prix_unitaire_ttc = None
            for p in self.pieces:
                if not isinstance(p, dict):
                    p.prix_total_ht = p.prix_unitaire_ht = p.prix_total_ttc = p.prix_unitaire_ttc = None
        return self

    def en_dict_public(self):
        d = {k: (float(v) if isinstance(v, Decimal) else v) for k, v in self.__dict__.items() if k in PUBLIC_JEU}
        d["pieces"] = [p if isinstance(p, dict) else p.en_dict_public() for p in self.pieces]
        return d


PUBLIC_JEU = ("statut", "refus", "validations", "matiere", "epaisseur_mm", "procede", "quantite", "poids_kg", "nb_feuilles",
              "taux_utilisation_pct", "prix_total_ht", "prix_unitaire_ht", "taux_tva", "prix_total_ttc", "prix_unitaire_ttc",
              "minimum_commande_ht")


def _matiere_jeu(pieces, quantite, tole):
    """Coût matière de `quantite` jeux : toutes les pièces imbriquées ensemble dans le format de la tôle, même calcul que
    decoupe.services.matiere.cout_matiere_imbrication (dernière feuille comptée jusqu'à la bande entamée)."""
    from decoupe.services.imbrication import ItemANester, etendue_derniere_feuille_mm, imbriquer_meilleur
    from decoupe.services.matiere import ErreurMatiere, espacement_pieces_mm, prix_au_mm2

    f = tole.format_tole
    marge = max(p.marge_bord_mm for p in pieces)
    items = [ItemANester(piece_id=i, largeur_mm=p.largeur_mm, hauteur_mm=p.hauteur_mm, surface_mm2=p.surface_mm2, quantite=quantite,
                         pas_rotation_deg=p.pas_rotation_deg, symetrie_autorisee=p.symetrie_autorisee,
                         exterieur=p.contour_json["exterieur"], trous=p.contour_json["trous"])
             for i, p in enumerate(pieces, start=1)]
    resultat = imbriquer_meilleur(items, f.largeur_mm, f.longueur_mm, marge_bord_mm=marge,
                                  espacement_pieces_mm=max(espacement_pieces_mm(p) for p in pieces))
    if resultat.pieces_non_placees:
        raise ErreurMatiere(f"Une pièce est trop grande pour la tôle {f} (marges de bord comprises).")
    surface_feuille = f.largeur_mm * f.longueur_mm
    etendue = min(etendue_derniere_feuille_mm(resultat, marge), f.longueur_mm)
    consommee = min(max(resultat.nb_feuilles - 1, 0) * surface_feuille + f.largeur_mm * etendue, resultat.nb_feuilles * surface_feuille)
    chutes = max(consommee - resultat.surface_pieces_mm2, 0.0)
    facturee = resultat.surface_pieces_mm2 + chutes * float(D(1) - D(pieces[0].taux_chute_recuperable) / 100)
    return resultat, arrondir(D(facturee) * prix_au_mm2(tole, f.largeur_mm, f.longueur_mm))


def chiffrer_jeu(demandes, aujourdhui=None):
    """Prix d'un jeu de pièces de la même tôle (même matière, épaisseur et quantité de jeux), découpées ensemble : mini
    imbrication de toutes les pièces dans chaque format vendu en ligne (le moins cher est retenu), temps de coupe cumulé,
    mise en place comptée une fois par tôle. Le prix du jeu est réparti sur les pièces (matière au prorata de la surface,
    découpe au prorata du temps de coupe) ; la somme des parts vaut le prix du jeu.
    Lève ErreurPrixWeb si le jeu est mal formé ; une pièce mal formée ou infaisable met le jeu en « refus »."""
    from chiffrage.moteur import ChiffrageError, cout_etape_gamme
    from chiffrage.reglage import minutes_mise_en_place
    from decoupe.models import Plieuse
    from decoupe.services.matiere import ErreurMatiere
    from technique.models import Gamme

    aujourdhui = aujourdhui or datetime.date.today()
    if not demandes:
        raise ErreurPrixWeb("Le jeu ne contient aucune pièce.")
    communs = {(str(d.get("matiere")), str(d.get("epaisseur")), str(d.get("quantite"))) for d in demandes}
    if len(communs) > 1:
        raise ErreurPrixWeb("Les pièces d'un même jeu partagent la matière, l'épaisseur et la quantité.")
    j = PrixJeu()
    contextes = []
    for d in demandes:
        try:
            r, ctx = _preparer(d.get("matiere"), d.get("epaisseur"), d.get("quantite"), forme=d.get("forme"), cotes=d.get("cotes"),
                               pliage=d.get("pliage"), libre=d.get("libre"), dxf=d.get("dxf"), echelle=d.get("echelle"),
                               bibliotheque=d.get("bibliotheque"))
        except ErreurPrixWeb as exc:
            j.pieces.append({"erreur": str(exc)})
            continue
        j.pieces.append(r)
        contextes.append(ctx)
        j.matiere, j.epaisseur_mm, j.quantite = r.matiere, r.epaisseur_mm, r.quantite
        j.taux_marge_pct, j.minimum_commande_ht = r.taux_marge_pct, r.minimum_commande_ht
    if len(contextes) < len(j.pieces) or any(c is None for c in contextes):
        return j.finaliser()

    pieces = [c["piece"] for c in contextes]
    quantite, toles = contextes[0]["quantite"], contextes[0]["toles"]
    j.procede = pieces[0].procede
    j.poids_kg = round(sum(p.poids_kg for p in j.pieces), 3)

    meilleur, erreurs = None, []
    for tole in toles:
        try:
            resultat, cout = _matiere_jeu(pieces, quantite, tole)
        except ErreurMatiere as exc:
            erreurs.append(str(exc))
            continue
        if meilleur is None or cout < meilleur[2]:
            meilleur = (tole, resultat, cout)
    if meilleur is None:
        j.refus.append(erreurs[0] if erreurs else "Aucune tôle ne convient.")
        return j.finaliser()
    tole, resultat, j.cout_matiere = meilleur
    j.nb_feuilles, j.format_tole, j.taux_utilisation_pct = resultat.nb_feuilles, str(tole.format_tole), resultat.taux_utilisation_pct
    j.tole = tole.pk

    temps = [c["temps"] for c in contextes]
    parametre = temps[0].parametre
    couts_pliage = []
    try:
        if parametre.poste_id is None:
            raise ChiffrageError(f"Le paramètre de coupe « {parametre} » n'a pas de poste de travail.")
        decoupe = Gamme(poste=parametre.poste, temps_fixe=resultat.nb_feuilles * minutes_mise_en_place(parametre),
                        temps_variable=sum(t.total_min for t in temps))
        j.cout_decoupe = cout_etape_gamme(decoupe, quantite, aujourdhui)
        for c in contextes:
            pli = c["pliage"]
            if pli is None:
                couts_pliage.append(D(0))
                continue
            plieuse = Plieuse.objects.select_related("poste").get(pk=pli.plieuse_id)
            if plieuse.poste is None:
                raise ChiffrageError(f"La plieuse « {plieuse} » n'a pas de poste de travail.")
            etape = Gamme(poste=plieuse.poste, temps_fixe=pli.temps_reglage_min, temps_variable=pli.temps_pli_min, origine="pliage")
            couts_pliage.append(cout_etape_gamme(etape, quantite, aujourdhui))
    except ChiffrageError as exc:
        j.refus.append("Prix indisponible en ligne pour ce jeu de pièces : demandez un devis.")
        j.notes.append(f"Paramétrage incomplet : {exc}")
        return j.finaliser()
    j.cout_pliage = sum(couts_pliage, D(0))

    coef = 1 + D(j.taux_marge_pct) / 100
    j.cout_revient = arrondir(j.cout_matiere + j.cout_decoupe + j.cout_pliage)
    j.prix_total_ht = arrondir(j.cout_revient * coef)
    j.prix_unitaire_ht = arrondir(j.prix_total_ht / quantite)
    j.taux_tva = taux_tva()
    j.prix_total_ttc, j.prix_unitaire_ttc = ttc(j.prix_total_ht, j.taux_tva), ttc(j.prix_unitaire_ht, j.taux_tva)

    # Part de chaque pièce : matière au prorata de la surface, découpe au prorata du temps ; l'arrondi va à la plus grande.
    surface = sum(p.surface_mm2 for p in pieces) or 1
    minutes = sum(t.total_min for t in temps) or 1
    for r, p, t, pli in zip(j.pieces, pieces, temps, couts_pliage):
        r.nb_feuilles, r.format_tole, r.tole = j.nb_feuilles, j.format_tole, j.tole
        r.cout_matiere = arrondir(j.cout_matiere * D(p.surface_mm2 / surface))
        r.cout_decoupe = arrondir(j.cout_decoupe * D(t.total_min / minutes))
        r.cout_pliage = pli
        r.cout_revient = arrondir(r.cout_matiere + r.cout_decoupe + r.cout_pliage)
        r.prix_total_ht = arrondir(r.cout_revient * coef)
    plus_grande = max(range(len(pieces)), key=lambda i: pieces[i].surface_mm2)
    j.pieces[plus_grande].prix_total_ht += j.prix_total_ht - sum(r.prix_total_ht for r in j.pieces)
    for r in j.pieces:
        r.prix_unitaire_ht = arrondir(r.prix_total_ht / quantite)
        r.taux_tva = j.taux_tva
        r.prix_total_ttc, r.prix_unitaire_ttc = ttc(r.prix_total_ht, r.taux_tva), ttc(r.prix_unitaire_ht, r.taux_tva)
        r.finaliser()
    return j.finaliser()
