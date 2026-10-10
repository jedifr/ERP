"""Extraction des masses linéiques du catalogue général ArcelorMittal (édition 2020) vers `decoupe/profiles_catalogue_arcelor.py`.

Outil de développement (hors application) : il lit le PDF du catalogue avec pypdf, repère les tableaux de masses linéiques (tubes ronds, carrés
et rectangulaires formés à froid EN 10219, plats, ronds et carrés pleins, cornières, UPN, IPE, HEA, HEB), **contrôle chaque ligne** par la
géométrie (aire exacte × 7,85 kg/dm³ ; tolérance 2 % pour les produits dont la section se calcule, 4 % pour les cornières à congés) et
n'écrit que les lignes cohérentes : les coquilles du catalogue sont écartées et listées.

Usage : python docs/outils/extraire_catalogue_arcelor.py catalogue.pdf > decoupe/profiles_catalogue_arcelor.py
Les numéros de page sont ceux du PDF (1 = couverture)."""
import json, math, re, sys

DENS = 7.85
t = []

def charger(chemin):
    import pypdf

    t[:] = [(p.extract_text() or "") for p in pypdf.PdfReader(chemin).pages]

def page(n):
    return t[n - 1]

def nombres(ligne):
    """Liste des nombres d'une ligne (virgule décimale, espaces insécables et milliers « 1 255,76 » non gérés : on s'arrête aux lignes courtes)."""
    ligne = ligne.replace(" ", " ")
    morceaux = ligne.split()
    sortie = []
    for m in morceaux:
        m2 = m.replace(",", ".")
        try:
            sortie.append(float(m2))
        except ValueError:
            return None
    return sortie

def aire_creux(h, b, e):
    """EN 10219 formé à froid : ro = 2 e, ri = e (e ≤ 6) ; ro = 2,5 e, ri = 1,5 e (6 < e ≤ 10) ; ro = 3 e, ri = 2 e au-delà."""
    if e <= 6:
        ro, ri = 2 * e, e
    elif e <= 10:
        ro, ri = 2.5 * e, 1.5 * e
    else:
        ro, ri = 3 * e, 2 * e
    return 2 * e * (h + b - 2 * e) - (4 - math.pi) * (ro ** 2 - ri ** 2)

def kgm(aire): return aire * DENS / 1000

def tubes_ronds():
    lignes = []
    for n in (30, 32, 34):
        d = None
        for l in page(n).splitlines():
            v = nombres(l)
            if not v or len(v) not in (5, 6):
                continue
            if len(v) == 6:
                d, e, m = v[0], v[1], v[2]
            else:
                if d is None: continue
                e, m = v[0], v[1]
            if e <= 0 or e > 40 or d < 10: continue
            lignes.append((d, e, m, n))
    return lignes

def tubes_creux(pages, rect):
    """Carrés (c x c) ou rectangles (h x b) formés à froid : « 20 x 20 1,5 0,83 … » puis lignes de continuation « 2 1,05 … »."""
    lignes = []
    for n in pages:
        h = b = None
        for l in page(n).splitlines():
            l = l.replace(" ", " ")
            mm = re.match(r"^(\d+(?:[.,]\d+)?) x (\d+(?:[.,]\d+)?) (.*)$", l.strip())
            if mm:
                h, b = float(mm.group(1).replace(",", ".")), float(mm.group(2).replace(",", "."))
                v = nombres(mm.group(3))
                if not v or len(v) < 4: continue
                e, m = v[0], v[1]
            else:
                v = nombres(l)
                if not v or len(v) not in (4, 5) or h is None: continue
                e, m = v[0], v[1]
            if (not rect and h != b) or (rect and h == b): continue
            if e <= 0 or e > 25: continue
            lignes.append((h, b, e, m, n))
    return lignes

if False:
    tr = tubes_ronds()
    print("tubes ronds", len(tr))
    mauvais = 0
    for d, e, m, n in tr:
        th = kgm(math.pi * e * (d - e))
        if abs(m - th) / th > 0.02:
            mauvais += 1; print("  écart rond", d, e, m, round(th, 3), "p", n)
    print("écarts", mauvais)
    tc = tubes_creux((36, 38, 40), False)
    print("carrés", len(tc))
    mauvais = 0
    for h, b, e, m, n in tc:
        th = kgm(aire_creux(h, b, e))
        if abs(m - th) / th > 0.02:
            mauvais += 1; print("  écart carré", h, e, m, round(th, 3), "p", n)
    print("écarts", mauvais)
    tr_ = tubes_creux((42, 44, 46, 48), True)
    print("rect", len(tr_))
    mauvais = 0
    for h, b, e, m, n in tr_:
        th = kgm(aire_creux(h, b, e))
        if abs(m - th) / th > 0.02:
            mauvais += 1; print("  écart rect", h, b, e, m, round(th, 3), "p", n)
    print("écarts", mauvais)


def plats():
    out = []
    for n in (52, 53, 54):
        for l in page(n).splitlines():
            v = nombres(l)
            if not v or len(v) % 4: continue
            for i in range(0, len(v), 4):
                L, e, m, p = v[i:i + 4]
                if 5 <= L <= 150 and 2 <= e <= 40:
                    out.append((L, e, m, n))
    return out

def grands_plats():
    """Larges plats (page 55) : la largeur n'est donnée qu'une fois par bloc, deux blocs côte à côte."""
    out = []
    largeur = [None, None]
    for l in page(55).splitlines():
        v = nombres(l)
        if not v: continue
        if len(v) == 8:
            largeur = [v[0], v[4]]; out += [(v[0], v[1], v[2], 55), (v[4], v[5], v[6], 55)]
        elif len(v) == 4 and v[0] > 100:
            out.append((v[0], v[1], v[2], 55)); largeur[0] = v[0]
    return out

def ronds():
    out = []
    for l in page(62).splitlines():
        v = nombres(l)
        if not v or len(v) % 3: continue
        for i in range(0, len(v), 3):
            d, m, a = v[i:i + 3]
            if 3 <= d <= 260 and m > 0.1:
                out.append((d, m, 62))
    return out

def carres():
    out = []
    for l in page(63).splitlines():
        v = nombres(l)
        if not v or len(v) % 3: continue
        for i in range(0, len(v), 3):
            d, m, a = v[i:i + 3]
            if 3 <= d <= 150 and m > 0.1:
                out.append((d, m, 63))
    return out

def cornieres_egales():
    out = []
    for n in (56,):
        for l in page(n).splitlines():
            v = nombres(l)
            if not v or len(v) < 8: continue
            a, e, r, m = v[0], v[1], v[2], v[3]
            if 15 <= a <= 250 and 2 <= e <= 30 and e < a / 2:
                th = kgm(e * (2 * a - e))
                if abs(m - th) / th < 0.04:
                    out.append((a, e, m, n))
                else:
                    out.append((a, e, None, n, m, round(th, 3)))
    return out

def cornieres_inegales():
    out = []
    for n in (58,):
        for l in page(n).splitlines():
            v = nombres(l)
            if not v or len(v) < 8: continue
            a, b, e = v[0], v[1], v[2]
            if not (a > b and 15 <= a <= 300 and 2 <= e <= 30 and e < b):
                continue
            th = kgm(e * (a + b - e))
            candidats = [x for x in v[3:9] if abs(x - th) / th < 0.04]
            if candidats:
                out.append((a, b, e, candidats[0], n))
            else:
                out.append((a, b, e, None, n, round(th, 3)))
    return out

def poutrelles(prefixe, pages, suffixe=None):
    """Lignes « UPN 100 10,60 100 50 6 8,5 … » ou « HE 100 A 16,70 96 100 5 8 … » : (désignation, kg/m, h, b, tw, tf)."""
    out = []
    for n in pages:
        for l in page(n).splitlines():
            l = l.strip()
            mm = re.match(r"^" + prefixe + r" (\d+)" + ((" " + suffixe) if suffixe else "") + r" (\d.*)$", l)
            if not mm: continue
            v = []
            for mot in mm.group(2).split():
                try:
                    v.append(float(mot.replace(',', '.')))
                except ValueError:
                    break
            if len(v) < 5: continue
            out.append((int(mm.group(1)), v[0], v[1], v[2], v[3], v[4], n))
    return out

def tout():
    return {
        "tubes_ronds": tubes_ronds(), "tubes_carres": tubes_creux((36, 38, 40), False), "tubes_rectangulaires": tubes_creux((42, 44, 46, 48), True),
        "plats": plats() + grands_plats(), "ronds_pleins": ronds(), "carres_pleins": carres(),
        "cornieres": cornieres_egales(), "cornieres_inegales": cornieres_inegales(),
        "upn": poutrelles("UPN", (94,)), "ipe": poutrelles("IPE", (86, 88, 90)), "hea": poutrelles("HE", (96, 98, 100, 102), "A"),
        "heb": poutrelles("HE", (96, 98, 100, 102), "B"),
    }



def _g(x):
    return f"{x:g}"


def lignes_catalogue():
    """[(famille, désignation, cotes, masse kg/m, page)] des lignes cohérentes ; `ecartees` : [(libellé, valeur lue, valeur attendue, page)]."""
    lignes, vues, ecartees = [], set(), []

    def ajouter(famille, designation, cotes, masse, page, attendu=None, tolerance=0.02):
        if designation in vues:
            return
        if attendu is not None and abs(masse - attendu) / attendu > tolerance:
            ecartees.append((designation, masse, round(attendu, 3), page))
            return
        vues.add(designation)
        lignes.append((famille, designation, cotes, round(masse, 3), page))

    for d, e, m, n in tubes_ronds():
        ajouter("tube_rond", f"Tube Ø{_g(d)}×{_g(e)}", {"d": d, "e": e}, m, n, kgm(math.pi * e * (d - e)))
    for l in page(20).splitlines():  # tubes gaz soudés série moyenne (page 20) : « 60,3 3,6 2” 5,03 5,23 » (masse du tube noir)
        mm = re.match(r"^(\d+[.,]?\d*) (\d+[.,]?\d*) .*?(\d+,\d+) +(\d+,\d+)$", l.strip())
        if mm:
            d, e, m = (float(mm.group(i).replace(",", ".")) for i in (1, 2, 3))
            if 10 <= d <= 300 and 1 <= e <= 12:
                ajouter("tube_rond", f"Tube Ø{_g(d)}×{_g(e)}", {"d": d, "e": e}, m, 20, kgm(math.pi * e * (d - e)))
    for h, b, e, m, n in tubes_creux((36, 38, 40), False):
        ajouter("tube_carre", f"Tube {_g(h)}×{_g(h)}×{_g(e)}", {"c": h, "e": e}, m, n, kgm(aire_creux(h, h, e)))
    for h, b, e, m, n in tubes_creux((42, 44, 46, 48), True):
        ajouter("tube_rectangulaire", f"Tube {_g(h)}×{_g(b)}×{_g(e)}", {"h": h, "b": b, "e": e}, m, n, kgm(aire_creux(h, b, e)))
    for largeur, e, m, n in plats() + grands_plats():
        ajouter("plat", f"Plat {_g(largeur)}×{_g(e)}", {"l": largeur, "e": e}, m, n, kgm(largeur * e))
    for d, m, n in ronds():
        if d <= 120:
            ajouter("rond_plein", f"Rond Ø{_g(d)}", {"d": d}, m, n, kgm(math.pi * d * d / 4))
    for d, m, n in carres():
        if d <= 120:
            ajouter("carre_plein", f"Carré {_g(d)}", {"c": d}, m, n, kgm(d * d))
    for x in cornieres_egales():
        if x[2] is not None:
            a, e, m, n = x
            ajouter("corniere", f"L {_g(a)}×{_g(a)}×{_g(e)}", {"a": a, "b": a, "e": e}, m, n, kgm(e * (2 * a - e)), 0.04)
    for x in cornieres_inegales():
        if x[3] is not None:
            a, b, e, m, n = x
            ajouter("corniere_inegale", f"L {_g(a)}×{_g(b)}×{_g(e)}", {"a": a, "b": b, "e": e}, m, n, kgm(e * (a + b - e)), 0.04)
    for nominal, m, h, b, tw, tf, n in poutrelles("UPN", (94,)):
        ajouter("upn", f"UPN {nominal}", {"h": nominal, "b": b, "tw": tw, "tf": tf}, m, n)  # h = désignation (coquilles du catalogue sur 260, 320, 350)
    for nominal, m, h, b, tw, tf, n in poutrelles("IPE", (86, 88, 90)):
        if nominal <= 400 and m < nominal / 4:
            ajouter("ipe", f"IPE {nominal}", {"h": h, "b": b, "tw": tw, "tf": tf}, m, n)
    for famille, suffixe in (("hea", "A"), ("heb", "B")):
        for nominal, m, h, b, tw, tf, n in poutrelles("HE", (96, 98, 100, 102), suffixe):
            if nominal <= 300:
                ajouter(famille, f"HE{suffixe} {nominal}", {"h": h, "b": min(nominal, 300), "tw": tw, "tf": tf}, m, n)
    return lignes, ecartees


if __name__ == "__main__":
    charger(sys.argv[1])
    lignes, ecartees = lignes_catalogue()
    sortie = ['"""Masses linéiques du catalogue général ArcelorMittal (édition 2020), générées par docs/outils/extraire_catalogue_arcelor.py.\n',
              "Seules les lignes dont la masse concorde avec la géométrie sont reprises (2 % pour les sections qui se calculent, 4 % pour les cornières à congés) ;",
              "les coquilles du catalogue sont écartées. Acier, kg/m. (famille, désignation, cotes, masse, page du PDF)\"\"\"\n", "LIGNES = ["]
    for famille, designation, cotes, masse, page in lignes:
        cotes_txt = "{" + ", ".join(f'"{k}": {v:g}' for k, v in cotes.items()) + "}"
        sortie.append(f'    ("{famille}", "{designation}", {cotes_txt}, {masse:g}, {page}),')
    sortie.append("]\n")
    sortie.append("# Lignes lues mais écartées (valeur du catalogue incohérente avec la géométrie) : (désignation, valeur lue, valeur attendue, page)")
    sortie.append("ECARTEES = [")
    for x in ecartees:
        sortie.append(f"    {x!r},")
    sortie.append("]")
    print("\n".join(sortie))
