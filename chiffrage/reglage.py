"""Réglage machine par tôle : le temps de mise en place d'une tôle sur la laser ou le jet d'eau s'applique UNE fois par tôle posée,
quel que soit le nombre de pièces qu'elle porte, et autant de fois qu'il y a de tôles (groupes de matière, d'épaisseur, de procédé
ou de machine différents = autres tôles).

Le nombre de tôles vient de l'imbrication retenue de chaque groupe du devis ; tant qu'aucune n'est retenue, de la meilleure imbrication
calculée (avertissement). Le coût du réglage d'un groupe est réparti entre ses pièces au prorata de leur temps de coupe et reste
inclus dans le prix des opérations de la ligne de devis de chaque pièce (colonne informative « dont réglage machine »)."""

import datetime
from dataclasses import dataclass, field
from decimal import Decimal

from decoupe.models import PieceDecoupe
from decoupe.services import imbrication_devis as imb
from decoupe.services.devis_pieces import pieces_du_devis
from decoupe.services.matiere import ErreurMatiere, bord_tole_piece_mm
from decoupe.services.temps import ErreurTemps, estimer_temps_decoupe

ZERO = Decimal("0")


@dataclass
class PartReglage:
    cout: Decimal = ZERO
    minutes: float = 0.0
    note: str = ""
    estime: bool = False
    avertissements: list = field(default_factory=list)


def minutes_mise_en_place(parametre):
    """Temps de mise en place d'une tôle (min) : exception du paramètre de coupe, sinon valeur du poste, sinon 0."""
    if parametre.temps_mise_en_place_min is not None:
        return float(parametre.temps_mise_en_place_min)
    if parametre.poste_id and parametre.poste.temps_mise_en_place_min is not None:
        return float(parametre.poste.temps_mise_en_place_min)
    return 0.0


def feuilles_du_groupe(groupe, quantites):
    """(nombre de tôles, estimé ?, avertissement) du groupe : imbrication retenue, sinon meilleure imbrication calculée."""
    pieces = groupe.pieces
    premiere = pieces[0]
    try:
        if premiere.tole_id and premiere.format_tole_id:
            resultat = imb.imbriquer_groupe(
                groupe, premiere.format_tole, premiere.marge_bord_mm, float(premiere.taux_chute_recuperable), premiere.tole, quantites=quantites,
                forme=premiere.imbrication_forme, sens=premiere.sens_imbrication, coin=premiere.coin_depart,
            )
            return resultat.nb_feuilles, False, ""
        formats = imb.formats_compatibles(groupe.procede, imb.formats_actifs())
        marge = max((bord_tole_piece_mm(p) for p in pieces), default=5.0)
        # On ne passe pas les quantités à comparer_formats (même signature que le panneau) : on garde le meilleur format puis on recalcule avec elles.
        _, meilleur = imb.comparer_formats(groupe, formats, marge, 0.0, None)
        if meilleur is None:
            return 1, True, "aucun format de tôle utilisable : 1 tôle comptée"
        resultat = imb.imbriquer_groupe(groupe, meilleur, marge, 0.0, None, quantites=quantites)
        return resultat.nb_feuilles, True, "imbrication non retenue : nombre de tôles estimé avec la meilleure imbrication calculée"
    except ErreurMatiere as exc:
        return 1, True, f"imbrication impossible ({exc}) : 1 tôle comptée"


def resume_groupe(groupe, nb_feuilles, quantites=None):
    """Résumé affiché dans l'imbrication d'un groupe : {"feuilles", "minutes_par_tole" (None si plusieurs valeurs), "minutes_total"}."""
    quantites = quantites or {}
    details = []
    for piece in groupe.pieces:
        try:
            estimation = estimer_temps_decoupe(piece)
        except ErreurTemps:
            continue
        details.append((minutes_mise_en_place(estimation.parametre), estimation.total_min * int(quantites.get(piece.pk, piece.quantite))))
    total_coupe = sum(d[1] for d in details) or 1.0
    total = sum(nb_feuilles * mep * (minutes / total_coupe if len(details) > 1 else 1.0) for mep, minutes in details)
    distinctes = {mep for mep, _ in details}
    return {"feuilles": nb_feuilles, "minutes_par_tole": distinctes.pop() if len(distinctes) == 1 else None, "minutes_total": total}


def _quantites_devis(devis, surcharge=None):
    """{id d'article: quantité} des lignes du devis, avec la surcharge éventuelle {id d'article: quantité} (ligne en cours de saisie)."""
    quantites = {}
    for ligne in devis.lignes.all():
        quantites[ligne.article_id] = quantites.get(ligne.article_id, 0) + ligne.quantite
    quantites.update(surcharge or {})
    return quantites


def parts_par_piece(devis, surcharge=None):
    """{id de pièce: PartReglage (coût hors marge)} pour toutes les pièces réalisables du devis. Mémorisé sur l'objet devis."""
    from .moteur import cout_etape_gamme
    from technique.models import Gamme, PosteTravail

    quantites_article = _quantites_devis(devis, surcharge)
    cle = tuple(sorted((str(a), float(q)) for a, q in quantites_article.items()))
    cache = devis.__dict__.setdefault("_cache_reglage", {})
    if cle in cache:
        return cache[cle]
    resultat = {}
    groupes, _ = imb.grouper(pieces_du_devis(devis))
    jour = devis.date_creation
    for groupe in groupes:
        if not groupe.pieces:
            continue
        quantites = {p.pk: int(quantites_article.get(p.article_id, p.quantite)) for p in groupe.pieces}
        nb, estime, avertissement = feuilles_du_groupe(groupe, quantites)
        details = []
        for piece in groupe.pieces:
            try:
                estimation = estimer_temps_decoupe(piece)
            except ErreurTemps:
                continue
            details.append((piece, estimation.parametre, estimation.total_min * quantites[piece.pk]))
        total_coupe = sum(d[2] for d in details) or 1.0
        for piece, parametre, minutes_coupe in details:
            mep = minutes_mise_en_place(parametre)
            part = minutes_coupe / total_coupe if len(details) > 1 else 1.0
            minutes = nb * mep * part
            cout = ZERO
            if minutes and parametre.poste_id and parametre.poste.mode_calcul == PosteTravail.ModeCalcul.HORAIRE:
                cout = cout_etape_gamme(Gamme(poste=parametre.poste, temps_fixe=minutes, temps_variable=0), 1, jour)
            note = ""
            if mep:
                note = f"{nb} tôle{'s' if nb > 1 else ''} × {mep:g} min" + (f", part {round(part * 100)} %" if len(details) > 1 else "")
                if estime:
                    note += " (estimé)"
            avert = [avertissement] if avertissement else []
            if mep and not parametre.poste_id:
                avert.append("poste de travail absent du paramètre de coupe : réglage non chiffré")
            resultat[piece.pk] = PartReglage(cout=cout, minutes=minutes, note=note, estime=estime, avertissements=avert)
    cache[cle] = resultat
    return resultat


def part_ligne(devis, article, quantite=None):
    """Réglage machine (PartReglage) de la ligne d'un article du devis : somme des parts de ses pièces à découper."""
    surcharge = {article.pk: quantite} if quantite is not None else None
    parts = parts_par_piece(devis, surcharge)
    pieces = PieceDecoupe.objects.filter(devis=devis, article=article).values_list("pk", flat=True)
    total = PartReglage()
    for pk in pieces:
        p = parts.get(pk)
        if p is None:
            continue
        total.cout += p.cout
        total.minutes += p.minutes
        total.estime = total.estime or p.estime
        total.avertissements += p.avertissements
        total.note = (total.note + " ; " if total.note else "") + p.note if p.note else total.note
    return total
