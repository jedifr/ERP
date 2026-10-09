"""Centre « À compléter » : tout ce qui manque dans les données et qui fausse un calcul, un document ou une facture
électronique (poste de travail absent d'un paramètre de coupe, matière sans coût, client sans SIRET…).

Chaque contrôle est une petite fonction indépendante qui retourne (total, [(libellé, url), …]) : on voit le nombre et les
premiers éléments à corriger, chacun relié à sa fiche. Un contrôle n'est affiché que si l'utilisateur a le droit de voir
les données concernées ; l'ajout d'un contrôle = une fonction + une ligne dans `CONTROLES`."""

from dataclasses import dataclass
from typing import Callable

from django.conf import settings
from django.contrib import admin
from django.shortcuts import render
from django.urls import reverse

MAX_ELEMENTS = 8
KO = "ko"  # empêche un calcul, un document ou une facture
ATTENTION = "attention"  # résultat possible mais à vérifier


@dataclass(frozen=True)
class Controle:
    cle: str
    gravite: str
    categorie: str
    titre: str
    consequence: str
    droit: str
    liste: str  # nom d'URL de la liste complète
    fonction: Callable
    filtre: str = ""  # paramètres d'URL qui filtrent la liste sur les éléments à corriger
    action: str = ""  # comment corriger en une fois (affiché sous la carte)


def _liens(queryset, nom_url, libelle=str):
    total = queryset.count()
    return total, [(libelle(o), reverse(nom_url, args=[o.pk])) for o in queryset[:MAX_ELEMENTS]]


def _societe():
    from .models import Societe

    s = Societe.charger()
    manques = [nom for nom, valeur in (("SIRET", s.siret), ("TVA intracommunautaire", s.tva_intracommunautaire), ("IBAN", s.iban), ("adresse", s.adresse)) if not valeur]
    return len(manques), [(f"{m} à renseigner", reverse("admin:comptes_societe_changelist")) for m in manques]


def _coupe_sans_poste():
    from decoupe.models import ParametreCoupe

    return _liens(ParametreCoupe.objects.filter(poste__isnull=True), "admin:decoupe_parametrecoupe_change")


def _postes_sans_tarif():
    from technique.models import PosteTravail

    return _liens(PosteTravail.objects.filter(tarifs__isnull=True), "admin:technique_postetravail_change")


def _matieres_sans_cout():
    from technique.models import Article

    return _liens(Article.objects.filter(nature=Article.Nature.MATIERE_PREMIERE, cout_unitaire__isnull=True), "admin:technique_article_change")


def _fabriques_sans_gamme():
    from technique.models import Article

    return _liens(Article.objects.filter(nature=Article.Nature.FABRIQUE, gamme_etapes__isnull=True), "admin:technique_article_change")


def _clients():
    from commercial.models import Tiers

    return Tiers.objects.filter(type_tiers__in=[Tiers.TypeTiers.CLIENT, Tiers.TypeTiers.LES_DEUX])


def _clients_sans_adresse_facturation():
    return _liens(_clients().exclude(adresses__est_facturation=True), "admin:commercial_tiers_change")


def _clients_francais_sans_siret():
    from commercial.models import Tiers

    return _liens(_clients().filter(regime_fiscal__in=[Tiers.RegimeFiscal.FRANCE, Tiers.RegimeFiscal.FRANCE_EXONERE], siret=""), "admin:commercial_tiers_change")


def _normes_a_verifier():
    from decoupe.models import NormeCote, ProfileSection

    return _liens(NormeCote.objects.filter(verifie=False), "admin:decoupe_normecote_change")[0] + ProfileSection.objects.filter(verifie=False).count(), []


CONTROLES = [
    Controle("societe", KO, "Société", "Informations de la société incomplètes", "Elles figurent sur tous les PDF et dans la facture électronique.",
             "comptes.view_societe", "admin:comptes_societe_changelist", _societe),
    Controle("coupe_poste", KO, "Découpe", "Paramètres de coupe sans poste de travail", "Le temps de coupe n'alimente pas la gamme : la pièce est chiffrée sans main-d'œuvre machine.",
             "decoupe.view_parametrecoupe", "admin:decoupe_parametrecoupe_changelist", _coupe_sans_poste,
             "?sans_poste=oui", "Pour tout corriger d'un coup : ouvrez la liste, cochez « tout sélectionner » puis l'action « Affecter un poste de travail »."),
    Controle("poste_tarif", KO, "Atelier", "Postes de travail sans tarif", "Les opérations sur ce poste sont chiffrées à zéro.",
             "technique.view_postetravail", "admin:technique_postetravail_changelist", _postes_sans_tarif),
    Controle("matiere_cout", ATTENTION, "Articles", "Matières premières sans coût", "Le coût matière des devis est faux ou nul.",
             "technique.view_article", "admin:technique_article_changelist", _matieres_sans_cout),
    Controle("fabrique_gamme", ATTENTION, "Articles", "Articles fabriqués sans gamme", "Aucune opération de fabrication n'est chiffrée pour eux.",
             "technique.view_article", "admin:technique_article_changelist", _fabriques_sans_gamme),
    Controle("client_adresse", ATTENTION, "Clients", "Clients sans adresse de facturation", "La facture et le devis n'ont pas d'adresse du client.",
             "commercial.view_tiers", "admin:commercial_tiers_changelist", _clients_sans_adresse_facturation),
    Controle("client_siret", ATTENTION, "Clients", "Clients français sans SIRET", "Obligatoire pour la facturation électronique (Factur-X).",
             "commercial.view_tiers", "admin:commercial_tiers_changelist", _clients_francais_sans_siret),
    Controle("normes", ATTENTION, "Formes et profilés", "Cotes normalisées et sections à vérifier", "Valeurs livrées avec l'application, à contrôler avec la norme avant de lancer une production.",
             "decoupe.view_normecote", "admin:decoupe_normecote_changelist", _normes_a_verifier),
]


def resultats(request):
    """[{controle, total, elements, url}] des contrôles non vides, bloquants d'abord."""
    sortie = []
    for c in CONTROLES:
        if not request.user.has_perm(c.droit):
            continue
        try:
            total, elements = c.fonction()
        except Exception:  # un contrôle en échec ne doit pas empêcher d'afficher les autres
            continue
        if total:
            sortie.append({"controle": c, "total": total, "elements": elements, "plus": max(0, total - len(elements)) if elements else 0, "url": reverse(c.liste) + c.filtre})
    sortie.sort(key=lambda r: 0 if r["controle"].gravite == KO else 1)
    return sortie


def nombre_bloquant(request):
    return sum(r["total"] for r in resultats(request) if r["controle"].gravite == KO)


def nombre_controles(request):
    return len(resultats(request))


def a_completer_view(request):
    res = resultats(request)
    contexte = {
        **admin.site.each_context(request),
        "title": "À compléter",
        "bloquants": [r for r in res if r["controle"].gravite == KO],
        "a_verifier": [r for r in res if r["controle"].gravite == ATTENTION],
    }
    return render(request, "admin/a_completer.html", contexte)
