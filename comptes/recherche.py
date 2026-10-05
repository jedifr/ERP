"""Recherche globale (Ctrl+K, UNFOLD["COMMAND"]["search_callback"]) : une seule barre pour retrouver un document
(devis, commande, livraison, ordre de fabrication, facture, article, client, commande fournisseur) par son numéro,
son client, sa référence… et pour ouvrir un écran fréquent (« factures en retard », « nouveau devis »).

Les résultats respectent les droits : on ne cherche que dans les modèles que l'utilisateur a le droit de voir,
avec les mêmes champs de recherche que les listes de l'admin."""

import logging
import unicodedata

from django.contrib import admin
from django.urls import reverse
from unfold.dataclasses import SearchResult

from .raccourcis import raccourcis_ecrans

logger = logging.getLogger(__name__)

PAR_MODELE = 5
LONGUEUR_MIN = 2

# (modèle « app.Modele », icône, détail affiché sous le titre)
DOCUMENTS = [
    ("chiffrage.Devis", "request_quote", lambda o: f"{o.client} · {o.get_statut_display()}"),
    ("chiffrage.Commande", "shopping_cart", lambda o: f"{o.client} · {o.get_statut_display()}"),
    ("chiffrage.Livraison", "local_shipping", lambda o: f"Commande {o.commande_id} · {o.get_statut_display()}"),
    ("chiffrage.OrdreFabrication", "build", lambda o: f"{o.article_id} × {o.quantite:g} · commande {o.commande_id}"),
    ("facturation.Facture", "receipt_long", lambda o: f"{o.get_type_document_display()} · commande {o.commande_id} · {o.get_statut_paiement_display()}"),
    ("technique.Article", "category", lambda o: o.libelle or o.get_nature_display()),
    ("commercial.Tiers", "handshake", lambda o: o.get_type_tiers_display()),
    ("achats.CommandeFournisseur", "local_shipping", lambda o: f"{o.fournisseur} · commande fournisseur"),
]


def _sans_accents(texte):
    return "".join(c for c in unicodedata.normalize("NFD", texte.lower()) if unicodedata.category(c) != "Mn")


def _ecrans(request, terme):
    mots = _sans_accents(terme).split()
    resultats = []
    for titre, description, url, icone, cles in raccourcis_ecrans(request):
        cible = _sans_accents(cles)
        if all(m in cible for m in mots):
            resultats.append(SearchResult(title=titre, description=description, link=url, icon=icone))
    return resultats


def _documents(request, terme):
    from django.apps import apps

    resultats = []
    for etiquette, icone, detail in DOCUMENTS:
        try:
            modele = apps.get_model(etiquette)
            admin_modele = admin.site._registry.get(modele)
            if admin_modele is None or not admin_modele.has_view_permission(request):
                continue
            trouves, _ = admin_modele.get_search_results(request, admin_modele.get_queryset(request), terme)
            nom = modele._meta.verbose_name.capitalize()
            for objet in trouves[:PAR_MODELE]:
                try:
                    complement = detail(objet)
                except Exception:  # un détail illisible ne doit pas casser la recherche
                    complement = ""
                lien = reverse(f"admin:{modele._meta.app_label}_{modele._meta.model_name}_change", args=[objet.pk])
                resultats.append(
                    SearchResult(title=str(objet), description=f"{nom} — {complement}" if complement else nom, link=lien, icon=icone)
                )
        except Exception:
            logger.exception("Recherche globale : modèle %s ignoré", etiquette)
    return resultats


def recherche_globale(request, terme):
    terme = (terme or "").strip()
    if len(terme) < LONGUEUR_MIN:
        return []
    return _ecrans(request, terme) + _documents(request, terme)
