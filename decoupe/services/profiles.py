"""Profilés (cornière, UPN, tubes) : dessins, imbrication des débits dans les barres et coût matière.

Les débits d'une même section d'un devis se placent ensemble dans des barres de longueur fixe (premier ajustement décroissant :
les plus longs d'abord), séparés par un trait de scie. Comme pour les tôles :
- **longueur consommée** = barres complètes − chute de bout (ce qui reste de la dernière barre après son dernier débit, rendu au
  stock) ;
- **chutes** = consommée − longueur des débits (traits de scie, chutes de tête, fonds de barres incomplètes) ;
- **longueur facturée** = débits + chutes × (1 − part de chute récupérable) ;
- **coût** = longueur facturée × prix au mètre (article d'achat de la section : au mètre, au kilo ou à la barre)."""

import math
from dataclasses import dataclass, field
from decimal import Decimal

from comptes.montants import D, arrondir, arrondir_prix

COULEURS = [("#fed7aa", "#9a3412"), ("#bfdbfe", "#1e40af"), ("#bbf7d0", "#166534"), ("#fbcfe8", "#9d174d"), ("#ddd6fe", "#5b21b6"), ("#fef08a", "#854d0e")]


class ErreurPrixProfile(Exception):
    """Prix au mètre impossible à déterminer (pas d'article d'achat, pas de coût…)."""


class ErreurProfile(Exception):
    """Débit impossible (longueur, coupe, barre trop courte…) — message affichable."""


@dataclass
class Barre:
    pieces: list = field(default_factory=list)  # (id de pièce, début mm, longueur mm)
    occupe_mm: float = 0.0  # chute de tête + débits + traits de scie

    @property
    def nb(self):
        return len(self.pieces)


@dataclass
class ResultatBarres:
    longueur_barre_mm: float
    barres: list
    pieces_mm: float
    consommee_mm: float
    chute_bout_mm: float
    facturee_mm: float
    prix_metre: Decimal | None
    cout_total: Decimal | None
    par_piece: list
    erreur_prix: str = ""

    @property
    def nb_barres(self):
        return len(self.barres)

    @property
    def taux_utilisation_pct(self):
        total = self.nb_barres * self.longueur_barre_mm
        return self.pieces_mm / total * 100 if total else 0.0

    @property
    def chutes_internes_mm(self):
        return max(self.consommee_mm - self.pieces_mm, 0.0)

    @property
    def recuperee_mm(self):
        return max(self.consommee_mm - self.facturee_mm, 0.0)

    @property
    def barres_completes_mm(self):
        return self.nb_barres * self.longueur_barre_mm


def verifier_piece(piece):
    """Cotes d'un débit valides (ErreurProfile sinon)."""
    h = piece.section.hauteur_mm
    if piece.longueur_mm <= 0:
        raise ErreurProfile("La longueur doit être positive.")
    for nom, angle in (("A", piece.coupe_a_deg), ("B", piece.coupe_b_deg)):
        if not 20 <= angle <= 90:
            raise ErreurProfile(f"Coupe de l'extrémité {nom} : un angle entre 20° et 90° (90 = coupe droite).")
    retrait = sum(h / math.tan(math.radians(a)) for a in (piece.coupe_a_deg, piece.coupe_b_deg) if a < 90)
    if retrait >= piece.longueur_mm:
        raise ErreurProfile("Les coupes en biais se rejoignent : la longueur est trop courte pour cette section.")
    return piece


def imbriquer_barres(pieces, section, quantites=None, trait_mm=3.0, marge_bout_mm=0.0, taux_chute=0):
    """Débits `pieces` (PieceProfile de la même section) placés dans les barres de la section. `quantites` : {id: quantité}
    pour chiffrer avec d'autres quantités que celles des pièces. Lève ErreurProfile si un débit dépasse la barre."""
    quantites = quantites or {}
    barre_mm = float(section.longueur_barre_mm)
    utile = barre_mm - marge_bout_mm
    unites = []
    for p in pieces:
        verifier_piece(p)
        if p.longueur_mm > utile + 1e-6:
            raise ErreurProfile(f"« {p.nom} » ({p.longueur_mm:g} mm) ne tient pas dans une barre de {barre_mm:g} mm.")
        unites += [p] * int(quantites.get(p.pk, p.quantite))
    unites.sort(key=lambda p: -p.longueur_mm)
    barres = []
    for p in unites:
        place = next((b for b in barres if b.occupe_mm + p.longueur_mm <= barre_mm + 1e-6), None)
        if place is None:
            place = Barre(occupe_mm=marge_bout_mm)
            barres.append(place)
        place.pieces.append((p.pk, place.occupe_mm, p.longueur_mm))
        place.occupe_mm += p.longueur_mm + trait_mm
    # le dernier trait de scie n'existe que s'il reste du métal après le dernier débit
    for b in barres:
        if b.occupe_mm > barre_mm:
            b.occupe_mm = barre_mm
    barres.sort(key=lambda b: -b.occupe_mm)  # la barre la moins remplie est la dernière : c'est elle qui est entamée
    pieces_mm = sum(p.longueur_mm * int(quantites.get(p.pk, p.quantite)) for p in pieces)
    chute_bout = barre_mm - barres[-1].occupe_mm if barres else 0.0
    consommee = len(barres) * barre_mm - chute_bout
    facturee = pieces_mm + max(consommee - pieces_mm, 0.0) * float(D(1) - D(taux_chute) / 100)
    prix, erreur, cout = None, "", None
    try:
        prix = section.prix_au_metre()
        cout = arrondir(D(facturee) / D(1000) * prix)
    except ErreurPrixProfile as exc:
        erreur = str(exc)
    par_piece = []
    for p in pieces:
        q = int(quantites.get(p.pk, p.quantite))
        part = p.longueur_mm * q / pieces_mm if pieces_mm else 0
        total = arrondir(cout * D(part)) if cout is not None else None
        par_piece.append({"piece": p, "quantite": q, "longueur_mm": p.longueur_mm * q, "total": total, "unitaire": arrondir_prix(total / D(q)) if total is not None and q else None})
    return ResultatBarres(barre_mm, barres, pieces_mm, consommee, chute_bout, facturee, prix, cout, par_piece, erreur)


def groupes(pieces):
    """[(section, [pièces])] dans l'ordre des sections."""
    resultat = {}
    for p in pieces:
        resultat.setdefault(p.section_id, (p.section, []))[1].append(p)
    return list(resultat.values())


def cout_matiere_piece_profile(piece, quantite):
    """Coût matière (Decimal) de `quantite` exemplaires de `piece` dans l'imbrication des débits de sa section, les autres
    débits gardant leur quantité. Lève ErreurProfile / ErreurPrixProfile."""
    autres = list(piece.devis.pieces_profile.filter(section=piece.section).select_related("section"))
    r = imbriquer_barres(autres, piece.section, {piece.pk: int(quantite)}, piece.trait_scie_mm, piece.marge_bout_mm, piece.taux_chute_recuperable)
    if r.cout_total is None:
        raise ErreurPrixProfile(r.erreur_prix)
    return next(x["total"] for x in r.par_piece if x["piece"].pk == piece.pk)


# ------------------------------------------------------------------ dessins
def svg_section(section, taille=120):
    """Coupe du profilé (contour en bleu)."""
    d, f = section.dimensions, section.famille
    style = 'fill="#bfdbfe" stroke="#2563eb" stroke-width="1.5" fill-rule="evenodd"'
    if f == "tube_rond":
        D_, e = float(d["d"]), float(d["e"])
        k = (taille - 12) / D_
        c = taille / 2
        corps = f'<path d="M {c - D_ * k / 2},{c} a {D_ * k / 2},{D_ * k / 2} 0 1,0 {D_ * k},0 a {D_ * k / 2},{D_ * k / 2} 0 1,0 {-D_ * k},0 Z M {c - (D_ - 2 * e) * k / 2},{c} a {(D_ - 2 * e) * k / 2},{(D_ - 2 * e) * k / 2} 0 1,0 {(D_ - 2 * e) * k},0 a {(D_ - 2 * e) * k / 2},{(D_ - 2 * e) * k / 2} 0 1,0 {-(D_ - 2 * e) * k},0 Z" {style}/>'
        return f'<svg viewBox="0 0 {taille} {taille}" width="{taille}" height="{taille}" xmlns="http://www.w3.org/2000/svg">{corps}</svg>'
    if f in ("tube_carre", "tube_rectangulaire"):
        h = float(d.get("h") or d["c"]); b = float(d.get("b") or d["c"]); e = float(d["e"])
        pts = [(0, 0, b, h), (e, e, b - 2 * e, h - 2 * e)]
        k = (taille - 12) / max(h, b)
        ox, oy = (taille - b * k) / 2, (taille - h * k) / 2
        chemin = " ".join(f"M {ox + x * k:.2f},{oy + y * k:.2f} h {w * k:.2f} v {hh * k:.2f} h {-w * k:.2f} Z" for x, y, w, hh in pts)
        return f'<svg viewBox="0 0 {taille} {taille}" width="{taille}" height="{taille}" xmlns="http://www.w3.org/2000/svg"><path d="{chemin}" {style}/></svg>'
    if f in ("plat", "carre_plein", "rond_plein"):
        largeur = float(d.get("l") or d.get("c") or d["d"]); epaisseur = float(d.get("e") or d.get("c") or d["d"])
        k = (taille - 12) / max(largeur, epaisseur)
        ox, oy = (taille - largeur * k) / 2, (taille - epaisseur * k) / 2
        if f == "rond_plein":
            r = largeur * k / 2
            corps = f'<circle cx="{taille / 2}" cy="{taille / 2}" r="{r:.2f}" {style}/>'
        else:
            corps = f'<rect x="{ox:.2f}" y="{oy:.2f}" width="{largeur * k:.2f}" height="{epaisseur * k:.2f}" {style}/>'
        return f'<svg viewBox="0 0 {taille} {taille}" width="{taille}" height="{taille}" xmlns="http://www.w3.org/2000/svg">{corps}</svg>'
    if f in ("ipe", "hea", "heb"):
        h, b, tw, tf = float(d["h"]), float(d["b"]), float(d["tw"]), float(d["tf"])
        x0, x1 = (b - tw) / 2, (b + tw) / 2
        pts = [(0, 0), (b, 0), (b, tf), (x1, tf), (x1, h - tf), (b, h - tf), (b, h), (0, h), (0, h - tf), (x0, h - tf), (x0, tf), (0, tf)]
        k = (taille - 12) / max(h, b)
        ox, oy = (taille - b * k) / 2, (taille - h * k) / 2
        pts_svg = " ".join(f"{ox + x * k:.2f},{oy + (h - y) * k:.2f}" for x, y in pts)
        return f'<svg viewBox="0 0 {taille} {taille}" width="{taille}" height="{taille}" xmlns="http://www.w3.org/2000/svg"><polygon points="{pts_svg}" {style}/></svg>'
    if f in ("corniere", "corniere_inegale"):
        a, b, e = float(d["a"]), float(d["b"]), float(d["e"])
        pts = [(0, 0), (b, 0), (b, e), (e, e), (e, a), (0, a)]
        k = (taille - 12) / max(a, b)
        ox, oy = (taille - b * k) / 2, (taille - a * k) / 2
        pts_svg = " ".join(f"{ox + x * k:.2f},{oy + (a - y) * k:.2f}" for x, y in pts)
        return f'<svg viewBox="0 0 {taille} {taille}" width="{taille}" height="{taille}" xmlns="http://www.w3.org/2000/svg"><polygon points="{pts_svg}" {style}/></svg>'
    # UPN : âme à gauche, semelles vers la droite
    h, b, tw, tf = float(d["h"]), float(d["b"]), float(d["tw"]), float(d["tf"])
    pts = [(0, 0), (b, 0), (b, tf), (tw, tf), (tw, h - tf), (b, h - tf), (b, h), (0, h)]
    k = (taille - 12) / max(h, b)
    ox, oy = (taille - b * k) / 2, (taille - h * k) / 2
    pts_svg = " ".join(f"{ox + x * k:.2f},{oy + (h - y) * k:.2f}" for x, y in pts)
    return f'<svg viewBox="0 0 {taille} {taille}" width="{taille}" height="{taille}" xmlns="http://www.w3.org/2000/svg"><polygon points="{pts_svg}" {style}/></svg>'


def svg_debit(longueur_mm, hauteur_mm, coupe_a_deg, coupe_b_deg, largeur_px=330):
    """Vue de côté d'un débit avec ses coupes d'extrémité."""
    marge = 14
    echelle = (largeur_px - 2 * marge) / max(longueur_mm, 1)
    h = max(min(hauteur_mm * echelle, 70), 24)
    ra = h / math.tan(math.radians(coupe_a_deg)) if coupe_a_deg < 90 else 0.0
    rb = h / math.tan(math.radians(coupe_b_deg)) if coupe_b_deg < 90 else 0.0
    L = largeur_px - 2 * marge
    pts = f"{marge + ra:.1f},{marge + h:.1f} {marge + L:.1f},{marge + h:.1f} {marge + L - rb:.1f},{marge:.1f} {marge:.1f},{marge:.1f}"
    return (
        f'<svg viewBox="0 0 {largeur_px} {h + 2 * marge + 22:.0f}" width="100%" xmlns="http://www.w3.org/2000/svg">'
        f'<polygon points="{pts}" fill="#bfdbfe" stroke="#2563eb" stroke-width="2"/>'
        f'<text x="{largeur_px / 2}" y="{marge + h / 2 + 5:.0f}" text-anchor="middle" font-size="14" fill="#374151">{longueur_mm:g}</text>'
        f'<text x="{largeur_px / 2}" y="{marge + h + 18:.0f}" text-anchor="middle" font-size="11" fill="#6b7280">vue de côté — A {coupe_a_deg:g}° · B {coupe_b_deg:g}°</text></svg>'
    )


def svg_barres(resultat, section, pieces_par_id, couleurs):
    """Chaque barre à l'horizontale ; le fond gris est la chute (trait de scie, fond de barre), la chute de bout est hachurée en
    rouge sur la dernière barre."""
    L = resultat.longueur_barre_mm
    H = max(L * 0.045, 120)
    h_sect = section.hauteur_mm
    sorties = []
    for i, b in enumerate(resultat.barres, start=1):
        dernier = i == len(resultat.barres)
        elements = [f'<rect x="0" y="0" width="{L}" height="{H}" fill="#e5e7eb" stroke="#6b7280" stroke-width="5"/>']
        if dernier and resultat.chute_bout_mm > 1:
            x0 = L - resultat.chute_bout_mm
            elements.append(f'<rect x="{x0:.1f}" y="0" width="{resultat.chute_bout_mm:.1f}" height="{H}" fill="url(#dp-hachure-b)"/>')
            elements.append(f'<line x1="{x0:.1f}" x2="{x0:.1f}" y1="0" y2="{H}" stroke="#b91c1c" stroke-width="8" stroke-dasharray="26 16"/>')
        for pid, x, longueur in b.pieces:
            p = pieces_par_id[pid]
            remplissage, trait = couleurs[pid]
            ra = H / math.tan(math.radians(p.coupe_a_deg)) if p.coupe_a_deg < 90 else 0.0
            rb = H / math.tan(math.radians(p.coupe_b_deg)) if p.coupe_b_deg < 90 else 0.0
            ra, rb = min(ra, longueur * 0.45), min(rb, longueur * 0.45)
            pts = f"{x + ra:.1f},{H:.1f} {x + longueur:.1f},{H:.1f} {x + longueur - rb:.1f},0 {x:.1f},0"
            elements.append(f'<polygon points="{pts}" fill="{remplissage}" stroke="{trait}" stroke-width="4"/>')
            elements.append(f'<text x="{x + longueur / 2:.1f}" y="{H / 2 + 22:.1f}" text-anchor="middle" font-size="{H * 0.36:.0f}" fill="#374151">{longueur:g}</text>')
        if dernier and resultat.chute_bout_mm > 1:
            t = H * 0.4
            elements.append(
                f'<text x="{L - resultat.chute_bout_mm / 2:.1f}" y="{H / 2 + t * 0.35:.1f}" text-anchor="middle" font-size="{t:.0f}" font-weight="700" fill="#991b1b" '
                f'paint-order="stroke" stroke="#fff" stroke-width="{t * 0.25:.0f}">chute de bout {resultat.chute_bout_mm:.0f} mm</text>'
            )
        sorties.append({
            "numero": i, "nb": b.nb, "partielle": dernier,
            "svg": (
                f'<svg viewBox="0 0 {L} {H}" class="dp-barre-svg" xmlns="http://www.w3.org/2000/svg"><defs><pattern id="dp-hachure-b" width="60" height="60" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
                f'<rect width="60" height="60" fill="#fee2e2"/><rect width="26" height="60" fill="#f87171"/></pattern></defs>{"".join(elements)}</svg>'
            ),
        })
    return sorties
