"""Import du plan comptable général (PCG) français.

Le jeu de données (comptabilite/data/pcg_<millésime>.json) est un instantané
figé du PCG publié annuellement par l'ANC (Autorité des Normes Comptables),
récupéré depuis github.com/arrhes/PCG (domaine public, licence CC0). Il est
embarqué dans l'application plutôt que téléchargé à la volée en production :
pas de dépendance à un service tiers disponible au moment du clic, et un
contenu vérifié une fois pour toutes plutôt que réinterprété à chaque appel.
Rafraîchir vers un millésime plus récent = remplacer ce fichier et relancer
l'import (idempotent, voir importer_pcg).
"""

import json
from pathlib import Path

from .models import CompteComptable

PCG_MILLESIME = 2026
_CHEMIN_DONNEES = Path(__file__).resolve().parent / "data" / f"pcg_{PCG_MILLESIME}.json"


def importer_pcg(chemin=None):
    """Importe (ou met à jour) le plan comptable depuis le jeu de données
    embarqué. Idempotent — rejouable sans risque (ex. pour un millésime
    suivant) : les comptes existants sont mis à jour, pas dupliqués.
    Renvoie (comptes_crees, comptes_mis_a_jour)."""
    chemin = chemin or _CHEMIN_DONNEES
    comptes = json.loads(Path(chemin).read_text(encoding="utf-8"))["flat"]

    crees = 0
    for compte in comptes:
        _, cree = CompteComptable.objects.update_or_create(
            code=str(compte["number"]),
            defaults={"libelle": compte["label"], "systeme": compte["system"]},
        )
        if cree:
            crees += 1

    # Deuxième passe : un compte_parent doit déjà exister en base pour
    # pouvoir être référencé — impossible de le faire en une seule passe
    # puisque le fichier ne garantit pas qu'un parent précède ses enfants.
    for compte in comptes:
        if compte["parent"] is not None:
            CompteComptable.objects.filter(code=str(compte["number"])).update(
                compte_parent_id=str(compte["parent"])
            )

    return crees, len(comptes) - crees
