"""Raccourcis de l'interface : menu « + Nouveau » (en haut de chaque page) et entrées « écrans » de la recherche
globale (Ctrl+K). Une seule liste, filtrée par les droits de l'utilisateur : il ne voit jamais un raccourci
vers un écran qu'il n'a pas le droit d'ouvrir."""

from django.conf import settings
from django.urls import reverse

# (groupe, titre, mots-clés de recherche, nom d'URL, paramètres d'URL, droit requis, icône)
CREATIONS = [
    ("Ventes", "Nouveau devis", "devis chiffrage offre", "admin:chiffrage_devis_add", "", "chiffrage.add_devis", "request_quote"),
    ("Ventes", "Nouvelle commande client", "commande client", "admin:chiffrage_commande_add", "", "chiffrage.add_commande", "shopping_cart"),
    ("Ventes", "Nouvelle livraison", "livraison bl bon de livraison", "admin:chiffrage_livraison_add", "", "chiffrage.add_livraison", "local_shipping"),
    ("Ventes", "Préparer une facture", "facture facturer facturation", "admin:facturation_facture_preparer", "", "facturation.add_facture", "receipt_long"),
    ("Achats", "Nouvelle commande fournisseur", "commande fournisseur achat", "admin:achats_commandefournisseur_add", "", "achats.add_commandefournisseur", "local_shipping"),
    ("Données", "Nouveau client ou fournisseur", "client fournisseur tiers", "admin:commercial_tiers_add", "", "commercial.add_tiers", "handshake"),
    ("Données", "Nouvel article", "article matière fabriqué référence", "admin:technique_article_add", "", "technique.add_article", "category"),
]

# Écrans fréquents, proposés par la recherche (Ctrl+K) : « retard », « relancer », « échec »…
ECRANS = [
    ("Factures en retard", "factures retard impayées relance", "admin:facturation_facture_changelist", "?retard=en_retard", "facturation.view_facture", "schedule"),
    ("Devis sans réponse", "devis relancer sans réponse attente", "admin:chiffrage_devis_changelist", "?statut__exact=valide&issue__exact=en_attente", "chiffrage.view_devis", "hourglass_top"),
    ("Devis en brouillon", "devis brouillon à traiter", "admin:chiffrage_devis_changelist", "?statut__exact=brouillon", "chiffrage.view_devis", "edit_note"),
    ("Ordres de fabrication non transmis", "ordres fabrication of échec planning synchro", "admin:chiffrage_ordrefabrication_changelist", "?statut_synchro__exact=echec_persistant", "chiffrage.view_ordrefabrication", "sync_problem"),
    ("Alertes de stock actives", "alertes stock réapprovisionnement seuil", "admin:stock_alertestock_changelist", "?statut__exact=active", "stock.view_alertestock", "warning"),
]


def _url(nom, parametres):
    return reverse(nom) + parametres


def menu_nouveau(request):
    """Entrées du menu « + Nouveau », groupées, selon les droits : [{"titre", "entrees": [{"titre", "url", "icone"}]}]."""
    utilisateur = getattr(request, "user", None)
    if not getattr(utilisateur, "is_staff", False):
        return []
    groupes = {}
    for groupe, titre, _mots, nom, parametres, droit, icone in CREATIONS:
        if utilisateur.has_perm(droit):
            groupes.setdefault(groupe, []).append({"titre": titre, "url": _url(nom, parametres), "icone": icone})
    return [{"titre": titre, "entrees": entrees} for titre, entrees in groupes.items()]


def raccourcis_ecrans(request):
    """[(titre, description, url, icône, mots-clés)] des écrans que l'utilisateur peut ouvrir (recherche globale)."""
    utilisateur = getattr(request, "user", None)
    if utilisateur is None:
        return []
    resultats = []
    for groupe, titre, mots, nom, parametres, droit, icone in CREATIONS:
        if utilisateur.has_perm(droit):
            resultats.append((titre, "Créer", _url(nom, parametres), "add_circle", f"{titre} {mots} nouveau créer"))
    for titre, mots, nom, parametres, droit, icone in ECRANS:
        if not utilisateur.has_perm(droit):
            continue
        if nom.startswith("admin:stock_") and not settings.STOCK_ACTIF:
            continue
        resultats.append((titre, "Écran", _url(nom, parametres), icone, f"{titre} {mots}"))
    return resultats
