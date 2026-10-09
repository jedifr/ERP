"""Accueil « Aujourd'hui » : ce qui demande une action, selon le profil de l'utilisateur, et la pastille des entrées du menu.

Le profil se déduit des groupes de droits (voir `comptes/groupes.py`) : commercial, atelier, comptabilité, direction.
Un administrateur (ou la direction) voit tout ; un utilisateur sans groupe connu voit les cartes que ses droits permettent.
Chaque carte n'apparaît que si l'utilisateur peut ouvrir l'écran vers lequel elle mène."""

import datetime

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

PROFILS = {
    "commercial": {"Commercial", "Responsable commercial"},
    "atelier": {"Atelier", "Méthodes et bureau d'études", "Magasinier", "Responsable stock"},
    "comptabilite": {"Facturation", "Responsable facturation", "Comptabilité", "Achats"},
    "direction": {"Direction"},
}
LIBELLES = {"commercial": "Commercial", "atelier": "Atelier", "comptabilite": "Comptabilité", "direction": "Direction"}


def profils(utilisateur):
    """Profils de l'utilisateur ({'direction'} pour un superutilisateur ; ensemble vide = non déterminé)."""
    if getattr(utilisateur, "is_superuser", False):
        return {"direction"}
    groupes = set(utilisateur.groups.values_list("name", flat=True))
    return {profil for profil, noms in PROFILS.items() if groupes & noms}


def _devis():
    from chiffrage.models import Devis

    return Devis


def _n_devis_brouillon():
    return _devis().objects.filter(statut="brouillon").count()


def _n_devis_a_relancer():
    Devis = _devis()
    limite = timezone.localdate() + datetime.timedelta(days=7)
    return Devis.objects.filter(statut="valide", issue="en_attente", date_validite__isnull=False, date_validite__lte=limite).count()


def _n_commandes_en_cours():
    from chiffrage.models import Commande

    return Commande.objects.filter(statut="en_cours").count()


def _n_of_echec():
    from chiffrage.models import OrdreFabrication

    if not settings.PLANNING_API_URL:
        return 0
    return OrdreFabrication.objects.filter(statut_synchro="echec_persistant").count()


def _n_pieces_erreur():
    from decoupe.models import PieceDecoupe

    return PieceDecoupe.objects.filter(statut="erreur").count()


def _n_alertes_stock():
    from stock.models import AlerteStock

    return AlerteStock.objects.filter(statut="active").count() if settings.STOCK_ACTIF else 0


def _factures_a_payer():
    from facturation.models import Facture

    return Facture.objects.filter(type_document="facture").exclude(statut_paiement="paye")


def _n_factures_retard():
    return sum(1 for f in _factures_a_payer().select_related("commande__client__conditions_paiement") if f.est_en_retard)


def _n_factures_a_payer():
    return _factures_a_payer().count()


# (profils concernés, titre, note, icône, ton, droit, nom d'URL + paramètres, fonction de comptage)
CARTES = [
    ({"commercial", "direction"}, "Devis à relancer", "expirés ou expirant sous 7 jours", "hourglass_top", "attention", "chiffrage.view_devis",
     "admin:chiffrage_devis_changelist", "?validite=bientot", _n_devis_a_relancer),
    ({"commercial", "direction"}, "Devis en brouillon", "à terminer et envoyer", "edit_note", "normal", "chiffrage.view_devis",
     "admin:chiffrage_devis_changelist", "?statut__exact=brouillon", _n_devis_brouillon),
    ({"commercial", "atelier", "direction"}, "Commandes en cours", "à produire ou à livrer", "shopping_cart", "normal", "chiffrage.view_commande",
     "admin:chiffrage_commande_changelist", "?statut__exact=en_cours", _n_commandes_en_cours),
    ({"atelier", "direction"}, "Ordres non transmis au planning", "échec de synchronisation", "sync_problem", "ko", "chiffrage.view_ordrefabrication",
     "admin:chiffrage_ordrefabrication_changelist", "?statut_synchro__exact=echec_persistant", _n_of_echec),
    ({"atelier", "direction"}, "Pièces en erreur d'import", "DXF à corriger", "error", "ko", "decoupe.view_piecedecoupe",
     "admin:decoupe_piecedecoupe_changelist", "?statut__exact=erreur", _n_pieces_erreur),
    ({"atelier", "direction"}, "Alertes de stock", "sous le seuil", "warning", "attention", "stock.view_alertestock",
     "admin:stock_alertestock_changelist", "?statut__exact=active", _n_alertes_stock),
    ({"comptabilite", "direction"}, "Factures en retard", "à relancer", "schedule", "ko", "facturation.view_facture",
     "admin:facturation_facture_changelist", "?retard=en_retard", _n_factures_retard),
    ({"comptabilite", "direction"}, "Factures à encaisser", "émises, non soldées", "payments", "normal", "facturation.view_facture",
     "admin:facturation_facture_changelist", "?type_document__exact=facture&statut_paiement__exact=a_payer", _n_factures_a_payer),
]


def cartes_action(request):
    """Cartes à afficher, les plus urgentes d'abord : [{titre, note, icone, ton, nombre, url}]. Les cartes à zéro sont
    réunies à part (`rien_a_signaler`) pour ne pas encombrer l'écran."""
    utilisateur = request.user
    profil = profils(utilisateur)
    cartes, calmes = [], []
    for concernes, titre, note, icone, ton, droit, nom, parametres, compter in CARTES:
        if profil and not (profil & concernes):
            continue
        if not utilisateur.has_perm(droit):
            continue
        if droit.startswith("stock.") and not settings.STOCK_ACTIF:
            continue
        try:
            nombre = compter()
        except Exception:
            continue
        carte = {"titre": titre, "note": note, "icone": icone, "ton": ton, "nombre": nombre, "url": reverse(nom) + parametres}
        (cartes if nombre else calmes).append(carte)
    ordre = {"ko": 0, "attention": 1, "normal": 2}
    cartes.sort(key=lambda c: ordre[c["ton"]])
    return cartes, calmes


def flux(request):
    """Bande « de l'offre à l'encaissement » : le chemin d'une affaire, avec le nombre de dossiers à chaque étape."""
    from chiffrage.models import Commande, Devis
    from facturation.models import Facture

    u = request.user
    etapes = []

    def ajouter(libelle, droit, nom, parametres, nombre):
        if u.has_perm(droit):
            etapes.append({"libelle": libelle, "nombre": nombre, "url": reverse(nom) + parametres})

    ajouter("Brouillon", "chiffrage.view_devis", "admin:chiffrage_devis_changelist", "?statut__exact=brouillon", Devis.objects.filter(statut="brouillon").count())
    ajouter("Envoyé", "chiffrage.view_devis", "admin:chiffrage_devis_changelist", "?statut__exact=valide&issue__exact=en_attente",
            Devis.objects.filter(statut="valide", issue="en_attente").count())
    ajouter("Accepté", "chiffrage.view_devis", "admin:chiffrage_devis_changelist", "?issue__exact=accepte", Devis.objects.filter(issue="accepte").count())
    ajouter("En fabrication", "chiffrage.view_commande", "admin:chiffrage_commande_changelist", "?statut__exact=en_cours",
            Commande.objects.filter(statut="en_cours").count())
    ajouter("Facturé", "facturation.view_facture", "admin:facturation_facture_changelist", "?type_document__exact=facture&statut_paiement__exact=a_payer",
            _n_factures_a_payer())
    ajouter("Payé", "facturation.view_facture", "admin:facturation_facture_changelist", "?type_document__exact=facture&statut_paiement__exact=paye",
            Facture.objects.filter(type_document="facture", statut_paiement="paye").count())
    return etapes


def contexte_accueil(request):
    cartes, calmes = cartes_action(request)
    profil = profils(request.user)
    return {
        "cartes_action": cartes,
        "cartes_calmes": calmes,
        "flux_affaire": flux(request),
        "profil_libelle": " · ".join(LIBELLES[p] for p in ("direction", "commercial", "atelier", "comptabilite") if p in profil),
    }


# ----------------------------------------------------------------------------------------------- pastilles du menu
def _pastille(request, droit, compter):
    if not request.user.has_perm(droit):
        return ""
    try:
        nombre = compter()
    except Exception:
        return ""
    return nombre or ""


def badge_devis(request):
    return _pastille(request, "chiffrage.view_devis", _n_devis_a_relancer)


def badge_factures(request):
    return _pastille(request, "facturation.view_facture", _n_factures_retard)


def badge_of(request):
    return _pastille(request, "chiffrage.view_ordrefabrication", _n_of_echec)


def badge_alertes_stock(request):
    return _pastille(request, "stock.view_alertestock", _n_alertes_stock)


def badge_parametrage(request):
    from .parametrage import nombre_a_completer

    try:
        return nombre_a_completer(request) or ""
    except Exception:
        return ""
