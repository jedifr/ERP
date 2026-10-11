"""Éditeur de nomenclature d'un article fabriqué (fiche devis, onglet « Lignes du devis », ligne dépliée) : lire et remplacer les
composants. Comme la gamme, la nomenclature appartient à l'ARTICLE : la modifier change aussi les autres devis qui l'utilisent.
(La ligne « matière première » créée depuis une pièce à découper est recalculée à chaque changement de matière ou d'épaisseur de la pièce.)"""

import json

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_http_methods

from .models import Article, Nomenclature


class ErreurNomenclature(Exception):
    pass


def _nombre(valeur, nom, obligatoire=False):
    if valeur in (None, ""):
        if obligatoire:
            raise ErreurNomenclature(f"{nom} : valeur requise.")
        return None
    try:
        return float(str(valeur).replace(",", "."))
    except ValueError as exc:
        raise ErreurNomenclature(f"{nom} : « {valeur} » n'est pas un nombre.") from exc


def etat(article):
    return {
        "article": article.pk,
        "lignes": [
            {"id": n.pk, "composant": n.article_composant_id, "libelle": n.article_composant.libelle, "unite": n.article_composant.unite_cout,
             "quantite": n.quantite, "longueur_mm": n.longueur_mm, "largeur_mm": n.largeur_mm}
            for n in article.composants.select_related("article_composant").order_by("pk")
        ],
    }


@transaction.atomic
def enregistrer(article, lignes):
    """Remplace les composants de l'article par `lignes` ([{id, composant, quantite, longueur_mm, largeur_mm}])."""
    if article.nature != Article.Nature.FABRIQUE:
        raise ErreurNomenclature("Seul un article fabriqué porte une nomenclature.")
    existantes = {n.pk: n for n in article.composants.all()}
    vus = set()
    for position, ligne in enumerate(lignes, start=1):
        composant = Article.objects.filter(pk=ligne.get("composant")).first()
        if composant is None:
            raise ErreurNomenclature(f"Ligne {position} : composant « {ligne.get('composant') or ''} » introuvable.")
        n = existantes.get(ligne.get("id")) or Nomenclature(article_parent=article)
        n.article_composant = composant
        n.quantite = _nombre(ligne.get("quantite"), f"Ligne {position} : quantité", obligatoire=True)
        n.longueur_mm = _nombre(ligne.get("longueur_mm"), f"Ligne {position} : longueur")
        n.largeur_mm = _nombre(ligne.get("largeur_mm"), f"Ligne {position} : largeur")
        try:
            n.full_clean()
        except ValidationError as exc:
            raise ErreurNomenclature(f"Ligne {position} : " + " ; ".join(exc.messages)) from exc
        n.save()
        if n.pk in existantes:
            vus.add(n.pk)
    for pk, n in existantes.items():
        if pk not in vus:
            n.delete()


@require_http_methods(["GET", "POST"])
def nomenclature_editeur_view(request, reference):
    article = get_object_or_404(Article, pk=reference)
    user = request.user
    if not user.has_perm("technique.view_nomenclature"):
        raise PermissionDenied
    if request.method == "GET":
        return JsonResponse(etat(article))
    if not (user.has_perm("technique.add_nomenclature") and user.has_perm("technique.change_nomenclature") and user.has_perm("technique.delete_nomenclature")):
        raise PermissionDenied
    try:
        donnees = json.loads(request.body or b"{}")
        enregistrer(article, donnees.get("lignes") or [])
    except ValueError:
        return JsonResponse({"detail": "Requête illisible."}, status=400)
    except ErreurNomenclature as exc:
        return JsonResponse({"detail": str(exc)}, status=400)
    return JsonResponse(etat(article))
