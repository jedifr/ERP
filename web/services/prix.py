"""Prix instantané du configurateur web : une pièce (forme de la bibliothèque, ou profil / boîte pliés), une tôle vendue en
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
    cout_matiere: Decimal = None
    cout_decoupe: Decimal = None
    cout_pliage: Decimal = None
    cout_revient: Decimal = None
    taux_marge_pct: Decimal = None
    prix_total_ht: Decimal = None
    prix_unitaire_ht: Decimal = None
    minimum_commande_ht: Decimal = None

    def finaliser(self):
        self.statut = "refus" if self.refus else "validation" if self.validations else "ok"
        if self.refus:
            self.prix_total_ht = self.prix_unitaire_ht = None
        return self

    def en_dict(self):
        return {k: (float(v) if isinstance(v, Decimal) else v) for k, v in self.__dict__.items()}


def toles_en_ligne():
    from technique.models import Article

    return (
        Article.objects.filter(nature=Article.Nature.MATIERE_PREMIERE, vendu_en_ligne=True, matiere__isnull=False, epaisseur__isnull=False, format_tole__isnull=False)
        .select_related("matiere", "matiere__famille", "format_tole")
        .order_by("matiere__nom", "epaisseur", "reference")
    )


def catalogue():
    """Ce que le configurateur peut proposer : matières et épaisseurs vendues en ligne, formes, pliage, grille web."""
    from decoupe.services import formes

    from web.models import ReglageWeb

    matieres = {}
    for tole in toles_en_ligne():
        m = matieres.setdefault(tole.matiere_id, {"matiere": tole.matiere.nom, "famille": str(tole.matiere.famille or ""), "epaisseurs": []})
        if tole.epaisseur not in m["epaisseurs"]:
            m["epaisseurs"].append(tole.epaisseur)
    reglage = ReglageWeb.charger()
    return {
        "matieres": list(matieres.values()),
        "formes": formes.catalogue()["familles"],
        "pliage": {
            "formes": ["profil", "boite"],
            "aide": "Cotes extérieures. Profil : faces et angles (90 = équerre, signe = sens du pli). Boîte : fond et bords A, B, C, D.",
        },
        "minimum_commande_ht": float(reglage.minimum_commande_ht),
        "quantite_max": reglage.quantite_max,
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


def chiffrer(matiere_nom, epaisseur, quantite, forme=None, cotes=None, pliage=None, aujourdhui=None):
    """Prix instantané. `forme` + `cotes` : pièce plane de la bibliothèque. `pliage` : {"forme": "profil", "faces": [...],
    "angles": [...], "longueur": L} ou {"forme": "boite", "longueur": L, "largeur": l, "bords": [A, B, C, D], "angles": [...]}.
    Lève ErreurPrixWeb pour une demande mal formée ; une pièce infaisable donne un résultat « refus »."""
    from chiffrage.moteur import ChiffrageError, cout_etape_gamme
    from chiffrage.reglage import minutes_mise_en_place
    from decoupe.models import Plieuse
    from decoupe.services import formes
    from decoupe.services.matiere import ErreurMatiere, cout_matiere_imbrication
    from decoupe.services.pliage import ErreurPliage, calculer_boite, calculer_pliage
    from decoupe.services.temps import ErreurTemps, estimer_temps_decoupe
    from technique.models import Gamme

    from web.models import ReglageWeb

    aujourdhui = aujourdhui or datetime.date.today()
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
        return r.finaliser()

    # Contour à découper : forme plane, ou flan de la pièce pliée.
    resultat_pliage = None
    try:
        if pliage:
            if pliage.get("forme") == "boite":
                longueur, largeur = float(pliage["longueur"]), float(pliage["largeur"])
                resultat_pliage = calculer_boite(matiere, epaisseur, longueur, largeur, [float(h or 0) for h in pliage["bords"]],
                                                 [float(a) for a in pliage.get("angles") or []] or None)
                r.designation = f"Boîte {longueur:g} × {largeur:g}, bords " + " / ".join(f"{float(h or 0):g}" for h in pliage["bords"])
            else:
                faces, angles = [float(f) for f in pliage["faces"]], [float(a) for a in pliage["angles"]]
                if len(faces) < 2 or len(angles) != len(faces) - 1 or any(f <= 0 for f in faces) or any(not 0 < abs(a) < 180 for a in angles):
                    raise ErreurPrixWeb("Profil : au moins deux faces positives et un angle (entre 0 et 180°) de moins que de faces.")
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
    except formes.ErreurForme as exc:
        raise ErreurPrixWeb(str(exc))
    except ErreurPliage as exc:
        r.refus.append(str(exc))
        return r.finaliser()

    if resultat_pliage is not None:
        r.pliage = resultat_pliage.en_dict()
        r.refus += resultat_pliage.refus
        r.validations += resultat_pliage.validations
        if resultat_pliage.statut == "refus":
            return r.finaliser()
        if resultat_pliage.flan_mm:
            exterieur = _contour_flan_boite(*resultat_pliage.flan_mm, resultat_pliage.crans_mm)
        else:
            d = resultat_pliage.developpe_mm
            exterieur = [(0, 0), (d, 0), (d, longueur), (0, longueur)]
        trous = []
    else:
        exterieur, trous = contour.exterieur, contour.trous

    procede = _procede(matiere, epaisseur)
    r.procede = procede
    piece = _piece(matiere, epaisseur, procede, exterieur, trous)
    r.largeur_mm, r.hauteur_mm = round(piece.largeur_mm, 2), round(piece.hauteur_mm, 2)
    r.contour = piece.contour_json
    r.poids_kg = round(piece.surface_mm2 * epaisseur * matiere.densite / 1e6, 3)

    try:
        temps = estimer_temps_decoupe(piece)
    except ErreurTemps as exc:
        r.refus.append(str(exc))
        return r.finaliser()
    r.notes += temps.avertissements
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
        r.refus.append(f"Prix indisponible ({exc}).")
        return r.finaliser()

    r.cout_revient = arrondir(r.cout_matiere + r.cout_decoupe + r.cout_pliage)
    r.prix_total_ht = arrondir(r.cout_revient * (1 + D(reglage.taux_marge_pct) / 100))
    r.prix_unitaire_ht = arrondir(r.prix_total_ht / quantite)
    return r.finaliser()
