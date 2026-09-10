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

from django.db import transaction

from .models import CompteComptable

PCG_MILLESIME = 2026
_CHEMIN_DONNEES = Path(__file__).resolve().parent / "data" / f"pcg_{PCG_MILLESIME}.json"


def importer_pcg(chemin=None):
    """Importe (ou met à jour) le plan comptable depuis le jeu de données
    embarqué. Idempotent — rejouable sans risque (ex. pour un millésime
    suivant) : les comptes existants sont mis à jour, pas dupliqués.
    Renvoie (comptes_crees, comptes_mis_a_jour).

    Écrit en bulk (quelques requêtes au total) plutôt qu'un
    update_or_create par compte (~1700 requêtes pour 861 comptes) : sur du
    matériel modeste (NAS...), cette volée de petites transactions pouvait
    dépasser le délai d'un worker Gunicorn et le faire tuer en plein
    import, laissant le plan comptable à moitié chargé."""
    chemin = chemin or _CHEMIN_DONNEES
    comptes_json = json.loads(Path(chemin).read_text(encoding="utf-8"))["flat"]
    codes = [str(c["number"]) for c in comptes_json]

    with transaction.atomic():
        existants = {c.code: c for c in CompteComptable.objects.filter(code__in=codes)}

        a_creer = []
        a_mettre_a_jour = []
        for compte in comptes_json:
            code = str(compte["number"])
            existant = existants.get(code)
            if existant is None:
                a_creer.append(
                    CompteComptable(
                        code=code, libelle=compte["label"], systeme=compte["system"], classe=int(code[0])
                    )
                )
            elif existant.libelle != compte["label"] or existant.systeme != compte["system"]:
                existant.libelle = compte["label"]
                existant.systeme = compte["system"]
                a_mettre_a_jour.append(existant)

        if a_creer:
            CompteComptable.objects.bulk_create(a_creer, batch_size=200)
        if a_mettre_a_jour:
            CompteComptable.objects.bulk_update(a_mettre_a_jour, ["libelle", "systeme"], batch_size=200)

        # Deuxième passe : un compte_parent doit déjà exister en base pour
        # pouvoir être référencé — impossible de le faire en une seule passe
        # puisque le fichier ne garantit pas qu'un parent précède ses
        # enfants. bulk_update ici aussi, plutôt qu'un .filter().update()
        # par compte.
        tous_les_comptes = {c.code: c for c in CompteComptable.objects.filter(code__in=codes)}
        a_lier = []
        for compte in comptes_json:
            if compte["parent"] is None:
                continue
            code = str(compte["number"])
            parent_code = str(compte["parent"])
            obj = tous_les_comptes[code]
            if obj.compte_parent_id != parent_code:
                obj.compte_parent_id = parent_code
                a_lier.append(obj)
        if a_lier:
            CompteComptable.objects.bulk_update(a_lier, ["compte_parent"], batch_size=200)

    crees = len(a_creer)
    return crees, len(comptes_json) - crees
