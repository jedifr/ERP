"""Pièces à découper importées depuis un devis : import d'un DXF/DWG, article fabriqué créé avec la pièce, réglages
(matière, épaisseur, procédé, gaz, quantité) et verdict immédiat (réalisable ? temps de coupe).

Une pièce importée par le devis est liée au devis (`PieceDecoupe.devis`) et à un article fabriqué (`PieceDecoupe.article`)
créé à l'import : la référence suit la codification « Article » si elle est configurée, sinon « <devis>-P01 ». Les
réglages de la pièce sont recopiés sur l'article (matière, épaisseur) et le temps de coupe alimente sa gamme dès que le
paramètre de coupe a un poste de travail."""

import datetime
import re
from pathlib import Path

from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import ProtectedError
from django.utils.text import slugify

from codification.models import RegleCodification
from codification.services import enregistrer_code_utilise, generer_code
from technique.models import Article, Matiere

from ..models import GazCoupe, PieceDecoupe, ProcedeCoupe, ProfilImportDecoupe
from . import formes
from .gamme import alimenter_gamme
from .temps import ErreurTemps, estimer_temps_decoupe


class ErreurPieceDevis(Exception):
    """Import ou réglage refusé (message affichable)."""


def _reference_depuis_nom(nom):
    """Référence d'article tirée du nom (nom du fichier DXF) : caractères sûrs pour une URL, longueur limitée, libre en base."""
    base = re.sub(r"[^\w .,+×Ø()'-]", "-", (nom or "").strip(), flags=re.UNICODE).strip(" .-")[:90]
    if not base:
        return None
    reference, n = base, 2
    while Article.objects.filter(pk=reference).exists():
        reference = f"{base}-{n}"
        n += 1
    return reference


def _reference_article(devis, nom=None):
    """Référence de l'article fabriqué d'une pièce : son nom (nom du fichier DXF) s'il est libre ; à défaut la codification
    « Article » configurée, puis « <devis>-P01 »."""
    reference = _reference_depuis_nom(nom)
    if reference:
        return reference, False
    code = generer_code(RegleCodification.Entite.ARTICLE)
    if code and not Article.objects.filter(pk=code).exists():
        return code, True
    n = 1
    while Article.objects.filter(pk=f"{devis.numero}-P{n:02d}").exists():
        n += 1
    return f"{devis.numero}-P{n:02d}", False


@transaction.atomic
def importer_pour_devis(devis, fichier, profil_import_id=None, procede=ProcedeCoupe.LASER, nom=None, parametres_forme=None):
    """Importe un DXF/DWG dans le devis : pièce, géométrie et article fabriqué. Retourne la pièce (statut « erreur » si la
    géométrie n'a pas pu être lue : la pièce reste visible dans le devis pour être corrigée ou supprimée).
    `nom` et `parametres_forme` servent aux pièces créées depuis la bibliothèque de formes (nom lisible, recette)."""
    extension = fichier.name.rsplit(".", 1)[-1].lower() if "." in fichier.name else ""
    if extension not in (PieceDecoupe.FormatSource.DXF, PieceDecoupe.FormatSource.DWG):
        raise ErreurPieceDevis(f"« {fichier.name} » : seuls les fichiers .dxf et .dwg sont acceptés.")
    profil = ProfilImportDecoupe.objects.filter(pk=profil_import_id).first() if profil_import_id else None
    nom = (nom or Path(fichier.name).stem)[:200]
    piece = PieceDecoupe(
        nom=nom, devis=devis, procede=procede, profil_import=profil, format_source=extension, pas_rotation_deg=90,
        parametres_forme=parametres_forme,
    )
    piece.fichier_source.save(Path(fichier.name).name, ContentFile(fichier.read()), save=False)
    piece.save()
    piece.importer_geometrie()

    reference, par_regle = _reference_article(devis, nom)
    article = Article.objects.create(reference=reference, libelle=nom, nature=Article.Nature.FABRIQUE)
    if par_regle:
        enregistrer_code_utilise(RegleCodification.Entite.ARTICLE, reference)
    piece.article = article
    piece.article_cree_automatiquement = True
    piece.save(update_fields=["article", "article_cree_automatiquement"])
    return piece


def creer_depuis_forme(devis, famille, cotes, quantite=1, procede=ProcedeCoupe.LASER):
    """Crée dans le devis une pièce (et son article fabriqué) depuis la bibliothèque de formes : le contour calculé est écrit
    en DXF comme n'importe quelle pièce importée, et la recette (famille + cotes) est gardée pour pouvoir la modifier."""
    try:
        contour = formes.construire(famille, cotes)
        nom = formes.nom_suggere(famille, cotes)
    except formes.ErreurForme as exc:
        raise ErreurPieceDevis(str(exc))
    fichier = ContentFile(formes.dxf_bytes(contour), name=f"{slugify(nom) or 'forme'}.dxf")
    piece = importer_pour_devis(devis, fichier, procede=procede, nom=nom, parametres_forme={"famille": famille, "cotes": cotes})
    if quantite and int(quantite) != 1:
        piece.quantite = max(1, int(quantite))
        piece.save(update_fields=["quantite"])
    return piece


@transaction.atomic
def modifier_forme(piece, famille, cotes):
    """Recalcule le contour d'une pièce paramétrique avec de nouvelles cotes (fichier DXF et géométrie remplacés). Le nom suit
    la forme tant que l'utilisateur ne l'a pas personnalisé."""
    if not piece.parametres_forme:
        raise ErreurPieceDevis("Cette pièce vient d'un fichier : elle n'a pas de cotes à modifier.")
    try:
        contour = formes.construire(famille, cotes)
        nouveau_nom = formes.nom_suggere(famille, cotes)
    except formes.ErreurForme as exc:
        raise ErreurPieceDevis(str(exc))
    ancienne = piece.parametres_forme
    try:
        ancien_nom = formes.nom_suggere(ancienne["famille"], ancienne["cotes"])
    except (formes.ErreurForme, KeyError):
        ancien_nom = None
    ancien_fichier = piece.fichier_source.name
    piece.fichier_source.save(f"{slugify(nouveau_nom) or 'forme'}.dxf", ContentFile(formes.dxf_bytes(contour)), save=False)
    piece.parametres_forme = {"famille": famille, "cotes": cotes}
    if piece.nom == ancien_nom:
        piece.nom = nouveau_nom[:200]
    piece.save()
    piece.importer_geometrie()
    if ancien_fichier and ancien_fichier != piece.fichier_source.name:
        piece.fichier_source.storage.delete(ancien_fichier)
    article = piece.article
    if article is not None and article.nature == Article.Nature.FABRIQUE and piece.article_cree_automatiquement:
        article.libelle = piece.nom
        article.save(update_fields=["libelle"])
    return piece


def appliquer_reglages(piece, donnees):
    """Enregistre les réglages saisis (matière, épaisseur, procédé, gaz, quantité, nom) et les reporte sur l'article.
    `donnees` : dictionnaire de chaînes (formulaire). Lève ErreurPieceDevis pour une valeur invalide."""
    if "nom" in donnees:
        nom = donnees["nom"].strip()
        if not nom:
            raise ErreurPieceDevis("Le nom de la pièce ne peut pas être vide.")
        piece.nom = nom[:200]
    if "matiere" in donnees:
        piece.matiere = Matiere.objects.filter(pk=donnees["matiere"]).first() if donnees["matiere"] else None
        if donnees["matiere"] and piece.matiere is None:
            raise ErreurPieceDevis(f"Matière inconnue : « {donnees['matiere']} ».")
    if "epaisseur" in donnees:
        brut = donnees["epaisseur"].strip().replace(",", ".")
        try:
            piece.epaisseur = float(brut) if brut else None
        except ValueError:
            raise ErreurPieceDevis("L'épaisseur est un nombre en millimètres.")
        if piece.epaisseur is not None and piece.epaisseur <= 0:
            raise ErreurPieceDevis("L'épaisseur doit être positive.")
    if "procede" in donnees:
        if donnees["procede"] not in ProcedeCoupe.values:
            raise ErreurPieceDevis("Procédé de coupe inconnu.")
        piece.procede = donnees["procede"]
    if "gaz_coupe" in donnees:
        if donnees["gaz_coupe"] and donnees["gaz_coupe"] not in GazCoupe.values:
            raise ErreurPieceDevis("Gaz de coupe inconnu.")
        piece.gaz_coupe = donnees["gaz_coupe"]
    if "rotation" in donnees:
        if donnees["rotation"] and donnees["rotation"] not in {str(v) for v in PieceDecoupe.PasRotation.values}:
            raise ErreurPieceDevis("Pas de rotation inconnu.")
        piece.pas_rotation_deg = int(donnees["rotation"]) if donnees["rotation"] else None
    if "quantite" in donnees:
        try:
            piece.quantite = int(float(donnees["quantite"].replace(",", ".")))
        except ValueError:
            raise ErreurPieceDevis("La quantité est un nombre entier.")
        if piece.quantite < 1:
            raise ErreurPieceDevis("La quantité doit être au moins 1.")
    with transaction.atomic():
        piece.save()
        article = piece.article
        if article is not None and article.nature == Article.Nature.FABRIQUE:
            article.matiere, article.epaisseur, article.libelle = piece.matiere, piece.epaisseur, piece.nom
            article.save(update_fields=["matiere", "epaisseur", "libelle"])
    return piece


def _date_gamme(piece):
    """Date de début de la gamme : celle du devis si elle est antérieure à aujourd'hui, pour que le devis chiffre avec sa gamme."""
    aujourdhui = datetime.date.today()
    if piece.devis_id and piece.devis.date_creation:
        return min(aujourdhui, piece.devis.date_creation)
    return aujourdhui


def verdict(piece, alimenter=False):
    """Verdict affichable : {ok, message, temps_min, avertissements, gamme}. Avec `alimenter`, le temps calculé alimente la
    gamme de l'article (jamais lors du simple affichage de la fiche)."""
    if piece.statut == PieceDecoupe.Statut.ERREUR:
        return {"ok": False, "message": piece.message_erreur or "Fichier illisible.", "temps_min": None, "avertissements": [], "gamme": ""}
    if not piece.matiere_id or not piece.epaisseur:
        return {"ok": None, "message": "Choisissez la matière et l'épaisseur.", "temps_min": None, "avertissements": [], "gamme": ""}
    try:
        estimation = estimer_temps_decoupe(piece)
    except ErreurTemps as exc:
        return {"ok": False, "message": str(exc), "temps_min": None, "avertissements": [], "gamme": ""}
    gamme = ""
    if alimenter and piece.article_id and piece.article.nature == Article.Nature.FABRIQUE:
        try:
            etape, _ = alimenter_gamme(piece, aujourdhui=_date_gamme(piece))
            gamme = f"Gamme de {piece.article_id} : étape {etape.ordre} sur {etape.poste}."
        except ErreurTemps as exc:
            gamme = str(exc)
    return {
        "ok": True, "message": "Réalisable", "temps_min": round(estimation.total_min, 2),
        "avertissements": [a for a in estimation.avertissements if not a.startswith("Vitesses calculées")],
        "gamme": gamme,
    }


def supprimer(piece):
    """Retire la pièce du devis. L'article créé à l'import est supprimé avec elle s'il n'est utilisé nulle part ; sinon il
    reste (retourne alors son nom pour prévenir l'utilisateur)."""
    article = piece.article if piece.article_cree_automatiquement else None
    conserve = None
    with transaction.atomic():
        piece.delete()
        if article is not None:
            try:
                with transaction.atomic():
                    article.delete()
            except ProtectedError:
                conserve = article.pk
    return conserve


def pieces_du_devis(devis):
    return list(devis.pieces_decoupe.select_related("matiere", "article").order_by("pk"))
