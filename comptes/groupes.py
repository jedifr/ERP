"""Rôles métier proposés par défaut (groupes Django et leurs permissions).

Créés une seule fois, au premier `migrate` : un groupe déjà existant n'est jamais
réécrit, ses permissions restent entièrement ajustables dans l'admin
(Utilisateurs → Groupes). Pour rattraper les permissions ajoutées depuis à ces
rôles dans un groupe existant, lancer `python manage.py synchroniser_groupes`
(ajoute seulement, ne retire jamais).

Chaque code est « app.permission ». Les permissions « voir » sur les modèles liés
(client, article, adresse...) sont indispensables : les listes à autocomplétion de
l'admin répondent 403 sans elles.
"""

from django.apps import apps as django_apps

def _crud(app, *modeles, actions=("view", "add", "change", "delete")):
    """Codes « app.action_modele » pour chaque modèle (noms de classe en minuscules)."""
    return [f"{app}.{action}_{modele.lower()}" for modele in modeles for action in actions]


# Permissions de consultation requises par les listes déroulantes à autocomplétion.
_LIENS_COMMERCIAUX = [
    "commercial.view_tiers", "commercial.view_adresse", "commercial.view_contact",
    "commercial.view_devise", "technique.view_article",
]

_COMMERCIAL = [
    *_LIENS_COMMERCIAUX,
    # Créer et modifier ses clients (jamais les supprimer) et leurs coordonnées.
    *_crud("commercial", "Tiers", "Adresse", "Contact", "ContactTelephone", actions=("add", "change")),
    *_crud("commercial", "ConditionPaiement", "Pays", "DelaiPropose", "TauxTVA", actions=("view",)),
    "chiffrage.view_devis", "chiffrage.add_devis", "chiffrage.change_devis",
    "chiffrage.view_devisligne", "chiffrage.add_devisligne", "chiffrage.change_devisligne",
    "chiffrage.delete_devisligne",
    "chiffrage.view_devisligneoperation",
    "chiffrage.view_commande", "chiffrage.add_commande", "chiffrage.change_commande",
    "chiffrage.view_commandeligne", "chiffrage.add_commandeligne", "chiffrage.change_commandeligne",
    "chiffrage.view_commandelignemodification",
    "chiffrage.view_livraison", "chiffrage.view_livraisonligne",
]

_RESPONSABLE_COMMERCIAL = [
    *_COMMERCIAL,
    "chiffrage.delete_devis", "chiffrage.valider_devis", "chiffrage.annuler_commande",
    "chiffrage.add_livraison", "chiffrage.change_livraison",
    "chiffrage.add_livraisonligne", "chiffrage.change_livraisonligne",
    "chiffrage.annuler_livraison", "stock.view_lot",  # choix du lot livré (autocomplétion)
    # Lancer la fabrication d'une commande (bouton « Créer les ordres de fabrication »).
    "chiffrage.add_ordrefabrication", "chiffrage.view_ordrefabrication",
    "chiffrage.view_operationof", "chiffrage.view_composantof",
]

_FACTURATION = [
    "commercial.view_tiers", "commercial.view_adresse",
    "chiffrage.view_commande", "chiffrage.view_commandeligne",
    "chiffrage.view_livraison", "chiffrage.view_livraisonligne",
    "facturation.view_facture", "facturation.add_facture", "facturation.change_facture",
    "facturation.view_factureligne", "facturation.add_factureligne", "facturation.change_factureligne",
    "facturation.delete_factureligne",
    "facturation.view_relancefacture", "facturation.relancer_facture",
    "comptabilite.view_ecriturecomptable", "comptabilite.view_ligneecriture",
]

_MAGASINIER = [
    "technique.view_article",
    "stock.view_emplacement",
    "stock.view_lot", "stock.add_lot", "stock.change_lot",
    "stock.view_mouvementstock", "stock.add_mouvementstock",
    "stock.view_transfert", "stock.add_transfert",
    "stock.view_inventaire", "stock.add_inventaire", "stock.change_inventaire",
    "stock.view_inventaireligne", "stock.add_inventaireligne", "stock.change_inventaireligne",
    "stock.delete_inventaireligne",
    "stock.view_alertestock", "stock.change_alertestock",
    # Bon de préparation d'une commande (PDF).
    "chiffrage.view_commande", "chiffrage.view_commandeligne",
]

_METHODES = [
    *_crud("technique", "Matiere", "Article", "PosteTravail", "TarifPoste", "Nomenclature", "Gamme"),
    *_crud("decoupe", "PieceDecoupe", "ImbricationJob", "ImbricationLigne", "ImbricationPlacement",
           "ProfilImportDecoupe", "RegleProfilImportDecoupe"),
    *_crud("commercial", "TauxTVA", actions=("view",)),
]

_ACHATS = [
    *_crud("achats", "ArticleFournisseur", "CommandeFournisseur", "LigneCommandeFournisseur", "Reception",
           "ReceptionLigne", "FactureFournisseur", "TarifAchatArticle"),
    *_crud("soustraitance", "EnvoiSousTraitance", "RetourSousTraitance"),
    *_crud("commercial", "Tiers", "Adresse", "Contact", "TauxTVA", "Devise", "ConditionPaiement", "Pays",
           actions=("view",)),
    *_crud("technique", "Article", "Matiere", "PosteTravail", actions=("view",)),
    "chiffrage.view_commandeligne", "chiffrage.view_commande",
    "stock.view_alertestock", "stock.view_lot", "stock.view_emplacement",
    "comptabilite.view_postegestion", "comptabilite.view_codeanalytique",
]

_COMPTABILITE = [
    *_crud("comptabilite", "CompteComptable", "CodeAnalytique", "JournalComptable", "EcritureComptable",
           "LigneEcriture", "PosteGestion", "ArticleCompteVente",
           "ArticleCompteAchat", "TiersCompteComptable", "ParametresComptables"),
    *_crud("facturation", "Facture", "FactureLigne", actions=("view",)),
    *_crud("achats", "FactureFournisseur", "CommandeFournisseur", actions=("view",)),
    *_crud("commercial", "Tiers", "Adresse", actions=("view",)),
    "technique.view_article",
]

GROUPES_PAR_DEFAUT = {
    "Commercial": _COMMERCIAL,
    "Responsable commercial": _RESPONSABLE_COMMERCIAL,
    "Direction": [
        *_RESPONSABLE_COMMERCIAL,
        "chiffrage.valider_vente_sous_cout", "chiffrage.voir_marges",
        "stock.view_lot", "stock.view_mouvementstock", "stock.view_inventaire", "stock.view_transfert",
        "stock.view_emplacement",
        "facturation.view_facture", "facturation.view_factureligne",
    ],
    "Atelier": [
        "technique.view_article", "technique.view_postetravail",
        "chiffrage.view_commande", "chiffrage.view_commandeligne",
        "chiffrage.view_ordrefabrication", "chiffrage.change_ordrefabrication", "chiffrage.add_ordrefabrication",
        "chiffrage.view_operationof", "chiffrage.change_operationof", "chiffrage.view_composantof",
        "stock.view_lot", "stock.view_emplacement",
    ],
    "Facturation": _FACTURATION,
    "Responsable facturation": [
        *_FACTURATION,
        "facturation.delete_facture", "facturation.creer_avoir", "facturation.facturer_avant_livraison",
        "comptabilite.add_ecriturecomptable", "comptabilite.add_ligneecriture",
        "comptabilite.view_comptecomptable", "comptabilite.view_journalcomptable",
    ],
    "Méthodes et bureau d'études": _METHODES,
    "Achats": _ACHATS,
    "Comptabilité": _COMPTABILITE,
    "Magasinier": _MAGASINIER,
    "Responsable stock": [
        *_MAGASINIER,
        "stock.annuler_mouvement", "stock.valider_inventaire",
        "stock.add_emplacement", "stock.change_emplacement",
        "stock.delete_inventaire",
    ],
}


def _permissions(codes):
    from django.contrib.auth.models import Permission
    from django.db.models import Q

    condition = Q()
    for code in codes:
        app_label, codename = code.split(".", 1)
        condition |= Q(content_type__app_label=app_label, codename=codename)
    return Permission.objects.filter(condition)


def _creer_permissions_manquantes(using):
    from django.contrib.auth.management import create_permissions

    for app_config in django_apps.get_app_configs():
        create_permissions(app_config, verbosity=0, using=using)


def creer_groupes_par_defaut(sender=None, **kwargs):
    """post_migrate : crée les groupes absents (jamais les groupes existants).

    Branché sur toutes les applications mais exécuté une seule fois par `migrate`
    (au tour de « auth », toujours présente) : `comptes` n'a pas de modèle, Django
    n'émet donc jamais son propre signal post_migrate."""
    if sender is not None and sender.label != "auth":
        return
    from django.contrib.auth.models import Group

    _creer_permissions_manquantes(kwargs.get("using", "default"))
    for nom, codes in GROUPES_PAR_DEFAUT.items():
        groupe, cree = Group.objects.get_or_create(name=nom)
        if cree:
            groupe.permissions.set(_permissions(codes))


def synchroniser_groupes(using="default"):
    """Ajoute aux groupes par défaut les permissions qui leur manquent (jamais de
    retrait). Renvoie {groupe: nombre de permissions ajoutées}."""
    from django.contrib.auth.models import Group

    _creer_permissions_manquantes(using)
    ajouts = {}
    for nom, codes in GROUPES_PAR_DEFAUT.items():
        groupe, _ = Group.objects.get_or_create(name=nom)
        manquantes = _permissions(codes).exclude(pk__in=groupe.permissions.values("pk"))
        ajouts[nom] = manquantes.count()
        groupe.permissions.add(*manquantes)
    return ajouts
