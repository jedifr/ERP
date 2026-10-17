"""Fichier STEP envoyé depuis le site client : l'ERP lit le solide (OpenCascade, paquet `cadquery-ocp-novtk`), vérifie que
c'est une tôle d'épaisseur constante, mesure l'épaisseur, repère les plis et déplie la pièce pour obtenir le flan à découper.
Le navigateur n'affiche que l'aperçu 3D : le flan, les plis et le prix viennent d'ici.

Dépliage : on parcourt une peau de la tôle (faces planes et cylindriques qui se raccordent en tangence) à partir de sa plus
grande face plane. Chaque face plane garde sa forme ; chaque pli (face cylindrique) devient une bande de la largeur du
développé neutre, et ce qui le suit est ramené dans le plan. Les trous, y compris à cheval sur un pli, suivent.

La largeur de bande se calcule au chiffrage, avec l'outillage de l'atelier (rayon intérieur du vé retenu, coefficient K de
la matière), pas avec le rayon du modèle : arc neutre β × (ri + K × e), corrigé de l'écart entre le rayon du modèle et celui
de l'atelier (2 × (r modèle − ri) × tan(β / 2) au-delà de 90°, 2 × (r modèle − ri) en deçà), pour garder les cotes
extérieures du modèle — même convention que le développé d'un profil (decoupe.services.pliage.developpe).

Non dépliable en ligne (refus, demande de devis) : plusieurs solides, épaisseur variable, surfaces gauches (cônes, tores,
surfaces libres) sur la peau, plis qui se referment en boucle."""

import math
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

FORMATS = (".step", ".stp")
TOLERANCE_EPAISSEUR = 0.02  # 2 % d'écart d'épaisseur admis entre deux points de la peau (+ 0,05 mm)
FLECHE_MM = 0.05  # écart maxi entre un arc du fichier et les segments qui le remplacent dans le flan
K_APERCU = 0.4  # coefficient K du flan d'aperçu (avant choix de la matière)


class ErreurStep(Exception):
    pass


@dataclass
class Pli:
    """Un pli du modèle : angle de pliage β (0 = à plat, 90 = équerre), rayon intérieur du modèle, longueur de pli et
    cotes extérieures des faces qui l'encadrent (jusqu'à l'arête fictive)."""

    beta_deg: float
    rayon_mm: float
    longueur_mm: float
    faces_mm: tuple = ()
    axe: tuple = (0, 0, 1)
    sens: str = "haut"  # côté où se relève ce qui suit le pli, vu du flan (côté de la face de départ)

    @property
    def angle(self):
        """Angle de la pièce au sens de l'atelier : 180 − β (90 = équerre)."""
        return round(180 - self.beta_deg, 2)


@dataclass
class AnalyseStep:
    epaisseur_mm: float = None
    plis: list = field(default_factory=list)
    encombrement_mm: tuple = None  # boîte englobante x, y, z
    volume_mm3: float = None
    avertissements: list = field(default_factory=list)


# ------------------------------------------------------------------ lecture
def lire(contenu):
    """Solide du fichier STEP (octets). Lève ErreurStep si le fichier est illisible ou contient plusieurs pièces."""
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_Reader
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer

    with tempfile.NamedTemporaryFile(suffix=".step") as tmp:
        tmp.write(contenu)
        tmp.flush()
        lecteur = STEPControl_Reader()
        if lecteur.ReadFile(tmp.name) != IFSelect_RetDone:
            raise ErreurStep("Fichier STEP illisible : exportez-le de nouveau depuis votre logiciel de CAO (AP203 ou AP214).")
        lecteur.TransferRoots()
        forme = lecteur.OneShape()
    solides = []
    exp = TopExp_Explorer(forme, TopAbs_SOLID)
    while exp.More():
        solides.append(exp.Current())
        exp.Next()
    if not solides:
        raise ErreurStep("Le fichier STEP ne contient pas de solide (surfaces seules ?) : exportez la pièce en volume.")
    if len(solides) > 1:
        raise ErreurStep(f"Le fichier contient {len(solides)} pièces séparées : envoyez un fichier par pièce.")
    return solides[0]


# ------------------------------------------------------------------ géométrie élémentaire
def _vec(v):
    return (v.X(), v.Y(), v.Z())


def _sous(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _plus(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _fois(a, k):
    return (a[0] * k, a[1] * k, a[2] * k)


def _scal(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _vect(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _norme(a):
    return math.sqrt(_scal(a, a))


def _unitaire(a):
    n = _norme(a)
    return _fois(a, 1 / n) if n > 1e-12 else a


class _Faces:
    """Faces du solide, numérotées, avec leurs voisines par arête."""

    def __init__(self, solide):
        from OCP.BRepMesh import BRepMesh_IncrementalMesh
        from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
        from OCP.TopExp import TopExp
        from OCP.TopoDS import TopoDS
        from OCP.OCP.collections import (
            IndexedDataMap_TopoDS_Shape_List_TopoDS_Shape_TopTools_ShapeMapHasher as TopTools_IndexedDataMapOfShapeListOfShape,
            IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher as TopTools_IndexedMapOfShape,
        )

        self.solide = solide
        BRepMesh_IncrementalMesh(solide, 0.5, False, 0.5, True)
        carte = TopTools_IndexedMapOfShape()
        TopExp.MapShapes_s(solide, TopAbs_FACE, carte)
        self.faces = [TopoDS.Face(carte.FindKey(i)) for i in range(1, carte.Extent() + 1)]
        self._index = carte
        aretes = TopTools_IndexedDataMapOfShapeListOfShape()
        TopExp.MapShapesAndAncestors_s(solide, TopAbs_EDGE, TopAbs_FACE, aretes)
        self.voisines = [[] for _ in self.faces]  # [(indice de la voisine, arête)]
        for i in range(1, aretes.Extent() + 1):
            arete = TopoDS.Edge(aretes.FindKey(i))
            liees = [carte.FindIndex(f) - 1 for f in aretes.FindFromIndex(i)]
            liees = list(dict.fromkeys(liees))
            for a in liees:
                for b in liees:
                    if a != b:
                        self.voisines[a].append((b, arete))

    def surface(self, i):
        from OCP.BRepAdaptor import BRepAdaptor_Surface

        return BRepAdaptor_Surface(self.faces[i])

    def type(self, i):
        from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Plane

        t = self.surface(i).GetType()
        return "plan" if t == GeomAbs_Plane else "cylindre" if t == GeomAbs_Cylinder else "autre"

    def aire(self, i):
        from OCP.BRepGProp import BRepGProp
        from OCP.GProp import GProp_GProps

        p = GProp_GProps()
        BRepGProp.SurfaceProperties_s(self.faces[i], p)
        return p.Mass()

    def normale(self, i, point):
        """Normale sortante de la face i au point (mm) donné, supposé sur la face."""
        from OCP.BRep import BRep_Tool
        from OCP.BRepLProp import BRepLProp_SLProps
        from OCP.gp import gp_Pnt
        from OCP.ShapeAnalysis import ShapeAnalysis_Surface
        from OCP.TopAbs import TopAbs_REVERSED

        face = self.faces[i]
        uv = ShapeAnalysis_Surface(BRep_Tool.Surface_s(face)).ValueOfUV(gp_Pnt(*point), 1e-6)
        props = BRepLProp_SLProps(self.surface(i), uv.X(), uv.Y(), 1, 1e-6)
        if not props.IsNormalDefined():
            return None
        n = _vec(props.Normal())
        return _fois(n, -1) if face.Orientation() == TopAbs_REVERSED else n

    def point_interieur(self, i):
        """Un point situé sur la face (centre du plus grand triangle de son maillage)."""
        from OCP.BRep import BRep_Tool
        from OCP.TopLoc import TopLoc_Location

        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(self.faces[i], loc)
        if tri is None or tri.NbTriangles() == 0:
            return None
        trsf = loc.Transformation()
        meilleur, aire_max = None, -1
        for k in range(1, tri.NbTriangles() + 1):
            a, b, c = tri.Triangle(k).Get()
            pa, pb, pc = (_vec(tri.Node(n).Transformed(trsf)) for n in (a, b, c))
            aire = _norme(_vect(_sous(pb, pa), _sous(pc, pa)))
            if aire > aire_max:
                meilleur, aire_max = _fois(_plus(_plus(pa, pb), pc), 1 / 3), aire
        if meilleur is not None and self.type(i) != "plan":
            # le centre d'un triangle est sur la corde : on le ramène sur la surface
            from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
            from OCP.gp import gp_Pnt

            proj = GeomAPI_ProjectPointOnSurf(gp_Pnt(*meilleur), BRep_Tool.Surface_s(self.faces[i]))
            if proj.NbPoints():
                meilleur = _vec(proj.NearestPoint())
        return meilleur


def _milieu_arete(arete):
    from OCP.BRepAdaptor import BRepAdaptor_Curve

    c = BRepAdaptor_Curve(arete)
    return _vec(c.Value((c.FirstParameter() + c.LastParameter()) / 2))


def _est_droite(arete):
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GeomAbs import GeomAbs_Line

    return BRepAdaptor_Curve(arete).GetType() == GeomAbs_Line


def _epaisseur_locale(solide, point, normale):
    """Distance entre un point de la peau et la peau opposée, le long de la normale rentrante."""
    from OCP.gp import gp_Dir, gp_Lin, gp_Pnt
    from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector

    inter = IntCurvesFace_ShapeIntersector()
    inter.Load(solide, 1e-6)
    inter.Perform(gp_Lin(gp_Pnt(*point), gp_Dir(*_fois(normale, -1))), 1e-4, 1e6)
    params = [inter.WParameter(k) for k in range(1, inter.NbPnt() + 1)]
    params = [p for p in params if p > 1e-3]
    return min(params) if params else None


def _points_arete(arete, face_orientation_inverse=False):
    """Points d'une arête dans son sens de parcours (fleche FLECHE_MM)."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GCPnts import GCPnts_QuasiUniformDeflection
    from OCP.GeomAbs import GeomAbs_Line
    from OCP.TopAbs import TopAbs_REVERSED

    c = BRepAdaptor_Curve(arete)
    debut, fin = c.FirstParameter(), c.LastParameter()
    if c.GetType() == GeomAbs_Line:
        pts = [_vec(c.Value(debut)), _vec(c.Value(fin))]
    else:
        d = GCPnts_QuasiUniformDeflection(c, FLECHE_MM, debut, fin)
        pts = [_vec(d.Value(k)) for k in range(1, d.NbPoints() + 1)] if d.IsDone() and d.NbPoints() >= 2 else \
            [_vec(c.Value(debut + (fin - debut) * k / 16)) for k in range(17)]
    if arete.Orientation() == TopAbs_REVERSED:
        pts.reverse()
    return pts


def _contours_face(face):
    """Contours (extérieur d'abord) d'une face : listes de points 3D."""
    from OCP.BRepTools import BRepTools, BRepTools_WireExplorer
    from OCP.TopAbs import TopAbs_WIRE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    exterieur = BRepTools.OuterWire_s(face)
    fils = [exterieur]
    exp = TopExp_Explorer(face, TopAbs_WIRE)
    while exp.More():
        w = TopoDS.Wire(exp.Current())
        if not w.IsSame(exterieur):
            fils.append(w)
        exp.Next()
    contours = []
    for w in fils:
        pts = []
        we = BRepTools_WireExplorer(w, face)
        while we.More():
            morceau = _points_arete(we.Current())
            if pts and morceau and _norme(_sous(pts[-1], morceau[0])) < 1e-6:
                morceau = morceau[1:]
            pts.extend(morceau)
            we.Next()
        if len(pts) >= 3:
            contours.append(pts)
    return contours


# ------------------------------------------------------------------ transformations rigides (matrice 3 × 3 + translation)
def _rotation(axe, angle):
    x, y, z = _unitaire(axe)
    c, s, t = math.cos(angle), math.sin(angle), 1 - math.cos(angle)
    return ((t * x * x + c, t * x * y - s * z, t * x * z + s * y),
            (t * x * y + s * z, t * y * y + c, t * y * z - s * x),
            (t * x * z - s * y, t * y * z + s * x, t * z * z + c))


def _appliquer_m(m, v):
    return (_scal(m[0], v), _scal(m[1], v), _scal(m[2], v))


def _produit_m(a, b):
    colonnes = list(zip(*b))
    return tuple(tuple(_scal(ligne, col) for col in colonnes) for ligne in a)


IDENTITE = (((1, 0, 0), (0, 1, 0), (0, 0, 1)), (0, 0, 0))


def _composer(t1, t2):
    """t1 ∘ t2 (on applique t2, puis t1)."""
    m1, d1 = t1
    m2, d2 = t2
    return (_produit_m(m1, m2), _plus(_appliquer_m(m1, d2), d1))


def _transformer(t, p):
    return _plus(_appliquer_m(t[0], p), t[1])


def _rotation_autour(origine, axe, angle):
    m = _rotation(axe, angle)
    return (m, _sous(origine, _appliquer_m(m, origine)))


# ------------------------------------------------------------------ analyse
class _Modele:
    def __init__(self, solide):
        self.f = _Faces(solide)
        f = self.f
        self.types = [f.type(i) for i in range(len(f.faces))]
        self.aires = [f.aire(i) for i in range(len(f.faces))]
        plans = sorted((i for i, t in enumerate(self.types) if t == "plan"), key=lambda i: -self.aires[i])
        if not plans:
            raise ErreurStep("La pièce n'a aucune face plane : ce n'est pas une tôle (demandez un devis).")

        # Épaisseur : distance de chaque grande face plane à la face opposée, pondérée par l'aire.
        votes = {}
        for i in plans[:40]:
            p = f.point_interieur(i)
            n = f.normale(i, p) if p else None
            e = _epaisseur_locale(solide, p, n) if n else None
            if e:
                votes[round(e, 2)] = votes.get(round(e, 2), 0) + self.aires[i]
        if not votes:
            raise ErreurStep("Épaisseur de la tôle introuvable dans le fichier.")
        self.epaisseur = max(votes, key=votes.get)

        # Peau : la plus grande face dont l'épaisseur est celle de la tôle, puis ses voisines raccordées en tangence.
        depart = next(i for i in plans if self._epaisseur_face(i) is not None and abs(self._epaisseur_face(i) - self.epaisseur) <= self._tolerance())
        self.depart = depart
        self.peau = self._peau(depart)
        for i in self.peau:
            if self.types[i] == "autre":
                raise ErreurStep("La pièce comporte des surfaces gauches (cônes, sphères, formes libres) : dépliage sur devis.")
            e = self._epaisseur_face(i)
            if e is None or abs(e - self.epaisseur) > self._tolerance():
                raise self._non_tole()
        # Le volume doit être celui d'une tôle : épaisseur × aire moyenne des deux peaux (écarte bossages, lamages, nervures).
        from OCP.BRepGProp import BRepGProp
        from OCP.GProp import GProp_GProps

        props = GProp_GProps()
        BRepGProp.VolumeProperties_s(solide, props)
        opposee = [i for i, t in enumerate(self.types) if i not in self.peau and t != "autre"
                   and (lambda e: e is not None and abs(e - self.epaisseur) <= self._tolerance())(self._epaisseur_face(i))]
        attendu = self.epaisseur * (sum(self.aires[i] for i in self.peau) + sum(self.aires[i] for i in opposee)) / 2
        if abs(props.Mass() - attendu) > 0.02 * props.Mass():
            raise self._non_tole()

    @staticmethod
    def _non_tole():
        return ErreurStep("La pièce n'est pas une tôle d'épaisseur constante (usinage, bossage ?) : envoyez-la avec une demande de devis.")

    def _tolerance(self):
        return self.epaisseur * TOLERANCE_EPAISSEUR + 0.05

    def _epaisseur_face(self, i):
        p = self.f.point_interieur(i)
        n = self.f.normale(i, p) if p else None
        return _epaisseur_locale(self.f.solide, p, n) if n else None

    def _peau(self, depart):
        f = self.f
        vus, file = {depart}, [depart]
        while file:
            i = file.pop()
            for j, arete in f.voisines[i]:
                if j in vus:
                    continue
                m = _milieu_arete(arete)
                ni, nj = f.normale(i, m), f.normale(j, m)
                if ni and nj and _scal(ni, nj) > 0.999:
                    vus.add(j)
                    file.append(j)
        return vus

    # -------------------------------------------------- cylindres de la peau
    def cylindre(self, i):
        c = self.f.surface(i).Cylinder()
        pos = c.Position()
        x, y = _vec(pos.XDirection()), _vec(pos.YDirection())
        return {"origine": _vec(pos.Location()), "x": x, "y": y, "axe": _vect(x, y), "rayon": c.Radius()}

    def angle_sur(self, cyl, point):
        r = _sous(point, cyl["origine"])
        return math.atan2(_scal(r, cyl["y"]), _scal(r, cyl["x"]))

    def plage_angle(self, i):
        from OCP.BRepTools import BRepTools

        umin, umax, vmin, vmax = BRepTools.UVBounds_s(self.f.faces[i])
        return umin, umax, vmin, vmax


def _ecart_angle(a, b):
    d = (a - b) % (2 * math.pi)
    return d - 2 * math.pi if d > math.pi else d


class _Depliage:
    """Parcours de la peau : place chaque face dans le plan de la face de départ. `largeur(pli)` donne la largeur de
    bande du pli (mm) ; les plis sont les groupes de faces cylindriques coaxiales de même rayon."""

    def __init__(self, modele):
        self.m = modele
        cylindres = [i for i in modele.peau if modele.types[i] == "cylindre"]
        self.cyl = {i: modele.cylindre(i) for i in cylindres}
        # Groupes de plis : faces cylindriques coaxiales et de même rayon (un pli coupé en morceaux par un trou ou une encoche).
        self.groupe = {}
        groupes = []
        for i in cylindres:
            if i in self.groupe:
                continue
            self.groupe[i] = len(groupes)
            g = [i]
            for j in cylindres:
                if j not in self.groupe and self._coaxial(self.cyl[i], self.cyl[j]):
                    self.groupe[j] = len(groupes)
                    g.append(j)
            groupes.append(g)
        self.groupes = groupes
        self.plis = [self._pli(g) for g in groupes]

    @staticmethod
    def _coaxial(a, b):
        if abs(a["rayon"] - b["rayon"]) > 1e-4 or abs(abs(_scal(a["axe"], b["axe"])) - 1) > 1e-6:
            return False
        d = _sous(b["origine"], a["origine"])
        return _norme(_vect(d, a["axe"])) < 1e-3

    def _pli(self, g):
        m = self.m
        e = m.epaisseur
        c = self.cyl[g[0]]
        # Angle total : réunion des plages angulaires des morceaux (même axe), longueur : étendue le long de l'axe.
        bornes = []
        for i in g:
            umin, umax, vmin, vmax = m.plage_angle(i)
            bornes.append((umin, umax, vmin, vmax))
        beta = max(b[1] for b in bornes) - min(b[0] for b in bornes)
        longueur = max(b[3] for b in bornes) - min(b[2] for b in bornes)
        # Côté de la peau : convexe (peau extérieure, rayon intérieur = r − e) ou concave (rayon intérieur = r).
        p = m.f.point_interieur(g[0])
        n = m.f.normale(g[0], p)
        radial = _sous(_sous(p, c["origine"]), _fois(c["axe"], _scal(_sous(p, c["origine"]), c["axe"])))
        convexe = _scal(n, radial) > 0
        ri = c["rayon"] - e if convexe else c["rayon"]
        # Peau à l'extérieur du pli : il s'éloigne du côté de la peau (vu du flan : vers le bas) ; à l'intérieur : vers le haut.
        return Pli(beta_deg=round(math.degrees(beta), 2), rayon_mm=round(max(ri, 0), 3), longueur_mm=round(longueur, 2),
                   axe=tuple(round(v, 6) for v in c["axe"]), sens="bas" if convexe else "haut")

    def deplier(self, largeur):
        """Polygones 2D (listes [extérieur, trous...]) de chaque face de la peau, dans le repère de la face de départ."""
        m, f = self.m, self.m.f
        largeurs = [largeur(p) for p in self.plis]
        place = {m.depart: IDENTITE}
        self.entree = entree = {}  # face cylindrique → (angle d'entrée u0, sens +1/−1, direction de sortie dans le plan, point d'entrée)
        file = [m.depart]
        while file:
            i = file.pop(0)
            t = place[i]
            for j, arete in f.voisines[i]:
                if j not in m.peau or j in place:
                    continue
                if self.m.types[i] == "plan" and self.m.types[j] == "plan":
                    place[j] = t
                elif self.m.types[j] == "cylindre" and self.m.types[i] == "plan":
                    if not _est_droite(arete):
                        continue
                    place[j] = t
                    entree[j] = self._entree(j, arete)
                elif self.m.types[i] == "cylindre":
                    if not _est_droite(arete):
                        continue
                    sortie = self._sortie(i, arete, largeurs)
                    if sortie is None:
                        continue
                    place[j] = _composer(t, sortie)
                    if self.m.types[j] == "cylindre":
                        entree[j] = self._entree(j, arete)
                else:
                    continue
                file.append(j)
        if len(place) < len(m.peau):
            raise ErreurStep("Une partie de la pièce n'a pas pu être dépliée : dépliage sur devis.")
        polygones = []
        self.bandes = {}  # groupe de pli → faces cylindriques dépliées (la bande du pli à plat)
        for i in sorted(place):
            t = place[i]
            contours = _contours_face(f.faces[i])
            if self.m.types[i] == "cylindre":
                contours = [[self._a_plat(i, p, largeurs) for p in c] for c in contours]
            a_plat = [[_transformer(t, p) for p in c] for c in contours]
            polygones.append(a_plat)
            if self.m.types[i] == "cylindre":
                self.bandes.setdefault(self.groupe[i], []).append(a_plat)
        return polygones

    def _entree(self, j, arete):
        """Géométrie d'entrée dans le pli j par l'arête (génératrice) donnée."""
        c = self.cyl[j]
        milieu = _milieu_arete(arete)
        u0 = self.m.angle_sur(c, milieu)
        umin, umax, _, _ = self.m.plage_angle(j)
        # Le pli s'étend du côté où se trouve le reste de la face.
        u_milieu = (umin + umax) / 2
        sens = 1 if _ecart_angle(u_milieu, u0) > 0 else -1
        tangente = _unitaire(_fois(_plus(_fois(c["x"], -math.sin(u0)), _fois(c["y"], math.cos(u0))), sens))
        return {"u0": u0, "sens": sens, "tangente": tangente, "groupe": self.groupe[j]}

    def _beta_groupe(self, g):
        return math.radians(self.plis[g].beta_deg)

    def _a_plat(self, j, p, largeurs):
        """Point p du pli j ramené à plat dans le repère de la face qui précède le pli."""
        c, e = self.cyl[j], self.entree[j]
        u = self.m.angle_sur(c, p)
        phi = abs(_ecart_angle(u, e["u0"]))
        rel = _sous(p, c["origine"])
        z = _scal(rel, c["axe"])
        radial = (math.cos(e["u0"]), math.sin(e["u0"]))
        generatrice = _plus(_plus(c["origine"], _fois(c["axe"], z)), _fois(_plus(_fois(c["x"], radial[0]), _fois(c["y"], radial[1])), c["rayon"]))
        beta = self._beta_groupe(e["groupe"]) or 1
        return _plus(generatrice, _fois(e["tangente"], phi / beta * largeurs[e["groupe"]]))

    def _sortie(self, i, arete, largeurs):
        """Transformation qui ramène à plat ce qui suit le pli i au-delà de l'arête donnée."""
        if i not in self.entree:
            return None
        c, e = self.cyl[i], self.entree[i]
        u1 = self.m.angle_sur(c, _milieu_arete(arete))
        phi = abs(_ecart_angle(u1, e["u0"]))
        if phi < 1e-6:
            return None  # arête d'entrée
        beta = self._beta_groupe(e["groupe"]) or 1
        # tourner de −φ (dans le sens du pli) autour de l'axe, puis avancer de la largeur de bande parcourue
        rotation = _rotation_autour(c["origine"], c["axe"], -e["sens"] * phi)
        avance = (IDENTITE[0], _fois(e["tangente"], phi / beta * largeurs[e["groupe"]]))
        return _composer(avance, rotation)


def _repere_plan(modele):
    """Origine, axes x, y du plan de la face de départ (normale z sortante)."""
    f = modele.f
    pln = f.surface(modele.depart).Plane()
    pos = pln.Position()
    p = f.point_interieur(modele.depart)
    n = _unitaire(f.normale(modele.depart, p))
    x = _unitaire(_vec(pos.XDirection()))
    y = _vect(n, x)
    return _vec(pos.Location()), x, y, n


def _flan(modele, polygones, depliage=None):
    """Réunion des faces dépliées : contour {exterieur, trous} en mm, recalé à l'origine, et ses lignes de pli (axe de
    chaque bande pliée, angle de la pièce, sens) quand `depliage` est donné."""
    from shapely.geometry import MultiPolygon, Polygon
    from shapely.ops import unary_union

    o, x, y, _ = _repere_plan(modele)
    morceaux = []
    for contours in polygones:
        pts2d = [[(_scal(_sous(p, o), x), _scal(_sous(p, o), y)) for p in c] for c in contours]
        poly = Polygon(pts2d[0], pts2d[1:]).buffer(0)
        if not poly.is_empty:
            morceaux.append(poly)
    # les faces se touchent par leurs arêtes : une soudure de 0,01 mm les réunit, puis on la retire
    union = unary_union([p.buffer(0.01, join_style=2) for p in morceaux]).buffer(-0.01, join_style=2)
    if isinstance(union, MultiPolygon):
        union = max(union.geoms, key=lambda g: g.area)
        modele_avert = "Le flan déplié est en plusieurs morceaux : vérification par l'atelier."
    else:
        modele_avert = None
    union = union.simplify(0.005)
    minx, miny, _, _ = union.bounds
    ext = [(round(px - minx, 4), round(py - miny, 4)) for px, py in list(union.exterior.coords)[:-1]]
    trous = [[(round(px - minx, 4), round(py - miny, 4)) for px, py in list(i.coords)[:-1]] for i in union.interiors]
    contour = {"exterieur": ext, "trous": trous}
    if depliage is not None:
        contour["plis"] = _lignes_pli(depliage, o, x, y, minx, miny)
    return contour, modele_avert


def _lignes_pli(depliage, o, x, y, minx, miny):
    """Axe de chaque bande pliée à plat : la médiane du plus petit rectangle qui l'entoure, dans le sens de la longueur."""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    from decoupe.services.pliage import ligne_pli

    lignes = []
    for g, pli in enumerate(depliage.plis):
        morceaux = []
        for contours in depliage.bandes.get(g, []):
            pts2d = [(_scal(_sous(p, o), x) - minx, _scal(_sous(p, o), y) - miny) for p in contours[0]]
            poly = Polygon(pts2d).buffer(0)
            if not poly.is_empty:
                morceaux.append(poly.buffer(0.01, join_style=2))
        if not morceaux:
            continue
        coins = list(unary_union(morceaux).minimum_rotated_rectangle.exterior.coords)[:4]
        cotes = [(coins[i], coins[(i + 1) % 4]) for i in range(4)]
        court = min(cotes, key=lambda c: math.dist(*c))
        oppose = cotes[(cotes.index(court) + 2) % 4]
        milieu = lambda c: ((c[0][0] + c[1][0]) / 2, (c[0][1] + c[1][1]) / 2)  # noqa: E731
        lignes.append(ligne_pli(f"Pli {g + 1}", milieu(court), milieu(oppose), pli.angle if pli.sens == "haut" else -pli.angle))
    return lignes


# ------------------------------------------------------------------ cotes des faces autour des plis
def _cotes_faces(modele, dep):
    """Pour chaque pli, cotes extérieures (jusqu'à l'arête fictive) des deux faces planes qui l'encadrent."""
    f, e = modele.f, modele.epaisseur
    for g, pli in enumerate(dep.plis):
        beta = math.radians(pli.beta_deg)
        ajout = (pli.rayon_mm + e) * (math.tan(beta / 2) if pli.beta_deg <= 90 else 1)
        cotes = []
        for i in dep.groupes[g]:
            for j, arete in f.voisines[i]:
                if j not in modele.peau or modele.types[j] != "plan" or not _est_droite(arete):
                    continue
                # éloignement maximal de la face j par rapport à la génératrice
                from OCP.BRepAdaptor import BRepAdaptor_Curve

                c = BRepAdaptor_Curve(arete)
                a = _vec(c.Value(c.FirstParameter()))
                d = _unitaire(_sous(_vec(c.Value(c.LastParameter())), a))
                loin = max((_norme(_vect(_sous(p, a), d)) for cont in _contours_face(f.faces[j]) for p in cont), default=0)
                cotes.append(round(loin + ajout, 2))
        pli.faces_mm = tuple(sorted(set(cotes))[:2]) if cotes else ()


# ------------------------------------------------------------------ interface
def largeur_bande(pli, epaisseur, coef_k, ri_atelier=None):
    """Largeur à plat d'un pli : arc neutre à l'outillage de l'atelier, corrigé de l'écart de rayon avec le modèle."""
    beta = math.radians(pli.beta_deg)
    ri = pli.rayon_mm if ri_atelier is None else ri_atelier
    correction = (pli.rayon_mm - ri) * (math.tan(beta / 2) if pli.beta_deg <= 90 else 1)
    return max(beta * (ri + coef_k * epaisseur) + 2 * correction, 0)


class PieceStep:
    """Pièce lue dans un fichier STEP : analyse (épaisseur, plis) puis dépliage à la demande."""

    def __init__(self, contenu):
        from OCP.Bnd import Bnd_Box
        from OCP.BRepBndLib import BRepBndLib
        from OCP.BRepGProp import BRepGProp
        from OCP.GProp import GProp_GProps

        solide = lire(contenu)
        try:
            self.modele = _Modele(solide)
            self.depliage = _Depliage(self.modele)
        except ErreurStep:
            raise
        except Exception:
            raise ErreurStep("La pièce n'a pas pu être analysée : envoyez-la avec une demande de devis.")
        _cotes_faces(self.modele, self.depliage)
        boite = Bnd_Box()
        BRepBndLib.Add_s(solide, boite, False)
        (xmin, ymin, zmin), (xmax, ymax, zmax) = _vec(boite.CornerMin()), _vec(boite.CornerMax())
        props = GProp_GProps()
        BRepGProp.VolumeProperties_s(solide, props)
        self.analyse = AnalyseStep(
            epaisseur_mm=self.modele.epaisseur, plis=self.depliage.plis,
            encombrement_mm=tuple(round(v, 2) for v in (xmax - xmin, ymax - ymin, zmax - zmin)), volume_mm3=props.Mass(),
        )

    def flan(self, coef_k=K_APERCU, ri_atelier=None):
        """Contour du flan déplié et avertissements."""
        e = self.modele.epaisseur
        try:
            polygones = self.depliage.deplier(lambda p: largeur_bande(p, e, coef_k, ri_atelier))
            contour, avert = _flan(self.modele, polygones, self.depliage)
        except ErreurStep:
            raise
        except Exception:
            raise ErreurStep("La pièce n'a pas pu être dépliée : envoyez-la avec une demande de devis.")
        return contour, [avert] if avert else []


def analyser_fichier(chemin):
    return PieceStep(Path(chemin).read_bytes())


def lisible():
    """Vrai si OpenCascade est installé sur le serveur (le site ne propose le STEP que dans ce cas)."""
    try:
        import OCP.STEPControl  # noqa: F401
    except ImportError:
        return False
    return True


def _non_parallele(plis):
    axes = [p.axe for p in plis]
    return any(abs(abs(_scal(a, axes[0])) - 1) > 1e-3 for a in axes[1:])


def deposer_step(nom, contenu, jeton, dossier):
    """Analyse et garde un STEP déposé depuis le site. Retourne la fiche du fichier (voir web.services.dxf.deposer), avec
    l'épaisseur et les plis lus. Lève ErreurDxf pour une pièce non exploitable en ligne."""
    from .dxf import ErreurDxf

    if not lisible():
        raise ErreurDxf("Le format STEP n'est pas encore lu en ligne : envoyez un DXF du flan, ou votre STEP avec une demande de devis.")
    try:
        piece = PieceStep(contenu)
        contour, avertissements = piece.flan()
    except ErreurStep as exc:
        raise ErreurDxf(str(exc))
    a = piece.analyse
    if a.plis and _non_parallele(a.plis):
        avertissements.append("Plis dans plusieurs directions : ordre de pliage et coins à vérifier par un technicien.")
    (dossier / f"{jeton}.step").write_bytes(contenu)
    from shapely.geometry import Polygon

    minx, miny, maxx, maxy = Polygon(contour["exterieur"]).bounds
    return {
        "jeton": jeton, "nom": nom, "format": "step", "contour": contour, "largeur_mm": round(maxx - minx, 2), "hauteur_mm": round(maxy - miny, 2),
        "avertissements": avertissements, "epaisseur_mm": round(a.epaisseur_mm, 2), "encombrement_mm": list(a.encombrement_mm),
        "plis": [{"angle": p.angle, "beta": p.beta_deg, "rayon_mm": p.rayon_mm, "longueur": p.longueur_mm, "faces": list(p.faces_mm)} for p in a.plis],
    }


def flan_atelier(jeton, coef_k, ri_atelier):
    """Flan d'un STEP déposé, déplié avec l'outillage de l'atelier (gardé en cache à côté du fichier)."""
    import json

    from .dxf import ErreurDxf, dossier

    cle = f"{jeton}-flan-k{coef_k:g}-r{ri_atelier:g}-plis.json"  # « -plis » : avec les lignes de pli
    cache = dossier() / cle
    if cache.exists():
        return json.loads(cache.read_text("utf-8"))
    chemin = dossier() / f"{jeton}.step"
    if not chemin.exists():
        raise ErreurDxf("Fichier STEP inconnu : renvoyez-le.")
    try:
        contour, _ = analyser_fichier(chemin).flan(coef_k=coef_k, ri_atelier=ri_atelier)
    except ErreurStep as exc:
        raise ErreurDxf(str(exc))
    cache.write_text(json.dumps(contour), "utf-8")
    return contour
