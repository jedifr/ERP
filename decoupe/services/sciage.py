"""Optimisation de sciage : débits de tubes et profilés répartis dans des barres (découpe à une dimension).

Chaque coupe est une droite x = x0 + y·t dans le plan de coupe (t = tangente de l'angle mesuré depuis la coupe droite, y de 0 à h,
h = hauteur de la section vue de côté). Un débit se pose après la coupe précédente en restant à un trait de scie d'elle sur toute la
hauteur ; deux coupes au même angle se confondent (débits imbriqués tête-bêche) et ne coûtent qu'un trait de scie. Un débit peut
se retourner (quatre orientations) pour profiter de cette imbrication.

Recherche : premier ajustement décroissant, meilleur ajustement décroissant, puis des ordres perturbés (tirage pseudo-aléatoire à
graine fixe : le même projet donne toujours le même plan). On retient le moins de barres neuves, puis le moins de chutes du stock,
puis les chutes les plus concentrées (une grande chute réutilisable vaut mieux que plusieurs petites).

Longueurs en mm, angles en degrés : 0 = coupe droite, le signe donne le sens de l'onglet (45 / -45 : trapèze)."""

import math
import random
from dataclasses import dataclass, field

ANGLE_MAX = 70.0
TOLERANCE = 1e-6


class ErreurSciage(Exception):
    """Débit impossible (longueur, angle, barre trop courte…) : message affichable."""


@dataclass(frozen=True)
class Debit:
    """Un exemplaire à couper."""

    repere: str
    longueur: float
    angle_g: float = 0.0
    angle_d: float = 0.0
    couleur: str = "#d4d4d4"

    def variantes(self):
        """Orientations possibles (t gauche, t droite) : telle quelle, retournée, inversée bout pour bout, les deux."""
        a, b = math.tan(math.radians(self.angle_g)), math.tan(math.radians(self.angle_d))
        vues, sortie = set(), []
        for v in ((a, b), (-a, -b), (-b, -a), (b, a)):
            cle = (round(v[0], 6), round(v[1], 6))
            if cle not in vues:
                vues.add(cle)
                sortie.append(v)
        return sortie


@dataclass
class Place:
    debit: Debit
    xl0: float  # pied de la coupe gauche
    tl: float
    xr0: float  # pied de la coupe droite
    tr: float

    @property
    def angle_g(self):
        return round(math.degrees(math.atan(self.tl)))

    @property
    def angle_d(self):
        return round(math.degrees(math.atan(self.tr)))


@dataclass
class Barre:
    longueur: float
    origine: str  # "barre" (neuve) ou "chute" (du stock)
    debut: float  # chute initiale
    places: list = field(default_factory=list)
    x0: float = 0.0  # dernière coupe : pied et pente
    t: float = 0.0
    fin: float = 0.0  # point le plus à droite du dernier débit

    def __post_init__(self):
        self.x0 = self.fin = self.debut

    @property
    def chute(self):
        return max(self.longueur - self.fin, 0.0)

    def essai(self, debit, variantes, lame, h):
        """Meilleure orientation (fin la plus courte) si le débit tient encore, sinon None."""
        meilleur = None
        for tl, tr in variantes:
            xl0 = self.x0 + lame * math.sqrt(1 + tl * tl) + max(0.0, h * (self.t - tl))
            xr0 = xl0 + debit.longueur - max(0.0, h * (tr - tl))
            fin = max(xr0, xr0 + h * tr)
            if fin <= self.longueur + TOLERANCE and (meilleur is None or fin < meilleur[0]):
                meilleur = (fin, xl0, tl, xr0, tr)
        return meilleur

    def ajouter(self, debit, essai):
        fin, xl0, tl, xr0, tr = essai
        self.places.append(Place(debit, xl0, tl, xr0, tr))
        self.x0, self.t, self.fin = xr0, tr, fin

    def signature(self):
        return (self.origine, self.longueur, tuple((p.debit.repere, p.debit.longueur, p.debit.angle_g, p.debit.angle_d) for p in self.places))

    def sequence(self):
        """« •(3)2x226[45°↔-45°]•(1)4x680[0°↔0°] » : repère, nombre, longueur et angles saisis, débits consécutifs regroupés."""
        blocs = []
        for p in self.places:
            d = p.debit
            cle = (d.repere, d.longueur, d.angle_g, d.angle_d)
            if blocs and blocs[-1][0] == cle:
                blocs[-1][1] += 1
            else:
                blocs.append([cle, 1])
        return "".join(f"•({r}){n}x{nombre(l)}[{nombre(g)}°↔{nombre(dr)}°]" for (r, l, g, dr), n in blocs)


@dataclass
class Groupe:
    barre: Barre
    nombre: int


@dataclass
class ResultatSection:
    longueur_barre: float
    barres: list
    essais: int

    @property
    def groupes(self):
        par_signature = {}
        for b in self.barres:
            s = b.signature()
            if s in par_signature:
                par_signature[s].nombre += 1
            else:
                par_signature[s] = Groupe(b, 1)
        return sorted(par_signature.values(), key=lambda g: (g.barre.origine == "chute", g.barre.chute))

    @property
    def nb_neuves(self):
        return sum(1 for b in self.barres if b.origine == "barre")

    @property
    def nb_chutes_stock(self):
        return len(self.barres) - self.nb_neuves

    @property
    def longueur_achetee(self):
        return sum(b.longueur for b in self.barres)

    @property
    def longueur_debits(self):
        return sum(p.debit.longueur for b in self.barres for p in b.places)

    @property
    def nb_debits(self):
        return sum(len(b.places) for b in self.barres)

    @property
    def chute_totale(self):
        return sum(b.chute for b in self.barres)

    @property
    def taux_utilisation(self):
        total = self.longueur_achetee
        return self.longueur_debits / total * 100 if total else 0.0

    def chutes_reutilisables(self, seuil):
        return sorted((b.chute for b in self.barres if b.chute >= seuil and b.chute > 0), reverse=True)

    def nb_coupes(self, chute_initiale):
        return sum(len(b.places) + (1 if chute_initiale > 0 else 0) for b in self.barres)


def nombre(x):
    """Nombre sans zéro inutile, séparateur de milliers français : 1 230 ; 2,5."""
    if abs(x - round(x)) < 1e-9:
        return f"{int(round(x)):,}".replace(",", " ")
    return f"{x:,.1f}".replace(",", " ").replace(".", ",")


def verifier(debit, hauteur):
    if debit.longueur <= 0:
        raise ErreurSciage(f"Repère {debit.repere} : la longueur doit être positive.")
    for nom, a in (("gauche", debit.angle_g), ("droit", debit.angle_d)):
        if abs(a) > ANGLE_MAX:
            raise ErreurSciage(f"Repère {debit.repere} : angle {nom} entre -{ANGLE_MAX:g}° et {ANGLE_MAX:g}° (0 = coupe droite).")
    a, b = math.tan(math.radians(debit.angle_g)), math.tan(math.radians(debit.angle_d))
    if debit.longueur <= hauteur * abs(b - a):
        raise ErreurSciage(f"Repère {debit.repere} : les coupes en biais se rejoignent, la longueur est trop courte pour cette section.")


def _passe(debits, variantes, longueur, chutes_stock, lame, chute_initiale, h, meilleur_ajustement):
    barres, stock = [], sorted(chutes_stock)
    for d in debits:
        v = variantes[d]
        cible = essai = None
        reste = math.inf
        for b in barres:
            e = b.essai(d, v, lame, h)
            if e is None:
                continue
            if not meilleur_ajustement:
                cible, essai = b, e
                break
            if b.longueur - e[0] < reste:
                cible, essai, reste = b, e, b.longueur - e[0]
        if cible is None:
            for i, l in enumerate(stock):  # la plus petite chute du stock qui reçoit le débit
                b = Barre(l, "chute", chute_initiale)
                e = b.essai(d, v, lame, h)
                if e is not None:
                    cible, essai = b, e
                    del stock[i]
                    break
            if cible is None:
                cible = Barre(longueur, "barre", chute_initiale)
                essai = cible.essai(d, v, lame, h)
                if essai is None:
                    raise ErreurSciage(f"Repère {d.repere} ({nombre(d.longueur)} mm) : ne tient pas dans une barre de {nombre(longueur)} mm.")
            barres.append(cible)
        cible.ajouter(d, essai)
    return barres


def _score(barres):
    neuves = sum(1 for b in barres if b.origine == "barre")
    return (neuves, len(barres) - neuves, -sum(b.chute ** 2 for b in barres))


def _rangement(barre, variantes, lame, chute_initiale, h):
    """Débits d'une barre remis dans l'ordre de coupe (les plus longs d'abord, repères groupés) si ça tient toujours."""
    debits = sorted((p.debit for p in barre.places), key=lambda d: (-d.longueur, d.repere))
    nouvelle = Barre(barre.longueur, barre.origine, chute_initiale)
    for d in debits:
        e = nouvelle.essai(d, variantes[d], lame, h)
        if e is None:
            return barre
        nouvelle.ajouter(d, e)
    return nouvelle


def optimiser(debits, longueur_barre, hauteur, lame=3.0, chute_initiale=0.0, chutes_stock=(), essais=None, graine=1):
    """Plan de coupe d'une section : `debits` (liste de Debit, un par exemplaire). Lève ErreurSciage."""
    if longueur_barre <= chute_initiale:
        raise ErreurSciage("La barre doit être plus longue que la chute initiale.")
    for d in set(debits):
        verifier(d, hauteur)
    variantes = {d: d.variantes() for d in set(debits)}
    if not debits:
        return ResultatSection(longueur_barre, [], 0)
    args = (variantes, longueur_barre, list(chutes_stock), lame, chute_initiale, hauteur)
    ordre = sorted(debits, key=lambda d: (-d.longueur, d.repere))
    meilleures = _passe(ordre, *args, False)
    score = _score(meilleures)
    if essais is None:  # environ 40 000 placements : moins d'une seconde
        essais = max(20, min(300, 40000 // len(debits)))
    alea = random.Random(graine)
    plancher = math.ceil(sum(d.longueur + lame for d in debits) / (longueur_barre - chute_initiale + lame))
    for i in range(essais + 1):
        if i == 0:
            candidat = _passe(ordre, *args, True)
        else:
            perturbe = sorted(debits, key=lambda d: -d.longueur * (1 + (alea.random() - 0.5) * 0.6))
            candidat = _passe(perturbe, *args, alea.random() < 0.5)
        s = _score(candidat)
        if s < score:
            meilleures, score = candidat, s
        # Nombre de barres minimal atteint et chute déjà concentrée sur une barre : inutile de chercher plus loin.
        if score[0] + score[1] <= plancher and sum(1 for b in meilleures if b.chute > lame) <= 1 and not chutes_stock:
            break
    barres = [_rangement(b, variantes, lame, chute_initiale, hauteur) for b in meilleures]
    return ResultatSection(longueur_barre, barres, i + 1)


# ------------------------------------------------------------------ dessins (SVG)
def _attr(texte):
    return str(texte).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def svg_barre(barre, largeur=900, hauteur=26, hachure="sc-hachure"):
    """Barre à l'échelle ; les onglets sont exagérés (biais de 5 px) pour rester lisibles. Chute hachurée."""
    s, k, y0, y1 = largeur / barre.longueur, 5, 2, hauteur - 2
    parties = [
        f'<svg viewBox="0 0 {largeur} {hauteur}" width="100%" height="{hauteur}" preserveAspectRatio="none" xmlns="http://www.w3.org/2000/svg">',
        f'<defs><pattern id="{hachure}" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="6" height="6" fill="#e5e7eb"/>'
        f'<line x1="0" y1="0" x2="0" y2="6" stroke="#9ca3af" stroke-width="2"/></pattern></defs>',
        f'<rect x="0.5" y="{y0}" width="{largeur - 1}" height="{y1 - y0}" fill="#ffffff" stroke="#9ca3af"/>',
    ]
    if barre.chute > 0.5:
        parties.append(f'<rect x="{barre.fin * s:.1f}" y="{y0}" width="{max(barre.chute * s - 0.5, 0):.1f}" height="{y1 - y0}" fill="url(#{hachure})"/>')
    for p in barre.places:
        cxl, cxr = p.xl0 * s, p.xr0 * s
        sl, sr = math.copysign(k, p.tl) if p.tl else 0, math.copysign(k, p.tr) if p.tr else 0
        pts = [(cxl - sl / 2, y1), (cxr - sr / 2, y1), (cxr + sr / 2, y0), (cxl + sl / 2, y0)]
        parties.append(
            f'<polygon points="{" ".join(f"{x:.1f},{y}" for x, y in pts)}" fill="{_attr(p.debit.couleur)}" stroke="#111827" stroke-width="0.8"/>'
        )
        if cxr - cxl > 14:
            parties.append(
                f'<text x="{(cxl + cxr) / 2:.1f}" y="{(y0 + y1) / 2 + 4}" text-anchor="middle" font-size="10" font-weight="600" '
                f'font-family="sans-serif" fill="#111827">{_attr(p.debit.repere)}</text>'
            )
    parties.append("</svg>")
    return "".join(parties)


def svg_piece(longueur, angle_g, angle_d, couleur="#d4d4d4", couleur_trait="currentColor"):
    """Croquis de face d'un débit : hauteur exagérée pour lire les onglets, cote hors tout dessous, angles aux extrémités."""
    W, H, ox, yt = 96, 16, 24, 4
    yb = yt + H
    tl, tr = math.tan(math.radians(max(-ANGLE_MAX, min(ANGLE_MAX, angle_g or 0)))), math.tan(math.radians(max(-ANGLE_MAX, min(ANGLE_MAX, angle_d or 0))))
    xl0 = ox + max(0.0, -H * tl)
    xr0 = xl0 + W - max(0.0, H * (tr - tl))
    pts = [(xl0, yb), (xr0, yb), (xr0 + H * tr, yt), (xl0 + H * tl, yt)]
    x1, x2 = min(xl0, xl0 + H * tl), max(xr0, xr0 + H * tr)
    texte = f'font-family="sans-serif" font-size="9.5" fill="{couleur_trait}"'
    angles = ""
    if angle_g:
        angles += f'<text x="{x1 - 3:.1f}" y="{yb - 3}" text-anchor="end" {texte}>{nombre(abs(angle_g))}°</text>'
    if angle_d:
        angles += f'<text x="{x2 + 3:.1f}" y="{yb - 3}" text-anchor="start" {texte}>{nombre(abs(angle_d))}°</text>'
    trait = f'stroke="{couleur_trait}" stroke-width="0.6"'
    return (
        f'<svg viewBox="0 0 164 40" width="164" height="40" xmlns="http://www.w3.org/2000/svg" role="img" '
        f'aria-label="{nombre(longueur)} mm, {nombre(angle_g or 0)}° / {nombre(angle_d or 0)}°">'
        f'<polygon points="{" ".join(f"{x:.1f},{y}" for x, y in pts)}" fill="{_attr(couleur)}" stroke="{couleur_trait}" stroke-width="1"/>{angles}'
        f'<line x1="{x1:.1f}" y1="{yb + 5}" x2="{x1:.1f}" y2="{yb + 12}" {trait}/><line x1="{x2:.1f}" y1="{yb + 5}" x2="{x2:.1f}" y2="{yb + 12}" {trait}/>'
        f'<line x1="{x1:.1f}" y1="{yb + 9}" x2="{x2:.1f}" y2="{yb + 9}" {trait}/>'
        f'<path d="M{x1:.1f},{yb + 9} l4,-2 v4 z M{x2:.1f},{yb + 9} l-4,-2 v4 z" fill="{couleur_trait}"/>'
        f'<text x="{(x1 + x2) / 2:.1f}" y="{yb + 19}" text-anchor="middle" font-weight="600" {texte}>{nombre(longueur)}</text></svg>'
    )


# ------------------------------------------------------------------ projet (OptimisationSciage)
@dataclass
class PlanSection:
    section: object  # ProfileSection
    lignes: list  # récapitulatif : dicts repere, couleur, longueur, quantite, angle_g, angle_d
    resultat: ResultatSection | None = None
    erreur: str = ""
    prix_metre: object = None  # Decimal ou None
    erreur_prix: str = ""
    seuil: float = 0.0
    chute_initiale: float = 0.0
    lame: float = 0.0

    @property
    def chutes_reutilisables(self):
        return self.resultat.chutes_reutilisables(self.seuil) if self.resultat else []

    @property
    def longueur_reutilisable(self):
        return sum(self.chutes_reutilisables)

    @property
    def nb_coupes(self):
        return self.resultat.nb_coupes(self.chute_initiale) if self.resultat else 0

    @property
    def masse_kg(self):
        return self.resultat.longueur_achetee / 1000 * self.section.masse_lineique if self.resultat else 0.0

    @property
    def cout(self):
        """Matière consommée : barres utilisées moins les chutes rendues au stock, au prix du mètre de la section."""
        if self.resultat is None or self.prix_metre is None:
            return None
        from comptes.montants import D, arrondir

        return arrondir(D(self.resultat.longueur_achetee - self.longueur_reutilisable) / D(1000) * self.prix_metre)

    @property
    def groupes(self):
        """Groupes de barres identiques avec leur dessin et leur séquence, prêts pour les gabarits."""
        if not self.resultat:
            return []
        return [
            {"nombre": g.nombre, "barre": g.barre, "sequence": g.barre.sequence(), "chute": g.barre.chute,
             "reutilisable": g.barre.chute >= self.seuil and g.barre.chute > 0, "svg": svg_barre(g.barre, hachure=f"sc-h{self.section.pk}-{i}")}
            for i, g in enumerate(self.resultat.groupes)
        ]


@dataclass
class Plan:
    sections: list

    @property
    def calculees(self):
        return [s for s in self.sections if s.resultat]

    def total(self, nom):
        return sum(getattr(s.resultat, nom) for s in self.calculees)

    @property
    def nb_neuves(self):
        return self.total("nb_neuves")

    @property
    def nb_chutes_stock(self):
        return self.total("nb_chutes_stock")

    @property
    def nb_debits(self):
        return self.total("nb_debits")

    @property
    def longueur_debits(self):
        return self.total("longueur_debits")

    @property
    def chute_totale(self):
        return self.total("chute_totale")

    @property
    def taux_utilisation(self):
        achetee = self.total("longueur_achetee")
        return self.longueur_debits / achetee * 100 if achetee else 0.0

    @property
    def longueur_reutilisable(self):
        return sum(s.longueur_reutilisable for s in self.calculees)

    @property
    def nb_reutilisables(self):
        return sum(len(s.chutes_reutilisables) for s in self.calculees)

    @property
    def masse_kg(self):
        return sum(s.masse_kg for s in self.calculees)

    @property
    def cout(self):
        couts = [s.cout for s in self.calculees]
        return sum(couts) if couts and all(c is not None for c in couts) else None

    @property
    def barres_par_section(self):
        return " · ".join(f"{s.resultat.nb_neuves} × {nombre(s.resultat.longueur_barre)} mm" for s in self.calculees)


def calculer_plan(optimisation, lignes):
    """Plan de coupe d'une OptimisationSciage (éventuellement non enregistrée). `lignes` : dicts avec repere, couleur, longueur_mm,
    quantite, angle_gauche_deg, angle_droit_deg et section (ProfileSection). Les sections suivent l'ordre des lignes."""
    from .profiles import ErreurPrixProfile

    try:
        chutes = optimisation.liste_chutes()
    except ValueError:
        chutes = []
    multiplicateur = max(int(optimisation.quantite_ensembles or 1), 1)
    par_section = {}
    for l in lignes:
        if not l.get("section") or not (l.get("longueur_mm") or 0) > 0 or not (l.get("quantite") or 0) > 0:
            continue
        par_section.setdefault(l["section"].pk, (l["section"], []))[1].append(l)
    sections = []
    for section, ls in par_section.values():
        recap, debits = {}, []
        for l in ls:
            d = Debit(str(l.get("repere") or "?"), float(l["longueur_mm"]), float(l.get("angle_gauche_deg") or 0), float(l.get("angle_droit_deg") or 0),
                      l.get("couleur") or "#d4d4d4")
            q = int(l["quantite"]) * multiplicateur
            debits += [d] * q
            cle = (d.repere, d.longueur, d.angle_g, d.angle_d)
            if cle in recap:
                recap[cle]["quantite"] += q
            else:
                recap[cle] = {"repere": d.repere, "couleur": d.couleur, "longueur": d.longueur, "quantite": q, "angle_g": d.angle_g, "angle_d": d.angle_d,
                              "croquis": svg_piece(d.longueur, d.angle_g, d.angle_d, d.couleur, "#000000")}
        plan = PlanSection(section, list(recap.values()), seuil=float(optimisation.chute_reutilisable_mm or 0),
                           chute_initiale=float(optimisation.chute_initiale_mm or 0), lame=float(optimisation.largeur_lame_mm or 0))
        longueur = float(optimisation.longueur_barre_mm if optimisation.longueur_barre == "imposee" else section.longueur_barre_mm)
        try:
            plan.resultat = optimiser(debits, longueur, section.hauteur_mm, plan.lame, plan.chute_initiale, chutes)
        except ErreurSciage as exc:
            plan.erreur = str(exc)
        try:
            plan.prix_metre = section.prix_au_metre()
        except ErreurPrixProfile as exc:
            plan.erreur_prix = str(exc)
        sections.append(plan)
    return Plan(sections)


def lignes_de(optimisation):
    """Lignes enregistrées d'une optimisation, au format de calculer_plan."""
    return [
        {"repere": d.repere, "couleur": d.couleur, "longueur_mm": d.longueur_mm, "quantite": d.quantite, "angle_gauche_deg": d.angle_gauche_deg,
         "angle_droit_deg": d.angle_droit_deg, "section": d.section}
        for d in optimisation.debits.select_related("section", "section__article")
    ]


# ------------------------------------------------------------------ import des débits de profilés (devis, commande, OF)
COULEURS = ["#d4d4d4", "#fdf26b", "#7ee07e", "#f472b6", "#60a5fa", "#fb923c", "#a78bfa", "#5eead4", "#fca5a5", "#bef264"]


def angles_depuis_piece_profile(piece):
    """PieceProfile mesure ses coupes depuis l'axe (90 = droite) sans sens ; on les suppose convergentes (trapèze), le cas courant
    des cadres soudés : 45° / 45° devient 45° / -45°."""
    return 90 - piece.coupe_a_deg, -(90 - piece.coupe_b_deg)


def debits_a_importer(devis=None, commande=None, ordre_fabrication=None):
    """[(PieceProfile, quantité)] d'un devis (quantités du devis), d'une commande (quantité de chaque ligne dont l'article est un débit
    de profilé, à défaut les débits de son devis) ou d'un OF (débits dont l'article est celui de l'OF, à la quantité de l'OF)."""
    from ..models import PieceProfile

    if devis is not None:
        return [(p, p.quantite) for p in devis.pieces_profile.select_related("section")]
    if commande is not None:
        sortie = []
        for ligne in commande.lignes.select_related("article"):
            p = PieceProfile.objects.filter(article=ligne.article).select_related("section").order_by("-pk").first() if ligne.article_id else None
            if p:
                sortie.append((p, ligne.quantite_commandee))
        if not sortie and commande.devis_id:
            sortie = [(p, p.quantite) for p in commande.devis.pieces_profile.select_related("section")]
        return sortie
    if ordre_fabrication is not None:
        p = PieceProfile.objects.filter(article=ordre_fabrication.article_id).select_related("section").order_by("-pk").first()
        return [(p, ordre_fabrication.quantite)] if p else []
    return []


def importer(optimisation, elements):
    """Ajoute les débits [(PieceProfile, quantité)] à l'optimisation ; renvoie le nombre de lignes créées."""
    from ..models import DebitSciage

    existants = list(optimisation.debits.all())
    ordre = max((d.ordre for d in existants), default=0)
    reperes = {d.repere for d in existants}
    n = 0
    for piece, quantite in elements:
        q = int(math.ceil(float(quantite or 0)))
        if q <= 0:
            continue
        ordre += 1
        repere = str(len(reperes) + 1)
        while repere in reperes:
            repere = str(int(repere) + 1)
        reperes.add(repere)
        g, d = angles_depuis_piece_profile(piece)
        DebitSciage.objects.create(
            optimisation=optimisation, repere=repere, couleur=COULEURS[(len(reperes) - 1) % len(COULEURS)], longueur_mm=piece.longueur_mm,
            quantite=q, angle_gauche_deg=g, angle_droit_deg=d, section=piece.section, piece_profile=piece, ordre=ordre,
        )
        n += 1
    return n
