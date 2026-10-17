"""Plan PDF d'une pièce pliée d'une demande web : vue de profil cotée (faces et angles) et mise à plat avec chaque
ligne de pli cotée depuis les deux bords du flan."""

import io
import math

from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfgen import canvas

from decoupe.services.pliage import _bande, _recul

PAGE_L, PAGE_H = landscape(A4)
MARGE = 28
BASE_COTE, PAS_COTE = 16, 15  # distance de la 1re ligne de cote au bord de la vue, puis écart entre lignes de cote (pt)


def _fmt(v):
    return f"{round(v, 1):g}".replace(".", ",")


def _unite(v):
    n = math.hypot(*v)
    return (v[0] / n, v[1] / n) if n else (0.0, 0.0)


def _scal(a, b):
    return a[0] * b[0] + a[1] * b[1]


def _profil_saisi(pliage):
    try:
        return [float(f) for f in pliage["faces"]], [float(a) for a in pliage["angles"]]
    except (KeyError, TypeError, ValueError):
        return None


def _profil_flan(lignes, exterieur, epaisseur, ri, coef_k):
    """Faces (cotes extérieures, comme un profil saisi) retrouvées depuis le flan quand tous les plis sont parallèles :
    on retire à chaque bande pliée sa moitié et on rajoute les reculs jusqu'aux arêtes fictives."""
    u = _unite((lignes[0]["p2"][0] - lignes[0]["p1"][0], lignes[0]["p2"][1] - lignes[0]["p1"][1]))
    for l in lignes[1:]:
        v = _unite((l["p2"][0] - l["p1"][0], l["p2"][1] - l["p1"][1]))
        if abs(u[0] * v[1] - u[1] * v[0]) > 0.02:
            return None
    n = (u[1], -u[0])
    s = [_scal(p, n) for p in exterieur]
    plis = sorted(lignes, key=lambda l: _scal(l["p1"], n))
    pos = [_scal(l["p1"], n) - min(s) for l in plis]
    alphas = [float(l["angle"]) for l in plis]
    reculs = [_recul(a, epaisseur, ri) for a in alphas]
    bandes = [_bande(a, epaisseur, ri, coef_k) for a in alphas]
    faces = [pos[0] - bandes[0] / 2 + reculs[0]]
    for i in range(1, len(plis)):
        faces.append(pos[i] - bandes[i] / 2 - (pos[i - 1] + bandes[i - 1] / 2) + reculs[i - 1] + reculs[i])
    faces.append(max(s) - min(s) - (pos[-1] + bandes[-1] / 2) + reculs[-1])
    return faces, [a if l["sens"] == "haut" else -a for a, l in zip(alphas, plis)]


def profil_piece(ligne):
    """(faces, angles) de la vue de profil : le profil saisi par le client, sinon reconstruit depuis le flan (fichier
    STEP aux plis parallèles). None pour une boîte ou des plis non parallèles."""
    pliage = ligne["demande"].get("pliage") or {}
    if pliage.get("forme") == "profil":
        return _profil_saisi(pliage)
    lignes, contour = ligne.get("lignes_pli") or [], ligne.get("contour") or {}
    if pliage or not lignes or not contour.get("exterieur"):
        return None
    from technique.models import Matiere

    matiere = Matiere.objects.filter(nom=ligne["demande"].get("matiere")).select_related("famille").first()
    coef_k = float(matiere.famille.coef_k) if matiere and matiere.famille_id else 0.4
    epaisseur = float(ligne["demande"].get("epaisseur") or 0)
    ri = float(lignes[0].get("rayon") or epaisseur)
    return _profil_flan(lignes, contour["exterieur"], epaisseur, ri, coef_k)


class _Vue:
    """Cadre de la page où l'on dessine des coordonnées en mm, mises à l'échelle et centrées."""

    def __init__(self, points, cadre, marges):
        x0, y0, x1, y1 = cadre
        g, b, d, h = marges
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        w, hh = max(max(xs) - min(xs), 1e-6), max(max(ys) - min(ys), 1e-6)
        self.k = min((x1 - x0 - g - d) / w, (y1 - y0 - b - h) / hh)
        self.ox = x0 + g + (x1 - x0 - g - d - w * self.k) / 2 - min(xs) * self.k
        self.oy = y0 + b + (y1 - y0 - b - h - hh * self.k) / 2 - min(ys) * self.k

    def __call__(self, p):
        return (self.ox + p[0] * self.k, self.oy + p[1] * self.k)


def _fleche(c, pointe, direction):
    ax, ay = direction
    x, y = pointe
    p = c.beginPath()
    p.moveTo(x, y)
    p.lineTo(x - 6 * ax + 2 * ay, y - 6 * ay - 2 * ax)
    p.lineTo(x - 6 * ax - 2 * ay, y - 6 * ay + 2 * ax)
    p.close()
    c.drawPath(p, fill=1, stroke=0)


def _lisible(angle_deg):
    """Angle du texte ramené dans ]-90°, 90°] : il se lit de gauche à droite ou de bas en haut."""
    angle_deg = (angle_deg + 180) % 360 - 180
    if angle_deg > 90.01 or angle_deg <= -90:
        angle_deg += 180 if angle_deg <= -90 else -180
    return angle_deg


def _texte(c, centre, angle_deg, texte, taille=8):
    angle_deg = _lisible(angle_deg)
    c.saveState()
    c.translate(*centre)
    c.rotate(angle_deg)
    c.setFont("Helvetica", taille)
    c.drawCentredString(0, 0, texte)
    c.restoreState()


def _cote(c, a, b, m, w, niveau, valeur):
    """Cote de a à b (points de la page) mesurée selon m, sur une ligne parallèle à m placée à `niveau` le long de w."""
    a2 = (a[0] + (niveau - _scal(a, w)) * w[0], a[1] + (niveau - _scal(a, w)) * w[1])
    b2 = (b[0] + (niveau - _scal(b, w)) * w[0], b[1] + (niveau - _scal(b, w)) * w[1])
    c.setLineWidth(0.4)
    for p, p2 in ((a, a2), (b, b2)):
        if math.dist(p, p2) > 3:
            c.line(p[0] + 2 * w[0], p[1] + 2 * w[1], p2[0] + 3 * w[0], p2[1] + 3 * w[1])
    c.line(*a2, *b2)
    d = _unite((b2[0] - a2[0], b2[1] - a2[1]))
    if d != (0.0, 0.0):
        _fleche(c, b2, d)
        _fleche(c, a2, (-d[0], -d[1]))
    angle = _lisible(math.degrees(math.atan2(m[1], m[0])))
    haut = (-math.sin(math.radians(angle)), math.cos(math.radians(angle)))  # vers le haut des lettres
    ecart = 3 if _scal(haut, w) > 0 else 9  # le texte du côté de w, hors de la ligne de cote
    centre = ((a2[0] + b2[0]) / 2, (a2[1] + b2[1]) / 2)
    largeur = c.stringWidth(_fmt(valeur), "Helvetica", 8)
    if math.dist(a2, b2) < largeur + 16 and d != (0.0, 0.0):  # cote trop courte : texte au-delà de la flèche de départ
        centre = (a2[0] - (largeur / 2 + 8) * d[0], a2[1] - (largeur / 2 + 8) * d[1])
    _texte(c, (centre[0] + ecart * w[0], centre[1] + ecart * w[1]), angle, _fmt(valeur))


def _dessiner_profil(c, faces, angles, cadre, mesure=False):
    pts, direction = [(0.0, 0.0)], 0.0
    dirs = []
    for i, f in enumerate(faces):
        d = (math.cos(direction), math.sin(direction))
        dirs.append(d)
        pts.append((pts[-1][0] + f * d[0], pts[-1][1] + f * d[1]))
        if i < len(angles):  # haut : le bord se relève vers l'observateur du flan, soit un virage à gauche ici
            direction += math.radians(180 - abs(angles[i])) * (1 if angles[i] > 0 else -1)
    vue = _Vue(pts, cadre, (BASE_COTE + 22,) * 4)
    if mesure:
        return vue.k
    page = [vue(p) for p in pts]
    centre = (sum(p[0] for p in page) / len(page), sum(p[1] for p in page) / len(page))
    c.setLineWidth(1.6)
    c.lines([(*page[i], *page[i + 1]) for i in range(len(page) - 1)])
    for i, f in enumerate(faces):
        a, b = page[i], page[i + 1]
        w = (-dirs[i][1], dirs[i][0])
        milieu = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        if _scal((milieu[0] - centre[0], milieu[1] - centre[1]), w) < 0:
            w = (-w[0], -w[1])
        _cote(c, a, b, dirs[i], w, max(_scal(a, w), _scal(b, w)) + BASE_COTE, f)
    c.setLineWidth(0.4)
    for i, angle in enumerate(angles):
        p, avant, apres = page[i + 1], dirs[i], dirs[i + 1]
        retour = (-avant[0], -avant[1])
        bis = _unite((retour[0] + apres[0], retour[1] + apres[1]))
        a0, a1 = math.degrees(math.atan2(apres[1], apres[0])), math.degrees(math.atan2(retour[1], retour[0]))
        ecart = (a1 - a0 + 180) % 360 - 180
        c.arc(p[0] - 9, p[1] - 9, p[0] + 9, p[1] + 9, a0, ecart)
        _texte(c, (p[0] + 22 * bis[0], p[1] + 22 * bis[1] - 3), 0, f"{abs(angle):g}°")


def _groupes(lignes):
    """Lignes de pli regroupées par direction : (u le long des lignes, n perpendiculaire, lignes)."""
    groupes = []
    for l in lignes:
        u = _unite((l["p2"][0] - l["p1"][0], l["p2"][1] - l["p1"][1]))
        if u[1] < -1e-9 or (abs(u[1]) < 1e-9 and u[0] < 0):
            u = (-u[0], -u[1])
        for g in groupes:
            if abs(g[0][0] * u[1] - g[0][1] * u[0]) < 0.02:
                g[2].append(l)
                break
        else:
            n = (u[1], -u[0])
            if n[0] < -1e-9 or (abs(n[0]) < 1e-9 and n[1] < 0):
                n = (-n[0], -n[1])
            groupes.append((u, n, [l]))
    return groupes


def _cote_cote(w):
    return ("haut" if w[1] > 0 else "bas") if abs(w[1]) >= abs(w[0]) else ("droite" if w[0] > 0 else "gauche")


def _dessiner_flan(c, contour, lignes, cadre, mesure=False):
    ext, trous = [tuple(p) for p in contour["exterieur"]], [[tuple(p) for p in t] for t in contour.get("trous") or []]
    # Plis de biais (fichier STEP) : le groupe de plis le plus nombreux est remis à la verticale.
    groupes = _groupes(lignes)
    principal = max(groupes, key=lambda g: len(g[2]))[0] if groupes else (0.0, 1.0)
    t = 0 if min(abs(principal[0]), abs(principal[1])) < 0.02 else math.pi / 2 - math.atan2(principal[1], principal[0])
    rot = lambda p: (p[0] * math.cos(t) - p[1] * math.sin(t), p[0] * math.sin(t) + p[1] * math.cos(t))  # noqa: E731
    ext, trous = [rot(p) for p in ext], [[rot(p) for p in tr] for tr in trous]
    lignes = [{**l, "p1": rot(l["p1"]), "p2": rot(l["p2"])} for l in lignes]

    cotes, niveaux = [], {"haut": 0, "bas": 0, "gauche": 0, "droite": 0}

    def ajouter(a, b, m, w, valeur):
        cote = _cote_cote(w)
        cotes.append((a, b, m, w, niveaux[cote], valeur))
        niveaux[cote] += 1

    for u, n, groupe in _groupes(lignes):
        s = [_scal(p, n) for p in ext]
        for sens, ref in ((u, min(s)), ((-u[0], -u[1]), max(s))):
            # point du bord le plus proche de la ligne de cote : son rappel ne longe pas le bord de la pièce
            bord = max((p for p, sp in zip(ext, s) if abs(sp - ref) < 1e-3), key=lambda p: _scal(p, sens))
            for l in sorted(groupe, key=lambda l: abs(_scal(l["p1"], n) - ref)):
                bout = max((l["p1"], l["p2"]), key=lambda p: _scal(p, sens))
                ajouter(bord, bout, n, sens, abs(_scal(l["p1"], n) - ref))
    xs, ys = [p[0] for p in ext], [p[1] for p in ext]
    ajouter(ext[xs.index(min(xs))], ext[xs.index(max(xs))], (1, 0), (0, 1), max(xs) - min(xs))
    ajouter(ext[ys.index(min(ys))], ext[ys.index(max(ys))], (0, 1), (1, 0), max(ys) - min(ys))

    marge = lambda cote: BASE_COTE + PAS_COTE * niveaux[cote] + 6  # noqa: E731
    vue = _Vue(ext, cadre, (marge("gauche"), marge("bas"), marge("droite"), marge("haut")))
    if mesure:
        return vue.k
    page = [vue(p) for p in ext]
    c.setLineWidth(1.2)
    for poly in [page] + [[vue(p) for p in tr] for tr in trous]:
        chemin = c.beginPath()
        chemin.moveTo(*poly[0])
        for p in poly[1:]:
            chemin.lineTo(*p)
        chemin.close()
        c.drawPath(chemin, stroke=1, fill=0)
    c.saveState()
    c.setStrokeColorRGB(0.75, 0.1, 0.1)
    c.setFillColorRGB(0.75, 0.1, 0.1)
    c.setLineWidth(0.8)
    c.setDash([8, 3, 2, 3])
    for l in lignes:
        a, b = vue(l["p1"]), vue(l["p2"])
        c.line(*a, *b)
        d = _unite((b[0] - a[0], b[1] - a[1]))
        etiquette = f"{l['nom']} · {l['angle']:g}° {l['sens']}" + (f" · Ri {_fmt(l['rayon'])}" if l.get("rayon") else "")
        _texte(c, ((a[0] + b[0]) / 2 - 4 * d[1], (a[1] + b[1]) / 2 + 4 * d[0]), math.degrees(math.atan2(d[1], d[0])), etiquette, 7)
    c.restoreState()
    for a, b, m, w, niveau, valeur in cotes:
        bord = max(_scal(p, w) for p in page)
        _cote(c, vue(a), vue(b), m, w, bord + BASE_COTE + PAS_COTE * niveau, valeur)


def _cadre(zone):
    return (zone[0], zone[1], zone[2], zone[3] - 18)  # sous le titre de la vue


def _titre(c, zone, texte):
    c.setFont("Helvetica-Bold", 10)
    c.drawString(zone[0] + 2, zone[3] - 12, texte)


def plan_pdf(demande, index):
    """PDF A4 paysage du plan de la ligne `index` de la demande (pièce pliée)."""
    ligne = demande.lignes[index]
    d = ligne["demande"]
    lignes = ligne.get("lignes_pli") or []
    tampon = io.BytesIO()
    c = canvas.Canvas(tampon, pagesize=(PAGE_L, PAGE_H))
    c.setTitle(f"Plan {demande.numero}-{index + 1}")
    c.setFont("Helvetica-Bold", 13)
    c.drawString(MARGE, PAGE_H - MARGE - 6, f"Demande {demande.numero} · pièce {index + 1} : {ligne['designation']}"[:110])
    rayon = next((l["rayon"] for l in lignes if l.get("rayon")), None)
    infos = [f"{d.get('matiere')} {_fmt(float(d.get('epaisseur') or 0))} mm", f"{d.get('quantite')} pièce(s)",
             f"Rayon intérieur {_fmt(rayon)} mm" if rayon else "", "Cotes en mm, dessin non à l'échelle"]
    c.setFont("Helvetica", 9)
    c.drawString(MARGE, PAGE_H - MARGE - 22, "   ·   ".join(i for i in infos if i))
    c.setLineWidth(0.5)
    c.line(MARGE, PAGE_H - MARGE - 30, PAGE_L - MARGE, PAGE_H - MARGE - 30)

    haut = PAGE_H - MARGE - 34
    profil, contour = profil_piece(ligne), ligne.get("contour")
    zone_flan = (MARGE, MARGE, PAGE_L - MARGE, haut)
    if profil:
        # Profil au-dessus du flan ou à sa gauche : la disposition qui dessine les deux vues le plus grand.
        milieu, gauche = MARGE + (haut - MARGE) * 0.5, MARGE + (PAGE_L - 2 * MARGE) * 0.42
        dispositions = [((MARGE, milieu + 4, PAGE_L - MARGE, haut), (MARGE, MARGE, PAGE_L - MARGE, milieu - 4)),
                        ((MARGE, MARGE, gauche - 6, haut), (gauche + 6, MARGE, PAGE_L - MARGE, haut))]
        echelle = lambda zp, zf: min(_dessiner_profil(None, *profil, _cadre(zp), mesure=True),  # noqa: E731
                                     _dessiner_flan(None, contour, lignes, _cadre(zf), mesure=True) if contour else math.inf)
        zone_profil, zone_flan = max(dispositions, key=lambda d: echelle(*d))
        _titre(c, zone_profil, "Vue de profil (cotes extérieures)")
        _dessiner_profil(c, *profil, _cadre(zone_profil))
        c.setLineWidth(0.3)
        c.setDash([2, 2])
        if zone_flan[1] == MARGE and zone_flan[0] == MARGE:
            c.line(MARGE, milieu, PAGE_L - MARGE, milieu)
        else:
            c.line(gauche, MARGE, gauche, haut)
        c.setDash([])
    _titre(c, zone_flan, "Mise à plat (flan découpé) : plis cotés depuis chaque bord")
    if contour:
        _dessiner_flan(c, contour, lignes, _cadre(zone_flan))
    c.showPage()
    c.save()
    return tampon.getvalue()
