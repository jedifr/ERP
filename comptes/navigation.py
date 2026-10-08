"""Navigation entre documents de même type : liste filtrable (recherche) des documents du modèle courant, avec le précédent et le
suivant. Utilisée par le menu déroulant de l'en-tête des fiches (comptes/static/comptes/navigation_documents.js).

La liste respecte les droits (affichage du modèle), la recherche et l'ordre de l'écran de liste de l'administration ; un écran peut
écarter des lignes par une méthode `queryset_navigation(qs)` (devis : indices remplacés)."""

from django.apps import apps
from django.contrib import admin
from django.contrib.admin.utils import quote
from django.core.exceptions import PermissionDenied
from django.http import Http404, JsonResponse
from django.urls import reverse

LIMITE = 20
LIMITE_VOISINS = 5000
CHAMPS_TIERS = ("client", "tiers", "fournisseur", "nom", "raison_sociale", "libelle", "designation")
CHAMPS_DATE = ("date_creation", "date", "date_commande", "date_facture", "date_livraison", "date_reception")
TONS = {
    "ok": {"valide", "accepte", "termine", "terminee", "paye", "payee", "livre", "livree", "recue", "recu", "cloture", "ok", "emise", "envoyee"},
    "attente": {"brouillon", "en_attente", "a_faire", "en_cours", "a_valider", "ouvert", "ouverte"},
    "ko": {"refuse", "annule", "annulee", "erreur", "perdu", "rejete"},
}


def _ton(valeur):
    for ton, valeurs in TONS.items():
        if str(valeur) in valeurs:
            return ton
    return "neutre"


def _sous_titre(obj):
    parties = []
    for nom in CHAMPS_TIERS:
        if hasattr(obj, nom) and nom != "libelle":
            valeur = getattr(obj, nom, None)
            if valeur not in (None, ""):
                parties.append(str(valeur))
                break
    for nom in CHAMPS_DATE:
        valeur = getattr(obj, nom, None)
        if hasattr(valeur, "strftime"):
            parties.append(valeur.strftime("%d/%m/%Y"))
            break
    return " · ".join(parties)


def _statut(obj):
    brut = getattr(obj, "statut", None)
    if brut in (None, ""):
        return None
    affichage = getattr(obj, "get_statut_display", None)
    libelle = affichage() if callable(affichage) else str(brut)
    return {"libelle": str(libelle), "ton": _ton(brut)}


def navigation_view(request, app, modele):
    try:
        classe = apps.get_model(app, modele)
    except LookupError:
        raise Http404
    modele_admin = admin.site._registry.get(classe)
    if modele_admin is None:
        raise Http404
    if not (modele_admin.has_view_permission(request) or modele_admin.has_change_permission(request)):
        raise PermissionDenied
    actuel = request.GET.get("actuel", "")
    recherche = (request.GET.get("q") or "").strip()

    base = modele_admin.get_queryset(request)
    filtre = getattr(modele_admin, "queryset_navigation", None)
    if callable(filtre):
        base = filtre(base)
    voisins = {"precedent": None, "suivant": None}
    pks = [str(pk) for pk in base.values_list("pk", flat=True)[:LIMITE_VOISINS]]
    if actuel in pks:
        i = pks.index(actuel)
        def url(pk):
            return reverse(f"admin:{app}_{modele}_change", args=[quote(pk)])
        voisins = {"precedent": url(pks[i - 1]) if i > 0 else None, "suivant": url(pks[i + 1]) if i < len(pks) - 1 else None}

    resultats = base
    if recherche and getattr(modele_admin, "search_fields", None):
        resultats, _ = modele_admin.get_search_results(request, base, recherche)
    total = resultats.count()
    items = []
    for obj in resultats[:LIMITE]:
        items.append({
            "pk": str(obj.pk), "label": str(obj), "sous": _sous_titre(obj), "statut": _statut(obj),
            "url": reverse(f"admin:{app}_{modele}_change", args=[quote(obj.pk)]), "courant": str(obj.pk) == actuel,
        })
    return JsonResponse({"items": items, "total": total, "ensemble": len(pks), **voisins})
