"""Chiffrage et faisabilité d'une pièce pliée : profil (suite de faces cotées à l'extérieur et d'angles) ou boîte
(fond rectangulaire et jusqu'à quatre bords relevés, pliée dans deux directions, coins crantés).

Règles de l'atelier (Découpe H2O, octobre 2026) :
- vé cible = 6 × épaisseur jusqu'à 3 mm, 8 × de 3 à 8 mm, 10 × de 8 à 12 mm, 12 × au-delà ; on retient le vé disponible le plus
  proche parmi ceux que l'abaque admet pour l'épaisseur, puis un plus petit si une face est plus courte que le bord mini ;
- force par mètre lue dans l'abaque Amada (acier Rm 450 ou inox Rm 700), × Rm de la matière / Rm de l'abaque. Ouverture ou
  épaisseur absente de l'abaque : force ∝ 1 / V et ∝ épaisseur², depuis la case la plus proche (« interpolée ») ;
- la force admise par le vé baisse quand l'angle se ferme (interpolation entre l'angle du vé et 90°) : l'angle mini vendu est
  l'angle où la force du pli atteint cette limite, + la marge de retour élastique, arrondi au multiple de 5 supérieur.
  Un pli aigu dont la force dépasse la force du vé à son propre angle passe en validation au bureau d'études ;
- plieuse : la moins puissante qui a le vé, un poinçon adapté, la longueur, et dont la force utilisable (70 %) suffit ;
- validation technicien : face entre deux plis < 2 × bord mini, aile d'un U plus longue que sa largeur, épaisseur au-delà du
  seuil, vé non vendu en ligne ;
- développé (cotes extérieures) : Σ faces − Σ reculs + Σ arcs neutres. Arc = β × (ri + K × e), β = 180° − angle de la pièce ;
  recul = (ri + e) × tan(β / 2) pour un angle ≥ 90° (intersection virtuelle), ri + e en dessous (tangence) ;
- temps : un réglage par réglage distinct (même vé, angle et faces adjacentes), 5 min sous 800 mm de pli, 7,5 au-delà ;
  30 s par pli et par pièce (15 s en série, appliqué par le moteur de chiffrage selon la quantité du devis) ;
- boîte : chaque bord est un pli de la longueur du côté du fond ; force et plieuse selon le pli le plus long ; flan =
  développé de chaque direction (bord / fond / bord), coins crantés ; mêmes validations qu'un U dans chaque direction,
  plus les bords relevés de la première direction, qui doivent passer sous le poinçon pour les derniers plis.
"""

import json
import math
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

KN_PAR_TONNE = 9.81
LETTRES = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


class ErreurPliage(Exception):
    pass


@lru_cache(maxsize=1)
def abaque():
    chemin = Path(__file__).resolve().parent.parent / "data" / "abaque_pliage_amada.json"
    brut = json.loads(chemin.read_text(encoding="utf-8"))
    tables = {}
    for cle in ("acier_rm450", "inox_rm700"):
        lignes = {float(e): {float(v): float(f) for v, f in forces.items()} for e, forces in brut[cle]["forces"].items()}
        tables[cle] = {"rm": brut[cle]["rm"], "lignes": dict(sorted(lignes.items()))}
    ves = sorted((float(v), d) for v, d in brut["ves"].items())
    return tables, ves


def _est_inox(famille):
    return famille is not None and (famille.nom.lower().startswith("inox") or (famille.nom_igems or "").lower() == "stainless steel")


def table_pour(famille):
    tables, _ = abaque()
    return tables["inox_rm700" if _est_inox(famille) else "acier_rm450"]


def _interpoler(points, x):
    """Interpolation linéaire dans une liste triée de (x, y), bornée aux extrémités."""
    if x <= points[0][0]:
        return points[0][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return points[-1][1]


def bord_mini(ouverture):
    _, ves = abaque()
    return _interpoler([(v, d["bord_mini"]) for v, d in ves], ouverture)


def rayon_interieur(ouverture, famille):
    _, ves = abaque()
    cle = "ir_inox" if _est_inox(famille) else "ir_acier"
    return _interpoler([(v, d[cle]) for v, d in ves], ouverture)


def plage_ves(table, epaisseur):
    """(ouverture mini, ouverture maxi) que l'abaque admet pour cette épaisseur (interpolée entre deux épaisseurs)."""
    lignes = table["lignes"]
    mini = [(e, min(row)) for e, row in lignes.items()]
    maxi = [(e, max(row)) for e, row in lignes.items()]
    return _interpoler(mini, epaisseur), _interpoler(maxi, epaisseur)


def force_abaque(table, epaisseur, ouverture):
    """(force en kN/m pour Rm de l'abaque, lue directement ?)."""
    lignes = table["lignes"]
    proche = min(lignes, key=lambda e: (abs(e - epaisseur), -e))
    rangee = lignes[proche]
    v_proche = min(rangee, key=lambda v: (abs(v - ouverture), v))
    force = rangee[v_proche] * v_proche / ouverture * (epaisseur / proche) ** 2
    return force, (proche == epaisseur and v_proche == ouverture)


def ve_cible(epaisseur):
    facteur = 6 if epaisseur <= 3 else 8 if epaisseur <= 8 else 10 if epaisseur <= 12 else 12
    return facteur * epaisseur


def angle_mini_outil(ve, force_kn_m):
    """Angle de pièce le plus fermé que le vé accepte pour cette force (None : force au-delà de la limite à 90°)."""
    if force_kn_m <= ve.force_max_angle_kn_m:
        return ve.angle_deg
    if force_kn_m > ve.force_max_90_kn_m:
        return None
    return ve.angle_deg + (force_kn_m - ve.force_max_angle_kn_m) / (ve.force_max_90_kn_m - ve.force_max_angle_kn_m) * (90 - ve.angle_deg)


def developpe(faces, angles, epaisseur, ri, coef_k):
    total = sum(faces)
    for angle in angles:
        alpha = abs(angle)
        beta = math.radians(180 - alpha)
        recul = (ri + epaisseur) * math.tan(beta / 2) if alpha >= 90 else ri + epaisseur
        total += beta * (ri + coef_k * epaisseur) - 2 * recul
    return total


@dataclass
class ResultatPliage:
    statut: str = "ok"  # ok, validation, refus
    refus: list = field(default_factory=list)
    validations: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    ouverture_ve: float = None
    ve: str = ""
    poincon: str = ""
    plieuse: str = ""
    plieuse_id: int = None
    force_kn_m: float = None
    force_t: float = None
    angle_mini: float = None
    rayon_interieur: float = None
    bord_mini: float = None
    developpe_mm: float = None
    flan_mm: list = None  # boîte : [x, y] du flan avant crantage des coins
    crans_mm: list = None  # boîte : profondeur des crans de coin côté A, B, C, D (développé de chaque bord)
    nb_plis: int = 0
    nb_reglages: int = 0
    temps_reglage_min: float = 0
    temps_pli_min: float = 0

    def finaliser(self):
        self.statut = "refus" if self.refus else "validation" if self.validations else "ok"
        return self

    def en_dict(self):
        return dict(self.__dict__)


@dataclass
class Pli:
    """Un pli : angle de la pièce, longueur de pli et cotes des deux faces qui l'encadrent."""

    nom: str
    angle: float
    longueur: float
    faces: tuple


def _controler_parametrage(matiere, plieuses):
    from decoupe.models import Plieuse

    famille = matiere.famille
    if famille is None:
        raise ErreurPliage(f"La matière {matiere} n'a pas de famille : impossible de choisir l'abaque de pliage.")
    if famille.coef_k is None:
        raise ErreurPliage(f"Renseignez le coefficient K de la famille {famille} (Paramétrage → Familles de matière).")
    if plieuses is None:
        plieuses = list(Plieuse.objects.filter(actif=True).prefetch_related("ves", "poincons"))
    if not plieuses:
        raise ErreurPliage("Aucune plieuse active : créez-les dans Paramétrage → Pliage.")
    return famille, plieuses


def _outillage(r, matiere, epaisseur, faces, plis, plieuses, reglage):
    """Vé, plieuse, poinçon, force, angle mini et temps, communs au profil et à la boîte. `faces` : [(nom, cote)].
    Retourne le contexte du calcul, ou None si la pièce est refusée (motif dans r.refus)."""
    famille, plieuses = _controler_parametrage(matiere, plieuses)
    table = table_pour(famille)
    rm = famille.rm_mpa or table["rm"]
    r.nb_plis = len(plis)
    if not famille.rm_mpa:
        r.notes.append(f"Rm de la famille {famille} non renseigné : abaque pris tel quel (Rm {table['rm']}).")

    # Vé : le plus proche du vé cible parmi ceux que l'abaque admet, puis plus petit si une face est trop courte.
    ouvertures = sorted({ve.ouverture_mm for p in plieuses for ve in p.ves.all() if ve.actif})
    mini, maxi = plage_ves(table, epaisseur)
    admises = [v for v in ouvertures if mini / 1.1 <= v <= maxi * 1.1]
    if not admises:
        r.refus.append(f"Aucun vé adapté à {epaisseur:g} mm (abaque : V{mini:g} à V{maxi:g}).")
        return None
    cible = ve_cible(epaisseur)
    premiere = min(admises, key=lambda v: (abs(v - cible), v))
    essais = [premiere] + sorted((v for v in admises if v < premiere), reverse=True)
    face_courte = None
    for ouverture in essais:
        bmin = bord_mini(ouverture)
        face_courte = next((nom for nom, cote in faces if cote < bmin), None)
        if face_courte is None:
            break
    if face_courte is not None:
        bmin = bord_mini(premiere)
        r.refus.append(f"{face_courte} trop courte : {bmin:.1f} mm minimum en {epaisseur:g} mm (V{premiere:g}).")
        return None
    r.ouverture_ve, r.bord_mini = ouverture, round(bmin, 1)
    r.rayon_interieur = round(rayon_interieur(ouverture, famille), 2)

    force_table, exacte = force_abaque(table, epaisseur, ouverture)
    force = force_table * rm / table["rm"]
    longueur_max = max(p.longueur for p in plis)
    r.force_kn_m = round(force, 1)
    r.force_t = round(force * longueur_max / 1000 / KN_PAR_TONNE, 2)
    if not exacte:
        r.notes.append(f"Force interpolée depuis l'abaque (épaisseur {epaisseur:g} mm, V{ouverture:g}).")

    angle_piece = min(abs(p.angle) for p in plis)
    # Plieuse : la moins puissante qui a le vé, un poinçon adapté, la longueur et la force (pli le plus long).
    raisons = []
    for plieuse in sorted(plieuses, key=lambda p: p.force_max_t):
        ve = next((v for v in plieuse.ves.all() if v.actif and v.ouverture_mm == ouverture), None)
        if ve is None:
            raisons.append(f"{plieuse} : pas de V{ouverture:g}")
            continue
        if longueur_max > plieuse.longueur_max_mm:
            raisons.append(f"{plieuse} : pli de {longueur_max:g} mm > {plieuse.longueur_max_mm:g} mm")
            continue
        limite_t = plieuse.force_max_t * reglage.marge_force_pct / 100
        if r.force_t > limite_t:
            raisons.append(f"{plieuse} : {r.force_t:g} t > {limite_t:g} t utilisables")
            continue
        poincon = next((p for p in sorted(plieuse.poincons.all(), key=lambda p: p.force_max_kn_m)
                        if p.actif and p.angle_nez_deg <= angle_piece and p.force_max_kn_m >= force), None)
        if poincon is None:
            raisons.append(f"{plieuse} : aucun poinçon pour {angle_piece:g}° et {force:.0f} kN/m")
            continue
        angle_outil = angle_mini_outil(ve, force)
        if angle_outil is None:
            raisons.append(f"{plieuse} : {force:.0f} kN/m dépasse la force du {ve} ({ve.force_max_90_kn_m:g} kN/m)")
            continue
        break
    else:
        r.refus.append("Aucune plieuse ne convient : " + " ; ".join(raisons) + ".")
        return None
    r.plieuse, r.plieuse_id, r.ve, r.poincon = str(plieuse), plieuse.pk, str(ve), str(poincon)
    # Angle mini de la machine : vé et poinçon le plus aigu qui supporte la force (pas seulement celui retenu pour cette pièce).
    nez_mini = min(p.angle_nez_deg for p in plieuse.poincons.all() if p.actif and p.force_max_kn_m >= force)
    angle_mini = math.ceil((max(angle_outil, nez_mini) + reglage.retour_elastique_deg) / 5) * 5
    r.angle_mini = angle_mini
    for pli in plis:
        if abs(pli.angle) < angle_mini:
            r.refus.append(f"{pli.nom} : {abs(pli.angle):g}° est plus fermé que l'angle mini de {angle_mini:g}° ({ve}, {epaisseur:g} mm).")
        elif abs(pli.angle) < 90 and force > ve.force_max_angle_kn_m:
            r.validations.append(f"{pli.nom} : angle aigu au-delà de la force nominale du {ve} : validation au bureau d'études.")

    if epaisseur > reglage.epaisseur_max_sans_validation_mm:
        r.validations.append(f"Épaisseur {epaisseur:g} mm au-delà de {reglage.epaisseur_max_sans_validation_mm:g} mm : validation du devis.")
    if not ve.vendable_en_ligne:
        r.validations.append(f"Le {ve} n'est pas vendu en ligne : validation du devis.")

    # Un réglage par réglage distinct : même angle, mêmes faces adjacentes et même longueur de pli.
    reglages = {(round(abs(p.angle), 1), tuple(sorted(p.faces)), p.longueur) for p in plis}
    r.nb_reglages = len(reglages)
    court, long_ = reglage.temps_reglage(epaisseur)
    r.temps_reglage_min = round(sum(long_ if longueur >= reglage.seuil_pli_long_mm else court for _, _, longueur in reglages), 3)
    r.temps_pli_min = round(len(plis) * reglage.temps_pli_s / 60, 3)
    return {"famille": famille, "bmin": bmin, "plieuse": plieuse}


def calculer_pliage(matiere, epaisseur, faces, angles, longueur_pli, plieuses=None, reglage=None):
    """Calcule le pliage d'un profil. Lève ErreurPliage si le paramétrage manque ; un pli infaisable donne un résultat « refus »."""
    from decoupe.models import ReglagePliage

    reglage = reglage or ReglagePliage.charger()
    r = ResultatPliage()
    plis = [Pli(f"Pli {i}", a, longueur_pli, (faces[i - 1], faces[i])) for i, a in enumerate(angles, start=1)]
    ctx = _outillage(r, matiere, epaisseur, [(f"Face {LETTRES[i]}", f) for i, f in enumerate(faces)], plis, plieuses, reglage)
    if ctx is None:
        return r.finaliser()
    bmin = ctx["bmin"]

    # Validations technicien.
    for j in range(1, len(faces) - 1):
        if faces[j] < 2 * bmin:
            r.validations.append(f"Face {LETTRES[j]} entre deux plis : {faces[j]:g} mm < 2 × bord mini ({2 * bmin:.1f} mm).")
        if (angles[j - 1] > 0) == (angles[j] > 0):
            ailes = [k for k in (j - 1, j + 1) if faces[k] > faces[j]]
            for k in ailes:
                r.validations.append(f"Aile {LETTRES[k]} ({faces[k]:g} mm) plus longue que la largeur {LETTRES[j]} ({faces[j]:g} mm).")

    r.developpe_mm = round(developpe(faces, angles, epaisseur, r.rayon_interieur, float(ctx["famille"].coef_k)), 2)
    return r.finaliser()


COTES_BOITE = "ABCD"  # A et C le long de la longueur du fond, B et D le long de sa largeur


def calculer_boite(matiere, epaisseur, longueur, largeur, bords, angles=None, plieuses=None, reglage=None):
    """Boîte (ou bac) pliée dans deux directions : un fond rectangulaire `longueur` × `largeur` (cotes extérieures) et
    jusqu'à quatre bords relevés du même côté, coins crantés (non soudés).

    `bords` : hauteurs extérieures des bords A, B, C, D (None ou 0 : pas de bord). A et C se plient le long de la
    longueur (pli de `longueur`), B et D le long de la largeur (pli de `largeur`). `angles` : angle de chaque bord avec
    le fond (90 par défaut ; plus ouvert pour un bac évasé).
    Les bords d'une même direction se plient d'abord ; ceux de l'autre direction se plient ensuite entre les bords déjà
    relevés : le poinçon doit être plus haut qu'eux (validation technicien sinon)."""
    from decoupe.models import ReglagePliage

    reglage = reglage or ReglagePliage.charger()
    angles = list(angles) if angles else [90] * 4
    hauteurs = [h or 0 for h in bords]
    presents = [c for c, h in zip(COTES_BOITE, hauteurs) if h > 0]
    h = dict(zip(COTES_BOITE, hauteurs))
    a = dict(zip(COTES_BOITE, angles))
    r = ResultatPliage()
    if not presents:
        r.refus.append("Une boîte a au moins un bord relevé.")
        return r.finaliser()
    plis = [Pli(f"Bord {c}", a[c], longueur if c in "AC" else largeur, (h[c], largeur if c in "AC" else longueur)) for c in presents]
    faces = [("Fond (longueur)", longueur), ("Fond (largeur)", largeur)] + [(f"Bord {c}", h[c]) for c in presents]
    ctx = _outillage(r, matiere, epaisseur, faces, plis, plieuses, reglage)
    if ctx is None:
        return r.finaliser()
    bmin = ctx["bmin"]

    # Validations technicien : comme un U dans chaque direction, puis bords relevés face au poinçon.
    for paire, cote_fond, nom_fond in (("AC", largeur, "largeur"), ("BD", longueur, "longueur")):
        if all(h[c] > 0 for c in paire):
            if cote_fond < 2 * bmin:
                r.validations.append(f"Fond entre les bords {paire[0]} et {paire[1]} : {cote_fond:g} mm < 2 × bord mini ({2 * bmin:.1f} mm).")
            for c in paire:
                if h[c] > cote_fond:
                    r.validations.append(f"Bord {c} ({h[c]:g} mm) plus haut que la {nom_fond} du fond ({cote_fond:g} mm).")
    for c in presents:
        if a[c] < 90:
            r.validations.append(f"Bord {c} rentrant ({a[c]:g}°) : coins à vérifier par un technicien.")
    directions = [max(h[c] for c in paire) for paire in ("AC", "BD") if any(h[c] > 0 for c in paire)]
    if len(directions) == 2:
        premiers = min(directions)  # on plie d'abord la direction aux bords les plus bas
        poincon = max((p for p in ctx["plieuse"].poincons.all() if p.actif and p.hauteur_mm), key=lambda p: p.hauteur_mm, default=None)
        if poincon is None or premiers >= poincon.hauteur_mm:
            limite = f"{poincon.hauteur_mm:g} mm du {poincon.nom}" if poincon else "hauteur de poinçon inconnue"
            r.validations.append(f"Bords relevés de {premiers:g} mm avant les derniers plis ({limite}) : validation technicien.")
        r.notes.append("Derniers plis entre bords déjà relevés : poinçon en segments à la longueur intérieure.")

    ri, k = r.rayon_interieur, float(ctx["famille"].coef_k)

    def flan(cote_fond, paire, sans=None):
        """Étendue du flan dans une direction : développé bord / fond / bord (bords absents ou `sans` omis)."""
        faces_dir, angles_dir = [], []
        if h[paire[0]] and paire[0] != sans:
            faces_dir.append(h[paire[0]])
            angles_dir.append(a[paire[0]])
        faces_dir.append(cote_fond)
        if h[paire[1]] and paire[1] != sans:
            faces_dir.append(h[paire[1]])
            angles_dir.append(a[paire[1]])
        return developpe(faces_dir, angles_dir, epaisseur, ri, k)

    r.flan_mm = [round(flan(longueur, "DB"), 2), round(flan(largeur, "AC"), 2)]
    # Cran de coin : ce que chaque bord ajoute au flan dans sa direction.
    r.crans_mm = [
        round(flan(largeur, "AC") - flan(largeur, "AC", c), 2) if c in "AC" else round(flan(longueur, "DB") - flan(longueur, "DB", c), 2)
        for c in COTES_BOITE
    ]
    r.developpe_mm = r.flan_mm[0]
    return r.finaliser()


def calculer_plis(matiere, epaisseur, plis, plieuses=None, reglage=None):
    """Outillage, faisabilité et temps des plis d'une pièce lue dans un fichier 3D (STEP) : `plis` = [{"angle" (angle de
    la pièce, 90 = équerre), "longueur" (mm), "faces" (cotes extérieures des faces qui encadrent le pli)}]. Le flan est
    calculé à part (web.services.step), avec le rayon intérieur et le K retenus ici."""
    from decoupe.models import ReglagePliage

    reglage = reglage or ReglagePliage.charger()
    r = ResultatPliage()
    objets = [Pli(f"Pli {i}", float(p["angle"]), float(p["longueur"]), tuple(p.get("faces") or ())) for i, p in enumerate(plis, start=1)]
    faces = [(f"Face {LETTRES[k]} du pli {i}", float(c)) for i, p in enumerate(plis, start=1) for k, c in enumerate(p.get("faces") or ())]
    _outillage(r, matiere, epaisseur, faces, objets, plieuses, reglage)
    return r.finaliser()


def calculer_pliage_article(pliage):
    """Calcule le pliage d'un PliageArticle (profil ou boîte) et enregistre le résultat."""
    from django.utils import timezone

    if pliage.forme == pliage.Forme.BOITE:
        resultat = calculer_boite(pliage.matiere, pliage.epaisseur_mm, pliage.longueur_fond_mm, pliage.largeur_fond_mm,
                                  pliage.bords_mm, pliage.angles_bords_deg or None)
    else:
        resultat = calculer_pliage(pliage.matiere, pliage.epaisseur_mm, pliage.faces_mm, pliage.angles_deg, pliage.longueur_pli_mm)
    pliage.resultat = resultat.en_dict()
    pliage.date_calcul = timezone.now()
    pliage.save(update_fields=["resultat", "date_calcul"])
    return resultat
