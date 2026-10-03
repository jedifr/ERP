"""Briques communes des documents PDF (ReportLab) : en-tête société, pied de page numéroté,
filigrane, styles. Les documents eux-mêmes sont dans chiffrage/documents.py."""

import io
from pathlib import Path

from django.conf import settings
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .models import Societe

AMBRE = colors.HexColor("#b45309")
GRIS = colors.HexColor("#6b7280")
GRIS_CLAIR = colors.HexColor("#e5e7eb")


def styles():
    base = getSampleStyleSheet()
    return {
        "normal": ParagraphStyle("normal", parent=base["Normal"], fontName="Helvetica", fontSize=9, leading=12),
        "petit": ParagraphStyle("petit", parent=base["Normal"], fontName="Helvetica", fontSize=7.5, leading=10, textColor=GRIS),
        "gras": ParagraphStyle("gras", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=9, leading=12),
        "titre": ParagraphStyle("titre", parent=base["Title"], fontName="Helvetica-Bold", fontSize=18, leading=22, alignment=0, textColor=AMBRE),
        "droite": ParagraphStyle("droite", parent=base["Normal"], fontName="Helvetica", fontSize=9, leading=12, alignment=2),
        "entete": ParagraphStyle("entete", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=8, leading=10, textColor=colors.white),
        "entete_droite": ParagraphStyle("entete_droite", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=8, leading=10, textColor=colors.white, alignment=2),
    }


def echapper(texte):
    """Texte libre -> balisage Paragraph (le contenu vient d'utilisateurs : on échappe < > &)."""
    return (str(texte or "")).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br/>")


def montant(valeur):
    return f"{valeur:,.2f}".replace(",", " ").replace(".", ",") + " €"


def quantite(valeur):
    return f"{valeur:g}".replace(".", ",")


class _CanvasNumerote(canvas.Canvas):
    """Pied de page « Page x / y » : le total n'est connu qu'une fois toutes les pages écrites."""

    def __init__(self, *args, pied=None, filigrane="", **kwargs):
        super().__init__(*args, **kwargs)
        self._pages = []
        self._pied = pied or []
        self._filigrane = filigrane

    def showPage(self):
        self._pages.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._pages)
        for etat in self._pages:
            self.__dict__.update(etat)
            self._dessiner_decor(total)
            super().showPage()
        super().save()

    def _dessiner_decor(self, total):
        largeur, hauteur = A4
        if self._filigrane:
            self.saveState()
            self.setFont("Helvetica-Bold", 60)
            self.setFillColor(colors.Color(0.85, 0.2, 0.2, alpha=0.12))
            self.translate(largeur / 2, hauteur / 2)
            self.rotate(35)
            self.drawCentredString(0, 0, self._filigrane)
            self.restoreState()
        self.saveState()
        self.setStrokeColor(GRIS_CLAIR)
        self.line(18 * mm, 20 * mm, largeur - 18 * mm, 20 * mm)
        self.setFont("Helvetica", 7)
        self.setFillColor(GRIS)
        y = 16 * mm
        for ligne in self._pied:
            self.drawCentredString(largeur / 2, y, ligne)
            y -= 3.2 * mm
        self.drawRightString(largeur - 18 * mm, 10 * mm, f"Page {self._pageNumber} / {total}")
        self.restoreState()


def _lignes_pied(societe):
    identite = " — ".join(
        p for p in (
            f"{societe.raison_sociale} {societe.forme_juridique}".strip(),
            f"capital {societe.capital}" if societe.capital else "",
            societe.rcs,
        ) if p
    )
    fiscal = " — ".join(
        p for p in (
            f"SIRET {societe.siret}" if societe.siret else "",
            f"TVA {societe.tva_intracommunautaire}" if societe.tva_intracommunautaire else "",
            f"IBAN {societe.iban}" if societe.iban else "",
            f"BIC {societe.bic}" if societe.bic else "",
        ) if p
    )
    return [ligne for ligne in (identite, fiscal) if ligne]


def entete_societe(societe, st):
    """Bloc logo + coordonnées de l'entreprise, en tête de document."""
    coordonnees = [f"<b>{echapper(societe.raison_sociale)}</b>"]
    if societe.adresse:
        coordonnees.append(echapper(societe.adresse))
    if societe.code_postal or societe.ville:
        coordonnees.append(echapper(f"{societe.code_postal} {societe.ville}".strip()))
    if societe.telephone:
        coordonnees.append(echapper(f"Tél. {societe.telephone}"))
    if societe.email:
        coordonnees.append(echapper(societe.email))
    if societe.site_web:
        coordonnees.append(echapper(societe.site_web))
    bloc = Paragraph("<br/>".join(coordonnees), st["normal"])
    cellules = [[bloc, ""]]
    if societe.logo:
        chemin = Path(settings.MEDIA_ROOT) / societe.logo.name
        if chemin.exists():
            logo = Image(str(chemin))
            ratio = min(45 * mm / logo.imageWidth, 22 * mm / logo.imageHeight)
            logo.drawWidth, logo.drawHeight = logo.imageWidth * ratio, logo.imageHeight * ratio
            logo.hAlign = "RIGHT"
            cellules = [[bloc, logo]]
    table = Table(cellules, colWidths=[110 * mm, 64 * mm])
    table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    return table


def construire_pdf(elements, titre, filigrane=""):
    """Assemble le document et renvoie les octets du PDF."""
    societe = Societe.charger()
    tampon = io.BytesIO()
    doc = SimpleDocTemplate(
        tampon, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=15 * mm, bottomMargin=26 * mm,
        title=titre, author=societe.raison_sociale, pageCompression=0,
    )
    pied = _lignes_pied(societe)
    doc.build(elements, canvasmaker=lambda *a, **k: _CanvasNumerote(*a, pied=pied, filigrane=filigrane, **k))
    return tampon.getvalue()


def tableau_lignes(entetes, lignes, largeurs, alignements_droite=()):
    """Tableau de lignes de document, en-tête sombre, fines séparations."""
    st = styles()
    donnees = [
        [Paragraph(echapper(e), st["entete_droite" if i in alignements_droite else "entete"]) for i, e in enumerate(entetes)]
    ] + lignes
    table = Table(donnees, colWidths=largeurs, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#374151")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 1), (-1, -1), 0.25, GRIS_CLAIR),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    for colonne in alignements_droite:
        style.append(("ALIGN", (colonne, 0), (colonne, -1), "RIGHT"))
    table.setStyle(TableStyle(style))
    return table


__all__ = ["Spacer", "Paragraph", "Table", "TableStyle", "styles", "echapper", "montant", "quantite",
           "entete_societe", "construire_pdf", "tableau_lignes", "AMBRE", "GRIS", "GRIS_CLAIR", "mm"]
