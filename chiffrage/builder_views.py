import datetime
import json

from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.views.decorators.http import require_http_methods

from technique.models import Article, PosteTravail

from .builder import ajouter_ligne_devis, creer_article_fabrique
from .models import Devis
from .moteur import ChiffrageError


@staff_member_required
@require_http_methods(["GET", "POST"])
def devis_builder_view(request, numero):
    devis = get_object_or_404(Devis, pk=numero)

    if request.method == "POST":
        return _traiter_ajout_ligne(request, devis)

    context = admin.site.each_context(request)
    context.update(
        {
            "title": f"Constructeur de devis — {devis.numero}",
            "devis": devis,
            "lignes": devis.lignes.select_related("article").prefetch_related("operations__poste"),
            "postes": PosteTravail.objects.all().order_by("nom"),
            "opts": Devis._meta,
        }
    )
    return TemplateResponse(request, "chiffrage/devis_builder.html", context)


def _traiter_ajout_ligne(request, devis):
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"detail": "JSON invalide."}, status=400)

    try:
        quantite = float(payload["quantite"])
    except (KeyError, TypeError, ValueError):
        return JsonResponse({"detail": "Quantité manquante ou invalide."}, status=400)

    try:
        if payload.get("nouvel_article"):
            article = _creer_article_depuis_payload(payload["nouvel_article"])
        else:
            reference = payload.get("article_existant")
            try:
                article = Article.objects.get(pk=reference)
            except Article.DoesNotExist:
                return JsonResponse({"detail": f"Article « {reference} » introuvable."}, status=400)

        ligne = ajouter_ligne_devis(devis, article, quantite)
    except ChiffrageError as exc:
        return JsonResponse({"detail": str(exc)}, status=400)

    return JsonResponse(
        {
            "ok": True,
            "ligne_id": ligne.id,
            "article": article.reference,
            "quantite": ligne.quantite,
        }
    )


def _creer_article_depuis_payload(data):
    try:
        reference = data["reference"]
    except KeyError:
        raise ChiffrageError("Référence du nouvel article manquante.")

    composants = []
    for c in data.get("composants", []):
        try:
            composant_article = Article.objects.get(pk=c["article_composant"])
        except Article.DoesNotExist:
            raise ChiffrageError(f"Composant « {c.get('article_composant')} » introuvable.")
        composants.append(
            {
                "article_composant": composant_article,
                "quantite": c.get("quantite"),
                "longueur_mm": c.get("longueur_mm") or None,
                "largeur_mm": c.get("largeur_mm") or None,
            }
        )

    etapes = []
    for e in data.get("etapes", []):
        try:
            poste = PosteTravail.objects.get(pk=e["poste"])
        except PosteTravail.DoesNotExist:
            raise ChiffrageError(f"Poste « {e.get('poste')} » introuvable.")
        date_debut = e.get("date_debut") or datetime.date.today().isoformat()
        etapes.append(
            {
                "poste": poste,
                "ordre": e.get("ordre"),
                "temps_fixe": e.get("temps_fixe") or None,
                "temps_variable": e.get("temps_variable") or None,
                "cout_forfaitaire": e.get("cout_forfaitaire") or None,
                "date_debut": datetime.date.fromisoformat(date_debut),
            }
        )

    return creer_article_fabrique(
        reference=reference,
        taux_marge_defaut=data.get("taux_marge_defaut") or None,
        composants=composants,
        etapes=etapes,
    )
