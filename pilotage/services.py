"""Pilotage — ne nécessite aucune nouvelle table (cahier des charges, Phase 4).

Recalcule à la demande, à partir des données déjà stockées par les phases
précédentes : marge réelle vs prévue d'un ordre de fabrication (à partir des
données réelles remontées sur `OperationOF`), et taux de charge d'un poste
sur une période.
"""

import datetime

from django.db.models import Sum

from chiffrage.models import OperationOF
from chiffrage.moteur import tarif_poste_valide
from technique.models import PosteTravail


class PilotageError(Exception):
    """Donnée de référence manquante empêchant le calcul."""


def _devis_ligne(of):
    ligne = of.commande.devis.lignes.filter(article=of.article).first()
    if ligne is None:
        raise PilotageError(
            f"Aucune ligne de devis pour l'article « {of.article} » sur le devis "
            f"« {of.commande.devis} » — le chiffrage n'a pas été calculé pour cet OF."
        )
    return ligne


def cout_reel_operation(operation_of):
    """Coût réel d'une opération d'OF, ou None si la donnée n'est pas encore remontée."""
    poste = operation_of.poste
    if poste.mode_calcul == PosteTravail.ModeCalcul.HORAIRE:
        if operation_of.temps_reel is None:
            return None
        tarif = tarif_poste_valide(poste, operation_of.ordre_fabrication.date_lancement)
        # temps_reel (OperationOF) est en MINUTES, comme temps_prevu — voir
        # cout_etape_gamme() dans chiffrage/moteur.py pour la même conversion.
        return (operation_of.temps_reel / 60) * tarif.cout_horaire

    # Forfaitaire (ex. sous-traitance) : prix fixé à l'avance, ne varie pas avec les
    # données réelles remontées — on reprend le coût déjà calculé au chiffrage.
    ligne = _devis_ligne(operation_of.ordre_fabrication)
    devis_operation = ligne.operations.filter(ordre=operation_of.ordre).first()
    return devis_operation.cout_calcule if devis_operation else None


def marge_reelle_ordre_fabrication(of):
    """Compare la marge prévue au devis (figée) à la marge réelle recalculée
    à partir des données remontées sur les opérations de l'OF."""
    ligne = _devis_ligne(of)

    cout_matiere = ligne.cout_matiere_calcule or 0
    prix_vente_matiere = ligne.prix_vente_matiere or 0

    operations_prevues = list(ligne.operations.all())
    cout_operations_prevu = sum(op.cout_calcule or 0 for op in operations_prevues)
    prix_vente_operations_prevu = sum(op.prix_vente or 0 for op in operations_prevues)

    operations_of = list(of.operations.all())
    couts_reels = [cout_reel_operation(op) for op in operations_of]
    donnees_completes = all(c is not None for c in couts_reels)
    cout_operations_reel = sum(c or 0 for c in couts_reels)

    cout_prevu_total = cout_matiere + cout_operations_prevu
    prix_vente_prevu_total = prix_vente_matiere + prix_vente_operations_prevu
    cout_reel_total = cout_matiere + cout_operations_reel

    marge_prevue = prix_vente_prevu_total - cout_prevu_total
    marge_reelle = prix_vente_prevu_total - cout_reel_total  # prix de vente figé au devis

    return {
        "ordre_fabrication": of.numero,
        "donnees_completes": donnees_completes,
        "prix_vente_prevu_total": prix_vente_prevu_total,
        "cout_prevu_total": cout_prevu_total,
        "marge_prevue": marge_prevue,
        "cout_reel_total": cout_reel_total,
        "marge_reelle": marge_reelle,
        "ecart_marge": marge_reelle - marge_prevue,
    }


def _jours_ouvres(date_debut, date_fin):
    jours = 0
    jour = date_debut
    un_jour = datetime.timedelta(days=1)
    while jour <= date_fin:
        if jour.weekday() < 5:  # lundi (0) à vendredi (4)
            jours += 1
        jour += un_jour
    return jours


def taux_charge_poste(poste, date_debut, date_fin, heures_par_jour_par_machine=7):
    """Taux de charge = temps réel cumulé / capacité disponible sur la période.

    La capacité disponible n'est pas définie précisément par le cahier des
    charges (jours ouvrés, heures par machine et par jour) : approximation
    du lundi au vendredi, `heures_par_jour_par_machine` heures par machine
    et par jour ouvré (7h par défaut, ajustable par appel).
    """
    # Sum("temps_reel") est en MINUTES (comme OperationOF.temps_reel/temps_prevu) ;
    # converti en heures ici pour rester dans la même unité que
    # capacite_disponible (nombre_machines * jours * heures/jour), sans quoi
    # le taux de charge calculé plus bas serait gonflé x60.
    temps_total_minutes = (
        OperationOF.objects.filter(
            poste=poste,
            ordre_fabrication__date_lancement__gte=date_debut,
            ordre_fabrication__date_lancement__lte=date_fin,
        ).aggregate(total=Sum("temps_reel"))["total"]
        or 0
    )
    temps_total_heures = temps_total_minutes / 60

    jours_ouvres = _jours_ouvres(date_debut, date_fin)
    capacite_disponible = poste.nombre_machines * jours_ouvres * heures_par_jour_par_machine

    return {
        "poste": poste.nom,
        "date_debut": date_debut,
        "date_fin": date_fin,
        "temps_reel_cumule": temps_total_heures,
        "capacite_disponible": capacite_disponible,
        "taux_charge": (temps_total_heures / capacite_disponible) if capacite_disponible else None,
    }
