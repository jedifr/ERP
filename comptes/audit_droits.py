"""Audit des droits : qui peut quoi, et qui cumule des droits incompatibles.

Séparation des pouvoirs (SoD) : une même personne ne devrait pas pouvoir à la fois
déclencher et contrôler une opération sensible. Ces règles ne bloquent rien (une petite
structure cumule souvent les rôles, en connaissance de cause) : elles rendent le cumul
visible pour qu'il soit décidé, pas subi."""

import datetime

from django.contrib.auth import get_user_model
from django.utils import timezone

# (libellé du risque, permission A, permission B) : détenir les deux est signalé.
CONFLITS = [
    ("Saisir une facture et la comptabiliser", "facturation.add_facture", "comptabilite.add_ecriturecomptable"),
    ("Saisir un mouvement de stock et valider l'inventaire", "stock.add_mouvementstock", "stock.valider_inventaire"),
    ("Réceptionner et enregistrer la facture fournisseur", "achats.add_reception", "achats.add_facturefournisseur"),
    ("Livrer et facturer", "chiffrage.add_livraison", "facturation.add_facture"),
    ("Créer un avoir et gérer les règlements comptables", "facturation.creer_avoir", "comptabilite.add_ecriturecomptable"),
    ("Passer une commande fournisseur et la réceptionner", "achats.add_commandefournisseur", "achats.add_receptionligne"),
]

SEUIL_INACTIVITE_JOURS = 90
SEUIL_SUPERUTILISATEURS = 2


def _conflits_de(utilisateur):
    if utilisateur.is_superuser:
        return []  # tous les droits : signalé à part, pas détaillé règle par règle
    permissions = utilisateur.get_all_permissions()
    return [libelle for libelle, a, b in CONFLITS if a in permissions and b in permissions]


def rapport():
    """Liste des comptes du personnel (actifs ou non), avec constats."""
    maintenant = timezone.now()
    lignes = []
    for u in get_user_model().objects.filter(is_staff=True).prefetch_related("groups").order_by("username"):
        groupes = [g.name for g in u.groups.all()]
        constats = []
        if u.is_superuser:
            constats.append("Superutilisateur : accès total")
        elif u.is_active and not groupes and not u.user_permissions.exists():
            constats.append("Aucun groupe : ce compte n'a accès à rien")
        if u.is_active and u.last_login is None:
            constats.append("Jamais connecté")
        elif u.is_active and u.last_login and (maintenant - u.last_login) > datetime.timedelta(days=SEUIL_INACTIVITE_JOURS):
            constats.append(f"Inactif depuis plus de {SEUIL_INACTIVITE_JOURS} jours : à désactiver ?")
        if not u.is_active:
            constats.append("Désactivé")
        conflits = _conflits_de(u) if u.is_active else []
        lignes.append(
            {
                "identifiant": u.get_username(),
                "nom": u.get_full_name(),
                "actif": u.is_active,
                "superutilisateur": u.is_superuser,
                "groupes": groupes,
                "derniere_connexion": u.last_login,
                "constats": constats,
                "conflits": conflits,
            }
        )
    return lignes


def synthese(lignes):
    actifs = [l for l in lignes if l["actif"]]
    superutilisateurs = [l for l in actifs if l["superutilisateur"]]
    return {
        "comptes_actifs": len(actifs),
        "superutilisateurs": len(superutilisateurs),
        "trop_de_superutilisateurs": len(superutilisateurs) > SEUIL_SUPERUTILISATEURS,
        "sans_groupe": sum(1 for l in actifs if any(c.startswith("Aucun groupe") for c in l["constats"])),
        "avec_conflits": sum(1 for l in actifs if l["conflits"]),
    }
