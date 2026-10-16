"""Pièces de tôle modèles écrites en STEP avec OpenCascade, pour les essais du dépliage (web.services.step)."""

import math
import tempfile
from pathlib import Path


def _section(faces, angles, e, ri):
    """Contour fermé de la section d'un profil plié : faces (longueurs droites de la peau gauche), angles β signés
    (degrés, + = vers la gauche), épaisseur e, rayon intérieur ri. Retourne une liste de segments ('l', p1, p2) ou
    ('a', p1, pm, p2) en 2D."""
    gauche, droite = [], []  # segments de chaque peau, dans le sens du parcours
    x, y, cap = 0.0, 0.0, 0.0

    def normale_g(c):
        return (-math.sin(c), math.cos(c))

    for k, longueur in enumerate(faces):
        dx, dy = math.cos(cap), math.sin(cap)
        ng = normale_g(cap)
        p1, p2 = (x, y), (x + dx * longueur, y + dy * longueur)
        gauche.append(("l", p1, p2))
        droite.append(("l", (p1[0] - ng[0] * e, p1[1] - ng[1] * e), (p2[0] - ng[0] * e, p2[1] - ng[1] * e)))
        x, y = p2
        if k < len(angles):
            b = math.radians(angles[k])
            s = 1 if b > 0 else -1
            rg = ri if s > 0 else ri + e  # rayon de la peau gauche
            centre = (x + ng[0] * rg * s, y + ng[1] * rg * s)

            def sur(r, a):
                return (centre[0] + r * math.cos(a), centre[1] + r * math.sin(a))

            a0 = math.atan2(y - centre[1], x - centre[0])
            a1 = a0 + b
            am = a0 + b / 2
            rd = rg + e if s > 0 else rg - e
            gauche.append(("a", sur(rg, a0), sur(rg, am), sur(rg, a1)))
            droite.append(("a", sur(rd, a0), sur(rd, am), sur(rd, a1)))
            x, y = sur(rg, a1)
            cap += b
    retour = [(s[0], *reversed(s[1:])) for s in reversed(droite)]
    fin = (gauche[-1][-1], retour[0][1])
    debut = (retour[-1][-1], gauche[0][1])
    return gauche + [("l", *fin)] + retour + [("l", *debut)]


def profil(faces, angles, e, ri, longueur, trous=()):
    """Solide d'un profil plié extrudé sur `longueur` (axe z), avec des trous [(x, y, z, axe, diametre)] traversants."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeWire
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder, BRepPrimAPI_MakePrism
    from OCP.GC import GC_MakeArcOfCircle, GC_MakeSegment
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt, gp_Vec

    fil = BRepBuilderAPI_MakeWire()
    for s in _section(faces, angles, e, ri):
        pts = [gp_Pnt(p[0], p[1], 0) for p in s[1:]]
        courbe = GC_MakeSegment(*pts).Value() if s[0] == "l" else GC_MakeArcOfCircle(*pts).Value()
        fil.Add(BRepBuilderAPI_MakeEdge(courbe).Edge())
    face = BRepBuilderAPI_MakeFace(fil.Wire()).Face()
    solide = BRepPrimAPI_MakePrism(face, gp_Vec(0, 0, longueur)).Shape()
    for x, y, z, axe, d in trous:
        a = gp_Dir(*axe)
        base = gp_Pnt(x - axe[0] * 1000, y - axe[1] * 1000, z - axe[2] * 1000)
        solide = BRepAlgoAPI_Cut(solide, BRepPrimAPI_MakeCylinder(gp_Ax2(base, a), d / 2, 2000).Shape()).Shape()
    return solide


def plaque(largeur, hauteur, e, trous=()):
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    solide = BRepPrimAPI_MakeBox(largeur, hauteur, e).Shape()
    for x, y, d in trous:
        solide = BRepAlgoAPI_Cut(solide, BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(x, y, -1), gp_Dir(0, 0, 1)), d / 2, e + 2).Shape()).Shape()
    return solide


def bossage():
    """Plaque de 10 mm portant un bossage de 20 mm : pas une tôle d'épaisseur constante."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt

    return BRepAlgoAPI_Fuse(BRepPrimAPI_MakeBox(100, 80, 10).Shape(), BRepPrimAPI_MakeBox(gp_Pnt(20, 20, 10), 40, 40, 20).Shape()).Shape()


def ecrire_step(solide):
    """Octets du fichier STEP (AP214) du solide."""
    from OCP.Interface import Interface_Static
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

    from OCP.Message import Message

    messager = Message.DefaultMessenger_s()
    for imprimante in list(messager.Printers()):  # pas de statistiques d'écriture dans la console
        messager.RemovePrinter(imprimante)
    Interface_Static.SetCVal_s("write.step.schema", "AP214IS")
    w = STEPControl_Writer()
    w.Transfer(solide, STEPControl_AsIs)
    with tempfile.TemporaryDirectory() as tmp:
        chemin = str(Path(tmp) / "piece.step")
        w.Write(chemin)
        return Path(chemin).read_bytes()


def assembler(*solides):
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound

    b, c = BRep_Builder(), TopoDS_Compound()
    b.MakeCompound(c)
    for s in solides:
        b.Add(c, s)
    return c


def bac(longueur, largeur, hauteur, e, ri):
    """Bac plié : fond `longueur` × `largeur` (cotes droites de la peau intérieure), quatre bords de `hauteur`, coins crantés."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
    from OCP.BRepBndLib import BRepBndLib
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec
    from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain

    u1 = profil([hauteur, longueur, hauteur], [90, 90], e, ri, largeur)
    u2 = profil([hauteur, largeur, hauteur], [90, 90], e, ri, longueur)
    rot = gp_Trsf()
    rot.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(1, 0, 0)), math.pi / 2)
    u2 = BRepBuilderAPI_Transform(u2, rot).Shape()

    def centre(s):
        b = Bnd_Box()
        BRepBndLib.Add_s(s, b, False)
        mn, mx = b.CornerMin(), b.CornerMax()
        return ((mn.X() + mx.X()) / 2, (mn.Y() + mx.Y()) / 2, (mn.Z() + mx.Z()) / 2)

    c1, c2 = centre(u1), centre(u2)
    dep = gp_Trsf()
    dep.SetTranslation(gp_Vec(0, c1[1] - c2[1], c1[2] - c2[2]))
    u2 = BRepBuilderAPI_Transform(u2, dep).Shape()
    u = ShapeUpgrade_UnifySameDomain(BRepAlgoAPI_Fuse(u1, u2).Shape())
    u.Build()
    return u.Shape()
