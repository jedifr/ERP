"""Contrôles à la validation d'un devis (passage de « brouillon » à « validé »).

Un devis validé est le prix engagé auprès du client et déclenche la commande :
mieux vaut refuser ici un devis vide, non chiffré ou vendu sous son coût que
le découvrir sur la commande."""

from .moteur import ChiffrageError, calculer_devis


def verifier_validation_devis(devis, peut_vendre_sous_cout=False):
    """Recalcule le chiffrage puis renvoie la liste des raisons de refus
    (vide si le devis peut être validé)."""
    if not devis.lignes.exists():
        return ["Le devis ne contient aucune ligne."]
    try:
        calculer_devis(devis)
    except ChiffrageError as exc:
        return [f"Chiffrage impossible : {exc}"]

    raisons = []
    for ligne in devis.lignes.select_related("article").prefetch_related("operations").all():
        if ligne.prix_vente_total is None:
            raisons.append(f"« {ligne.article} » : ligne non chiffrée.")
        elif ligne.vente_sous_le_cout and not peut_vendre_sous_cout:
            raisons.append(
                f"« {ligne.article} » : vendu sous le coût ({ligne.prix_vente_total:.2f} € HT "
                f"pour un coût de {ligne.cout_total:.2f} €) — la permission « valider un devis "
                "vendu sous le coût » est requise."
            )
    return raisons
