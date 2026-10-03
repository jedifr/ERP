"""Traçabilité d'un lot de matière : d'où il vient (réceptions fournisseur) et où il est parti
(livraisons clients), reconstituée depuis les mouvements de stock et leurs références."""

from .models import MouvementStock


def tracabilite_lot(lot):
    from achats.models import Reception
    from chiffrage.models import Livraison, LivraisonLigne

    receptions, livraisons, autres = [], [], []
    for mouvement in lot.mouvements.select_related("utilisateur").order_by("date_mouvement", "id"):
        reference = mouvement.reference_origine
        if reference.startswith("RECEPTION-"):
            reception = Reception.objects.filter(pk=reference.removeprefix("RECEPTION-")).select_related(
                "commande_fournisseur__fournisseur"
            ).first()
            receptions.append({"mouvement": mouvement, "reception": reception})
        elif reference.startswith("LIVRAISON-") and mouvement.type_mouvement == MouvementStock.TypeMouvement.SORTIE:
            livraison = Livraison.objects.filter(pk=reference.removeprefix("LIVRAISON-")).select_related("commande__client").first()
            livraisons.append({"mouvement": mouvement, "livraison": livraison})
        else:
            autres.append(mouvement)
    return {"receptions": receptions, "livraisons": livraisons, "autres": autres}
