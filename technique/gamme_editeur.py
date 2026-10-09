"""Éditeur de gamme partagé : lire, modifier, compléter la gamme d'un article fabriqué depuis la fiche d'un devis (et, plus tard,
depuis le constructeur de devis).

Les opérations sont celles de la gamme de l'ARTICLE : elles seront reprises dans les prochains devis de la pièce. L'historique
par date est conservé : modifier une étape ferme l'ancienne (date de fin = veille) et en crée une nouvelle, de sorte que les
devis déjà établis se recalculent avec leurs anciens temps. L'étape « découpe » (origine « decoupe ») est calculée depuis la
pièce : seul son temps de réglage se saisit ici."""

import datetime
import json

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Max
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_http_methods

from .models import Article, Gamme, GammeType, PosteTravail


class ErreurGamme(Exception):
    pass


def _nombre(valeur):
    if valeur in (None, ""):
        return None
    try:
        return float(str(valeur).replace(",", "."))
    except ValueError as exc:
        raise ErreurGamme(f"« {valeur} » n'est pas un nombre.") from exc


def etapes_actives(article, jour=None):
    from chiffrage.moteur import gamme_active

    return list(gamme_active(article, jour or datetime.date.today()).select_related("poste"))


def _cout(etape, quantite, jour):
    from chiffrage.moteur import ChiffrageError, cout_etape_gamme

    try:
        return float(cout_etape_gamme(etape, quantite, jour)), ""
    except ChiffrageError as exc:
        return None, str(exc)


def _ligne(etape, quantite, jour):
    unitaire, probleme = _cout(etape, 1, jour)
    total, _ = _cout(etape, quantite, jour)
    horaire = etape.poste.mode_calcul == PosteTravail.ModeCalcul.HORAIRE
    return {
        "id": etape.pk, "poste": etape.poste_id, "mode": etape.poste.mode_calcul, "origine": etape.origine,
        "temps_fixe": etape.temps_fixe, "temps_variable": etape.temps_variable,
        "cout_forfaitaire": float(etape.cout_forfaitaire) if etape.cout_forfaitaire is not None else None,
        "cout_unitaire": unitaire, "cout_total": total, "probleme": probleme,
        "minutes_par_piece": (etape.temps_variable or 0) + (0 if etape.origine == "decoupe" else (etape.temps_fixe or 0) / max(quantite, 1)) if horaire else None,
        "mise_en_place_tole": etape.poste.temps_mise_en_place_min if etape.origine == "decoupe" else None,
    }


def etat(article, quantite=1, jour=None):
    """Gamme active de l'article, avec les coûts, les postes et les gammes types disponibles."""
    jour = jour or datetime.date.today()
    quantite = max(int(quantite or 1), 1)
    etapes = [_ligne(e, quantite, jour) for e in etapes_actives(article, jour)]
    couts = [e["cout_total"] for e in etapes if e["cout_total"] is not None]
    minutes = [e["minutes_par_piece"] for e in etapes if e["minutes_par_piece"] is not None]
    return {
        "article": article.pk, "quantite": quantite, "etapes": etapes,
        "postes": [{"id": p.pk, "nom": p.nom, "mode": p.mode_calcul} for p in PosteTravail.objects.order_by("nom")],
        "types": [{"id": t.pk, "nom": t.nom, "description": t.description, "etapes": t.etapes.count()} for t in GammeType.objects.order_by("nom")],
        "total_minutes_par_piece": round(sum(minutes), 2), "total_cout_par_piece": round(sum(couts) / quantite, 2) if couts else 0,
        "total_cout": round(sum(couts), 2),
    }


def _valeurs(ligne, poste):
    horaire = poste.mode_calcul == PosteTravail.ModeCalcul.HORAIRE
    if horaire:
        fixe, variable = _nombre(ligne.get("temps_fixe")), _nombre(ligne.get("temps_variable"))
        if fixe is None and variable is None:
            raise ErreurGamme(f"{poste} : saisissez un temps de réglage et/ou un temps par pièce.")
        return {"temps_fixe": fixe or 0.0, "temps_variable": variable or 0.0, "cout_forfaitaire": None}
    forfait = _nombre(ligne.get("cout_forfaitaire"))
    if forfait is None:
        raise ErreurGamme(f"{poste} : saisissez le coût forfaitaire par pièce.")
    return {"temps_fixe": None, "temps_variable": None, "cout_forfaitaire": forfait}


def _sauver(etape):
    try:
        etape.full_clean()
    except ValidationError as exc:
        raise ErreurGamme(" ; ".join(exc.messages)) from exc
    etape.save()
    return etape


@transaction.atomic
def enregistrer(article, lignes, jour=None):
    """Remplace la gamme active par `lignes` (dans l'ordre affiché) en conservant l'historique."""
    jour = jour or datetime.date.today()
    if article.nature != Article.Nature.FABRIQUE:
        raise ErreurGamme("Seul un article fabriqué porte une gamme.")
    actives = {e.pk: e for e in etapes_actives(article, jour)}
    vus = set()
    for position, ligne in enumerate(lignes, start=1):
        poste = PosteTravail.objects.filter(pk=ligne.get("poste")).first()
        ancienne = actives.get(ligne.get("id"))
        if ancienne is not None and ancienne.origine == "decoupe":
            poste = ancienne.poste  # l'étape de découpe suit la machine du paramètre de coupe
        if poste is None:
            raise ErreurGamme(f"Étape {position} : choisissez un poste.")
        if ancienne is not None:
            vus.add(ancienne.pk)
        if ancienne is not None and ancienne.origine == "decoupe":
            valeurs = {"temps_fixe": 0.0, "temps_variable": ancienne.temps_variable, "cout_forfaitaire": None}  # réglage compté par tôle
        else:
            valeurs = _valeurs(ligne, poste)
        origine = ancienne.origine if ancienne is not None else "manuelle"
        if ancienne is not None:
            avant = (ancienne.poste_id, ancienne.ordre, ancienne.temps_fixe, ancienne.temps_variable,
                     None if ancienne.cout_forfaitaire is None else float(ancienne.cout_forfaitaire))
            apres = (poste.pk, position, valeurs["temps_fixe"], valeurs["temps_variable"], valeurs["cout_forfaitaire"])
            if avant == apres:
                continue  # inchangée : pas de nouvelle version dans l'historique
        if ancienne is not None and ancienne.date_debut == jour:
            for champ, valeur in {"poste": poste, "ordre": position, **valeurs}.items():
                setattr(ancienne, champ, valeur)
            _sauver(ancienne)
            continue
        if ancienne is not None:
            ancienne.date_fin = jour - datetime.timedelta(days=1)
            ancienne.save(update_fields=["date_fin"])
        _sauver(Gamme(article=article, poste=poste, ordre=position, date_debut=jour, origine=origine, **valeurs))
    for pk, ancienne in actives.items():
        if pk in vus:
            continue
        if ancienne.origine == "decoupe":
            raise ErreurGamme("L'étape de découpe est calculée depuis la pièce : elle ne se supprime pas ici.")
        if ancienne.date_debut == jour:
            ancienne.delete()
        else:
            ancienne.date_fin = jour - datetime.timedelta(days=1)
            ancienne.save(update_fields=["date_fin"])


@transaction.atomic
def ajouter_gamme_type(article, gamme_type, jour=None):
    """Ajoute à la fin de la gamme de l'article les étapes d'une gamme type (copiées)."""
    jour = jour or datetime.date.today()
    if article.nature != Article.Nature.FABRIQUE:
        raise ErreurGamme("Seul un article fabriqué porte une gamme.")
    ordre = max([e.ordre for e in etapes_actives(article, jour)] or [0])
    for modele in gamme_type.etapes.select_related("poste").order_by("ordre"):
        ordre += 1
        _sauver(Gamme(article=article, poste=modele.poste, ordre=ordre, temps_fixe=modele.temps_fixe, temps_variable=modele.temps_variable,
                      cout_forfaitaire=modele.cout_forfaitaire, date_debut=jour, origine="manuelle"))


@transaction.atomic
def reprendre_gamme(article, source, jour=None):
    """Ajoute à la gamme de `article` les étapes saisies à la main d'un autre article (hors découpe, propre à chaque pièce)."""
    jour = jour or datetime.date.today()
    if article.pk == source.pk:
        raise ErreurGamme("Choisissez une autre pièce.")
    ordre = max([e.ordre for e in etapes_actives(article, jour)] or [0])
    copiees = 0
    for e in etapes_actives(source, jour):
        if e.origine == "decoupe":
            continue
        ordre += 1
        copiees += 1
        _sauver(Gamme(article=article, poste=e.poste, ordre=ordre, temps_fixe=e.temps_fixe, temps_variable=e.temps_variable,
                      cout_forfaitaire=e.cout_forfaitaire, date_debut=jour, origine="manuelle"))
    if not copiees:
        raise ErreurGamme("Cette pièce n'a aucune opération saisie à la main à reprendre.")


@require_http_methods(["GET", "POST"])
def gamme_editeur_view(request, reference):
    """Vue JSON unique de l'éditeur : GET = état ; POST {action: enregistrer | type | reprendre | apercu, …} = modifie puis renvoie l'état."""
    article = get_object_or_404(Article, pk=reference)
    user = request.user
    if not user.has_perm("technique.view_gamme"):
        raise PermissionDenied
    if request.method == "GET":
        return JsonResponse(etat(article, request.GET.get("quantite") or 1))
    if not (user.has_perm("technique.add_gamme") and user.has_perm("technique.change_gamme")):
        raise PermissionDenied
    try:
        donnees = json.loads(request.body or b"{}")
    except ValueError:
        return JsonResponse({"detail": "Requête illisible."}, status=400)
    action = donnees.get("action")
    try:
        if action == "enregistrer":
            enregistrer(article, donnees.get("etapes") or [])
        elif action == "type":
            ajouter_gamme_type(article, get_object_or_404(GammeType, pk=donnees.get("type")))
        elif action == "reprendre":
            ajouter = get_object_or_404(Article, pk=donnees.get("source"))
            reprendre_gamme(article, ajouter)
        else:
            return JsonResponse({"detail": "Action inconnue."}, status=400)
    except ErreurGamme as exc:
        return JsonResponse({"detail": str(exc)}, status=400)
    return JsonResponse(etat(article, donnees.get("quantite") or 1))


@require_http_methods(["GET"])
def gamme_editeur_options_view(request):
    """Postes et gammes types (avec leurs étapes) pour l'éditeur en mode brouillon (constructeur de devis : article pas encore créé)."""
    if not request.user.has_perm("technique.view_gamme"):
        raise PermissionDenied
    return JsonResponse({
        "postes": [{"id": p.pk, "nom": p.nom, "mode": p.mode_calcul} for p in PosteTravail.objects.order_by("nom")],
        "types": [{
            "id": t.pk, "nom": t.nom, "description": t.description, "etapes": t.etapes.count(),
            "lignes": [{"poste": e.poste_id, "mode": e.poste.mode_calcul, "temps_fixe": e.temps_fixe, "temps_variable": e.temps_variable,
                        "cout_forfaitaire": float(e.cout_forfaitaire) if e.cout_forfaitaire is not None else None}
                       for e in t.etapes.select_related("poste").order_by("ordre")],
        } for t in GammeType.objects.order_by("nom")],
    })
