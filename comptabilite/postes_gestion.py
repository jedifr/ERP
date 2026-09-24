"""Import des postes de gestion (achat + vente).

Jeu de données propre à l'entreprise (pas un référentiel public comme le
PCG) : comptabilite/data/postes_gestion.json, construit à partir des
exports "poste de gestion achats"/"poste de gestion ventes" d'un logiciel
de gestion existant fournis par l'utilisateur (mêmes 145 codes dans les
deux fichiers, fusionnés). Les comptes qu'il référence descendent à un
niveau de détail (6 chiffres) plus fin que le PCG officiel embarqué (voir
comptabilite.pcg) : importer_postes_gestion() les crée automatiquement
s'ils n'existent pas encore, en système développé, avec le libellé du
premier poste qui les référence — à affiner ensuite dans l'admin si
besoin, ce n'est qu'un point de départ raisonnable.
"""

import json
from pathlib import Path

from .models import CompteComptable, PosteGestion

_CHEMIN_DONNEES = Path(__file__).resolve().parent / "data" / "postes_gestion.json"

_CHAMPS_MODELE = {
    "achat_france": "compte_achat_france",
    "achat_france_exonere": "compte_achat_france_exonere",
    "achat_intra_ue": "compte_achat_intra_ue",
    "achat_hors_ue": "compte_achat_hors_ue",
    "vente_france": "compte_vente_france",
    "vente_france_exonere": "compte_vente_france_exonere",
    "vente_intra_ue": "compte_vente_intra_ue",
    "vente_hors_ue": "compte_vente_hors_ue",
    "vente_tva_majoree": "compte_vente_tva_majoree",
}


def importer_postes_gestion(chemin=None):
    """Importe (ou met à jour) les postes de gestion depuis le jeu de
    données embarqué. Idempotent — rejouable sans risque. Écrit en bulk
    (comme comptabilite.pcg.importer_pcg) plutôt qu'un update_or_create
    par poste, pour rester rapide même sur du matériel modeste. Renvoie
    (postes_crees, postes_mis_a_jour, comptes_crees)."""
    chemin = chemin or _CHEMIN_DONNEES
    postes_json = json.loads(Path(chemin).read_text(encoding="utf-8"))["postes"]

    codes_references = {}
    for poste in postes_json:
        for champ in _CHAMPS_MODELE:
            code = poste[champ]
            if code and code not in codes_references:
                codes_references[code] = poste["libelle"]

    existants_comptes = set(
        CompteComptable.objects.filter(code__in=list(codes_references)).values_list("code", flat=True)
    )
    a_creer_comptes = [
        CompteComptable(code=code, libelle=libelle, systeme=CompteComptable.Systeme.DEVELOPPE, classe=int(code[0]))
        for code, libelle in codes_references.items()
        if code not in existants_comptes
    ]
    if a_creer_comptes:
        CompteComptable.objects.bulk_create(a_creer_comptes, batch_size=200)
    comptes_crees = len(a_creer_comptes)

    comptes_par_code = {c.code: c for c in CompteComptable.objects.filter(code__in=list(codes_references))}

    existants_postes = {
        p.code: p for p in PosteGestion.objects.filter(code__in=[p["code"] for p in postes_json])
    }
    a_creer_postes = []
    a_maj_postes = []
    for poste in postes_json:
        valeurs = {"libelle": poste["libelle"], "groupe": poste["groupe"]}
        for champ_json, champ_modele in _CHAMPS_MODELE.items():
            code = poste[champ_json]
            valeurs[champ_modele] = comptes_par_code.get(code) if code else None

        existant = existants_postes.get(poste["code"])
        if existant is None:
            a_creer_postes.append(PosteGestion(code=poste["code"], **valeurs))
        else:
            for champ, valeur in valeurs.items():
                setattr(existant, champ, valeur)
            a_maj_postes.append(existant)

    if a_creer_postes:
        PosteGestion.objects.bulk_create(a_creer_postes, batch_size=100)
    if a_maj_postes:
        PosteGestion.objects.bulk_update(
            a_maj_postes, ["libelle", "groupe"] + list(_CHAMPS_MODELE.values()), batch_size=100
        )

    return len(a_creer_postes), len(a_maj_postes), comptes_crees
