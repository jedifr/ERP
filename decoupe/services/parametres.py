"""Création de paramètres de coupe sans relevé : calcul des vitesses depuis l'usinabilité et duplication d'un
paramètre relevé vers d'autres épaisseurs (voir vitesses.py pour les formules)."""

from django.db import transaction
from django.db.models import Q

from decoupe.models import ParametreCoupe, VitesseCoupe

from .vitesses import CHAMPS_PROPORTIONNELS, vitesses_depuis_usinabilite


class ErreurParametre(Exception):
    pass


def usinabilite_de(parametre):
    """Usinabilité du paramètre, à défaut celle de sa nuance, à défaut celle de sa famille."""
    if parametre.usinabilite:
        return parametre.usinabilite
    if parametre.matiere_id:
        return parametre.matiere.usinabilite_effective
    return parametre.famille.usinabilite if parametre.famille_id else None


def parametres_applicables(matiere, procede=ParametreCoupe.Procede.JET_EAU):
    """Paramètres de coupe utilisables pour une matière : ceux de la nuance elle-même (exceptions) et ceux de sa famille."""
    q = Q(matiere_id=matiere.pk)
    if matiere.famille_id:
        q |= Q(famille_id=matiere.famille_id)
    return list(ParametreCoupe.objects.filter(q, procede=procede).select_related("matiere", "famille"))


def _rang_gaz(parametre, matiere):
    """Ordre de préférence des gaz : celui de la famille d'abord, puis oxygène, azote, air."""
    ordre = [g for g in (matiere.famille.gaz_laser_prefere if matiere.famille_id else "", "O2", "N2", "Air") if g]
    return ordre.index(parametre.gaz) if parametre.gaz in ordre else len(ordre)


def meilleur_parametre(matiere, epaisseur, procede=ParametreCoupe.Procede.JET_EAU, gaz=""):
    """Jet d'eau : paramètre de l'épaisseur la plus proche. Laser : épaisseur EXACTE uniquement (une épaisseur absente de
    la base n'est pas réalisable), au gaz demandé ou à défaut au gaz usuel de la famille. À égalité, la nuance passe avant
    sa famille. Retourne None si rien ne convient."""
    candidats = parametres_applicables(matiere, procede)
    if gaz:
        candidats = [p for p in candidats if p.gaz == gaz]
    if procede == ParametreCoupe.Procede.LASER:
        candidats = [p for p in candidats if abs(p.epaisseur_mm - epaisseur) < 1e-6]
        return min(candidats, key=lambda p: (p.matiere_id is None, _rang_gaz(p, matiere)), default=None)
    if not candidats:
        return None
    return min(candidats, key=lambda p: (abs(p.epaisseur_mm - epaisseur), p.matiere_id is None))


def epaisseurs_possibles(matiere, procede, gaz=""):
    """Épaisseurs (mm) disponibles pour la matière, au gaz demandé le cas échéant."""
    return sorted({p.epaisseur_mm for p in parametres_applicables(matiere, procede) if not gaz or p.gaz == gaz})


def reglage(procede):
    from decoupe.models import ReglageProcede

    return ReglageProcede.pour(procede)


def calculer_vitesses(parametre):
    """Remplace les vitesses de `parametre` par celles calculées depuis l'usinabilité. Refuse d'écraser des vitesses
    relevées sur la machine."""
    if parametre.procede == ParametreCoupe.Procede.LASER:
        raise ErreurParametre(f"« {parametre} » : au laser, les vitesses viennent du tableau du constructeur (pas de calcul d'usinabilité).")
    if parametre.origine == "machine" and parametre.vitesses.exists():
        raise ErreurParametre(f"« {parametre} » : vitesses relevées sur la machine, elles ne sont pas remplacées par un calcul.")
    usinabilite = usinabilite_de(parametre)
    if not usinabilite:
        raise ErreurParametre(f"« {parametre} » : renseignez l'usinabilité (de la famille, de la nuance ou du paramètre).")
    with transaction.atomic():
        parametre.vitesses.all().delete()
        VitesseCoupe.objects.bulk_create(
            VitesseCoupe(parametre=parametre, **v) for v in vitesses_depuis_usinabilite(usinabilite, parametre.epaisseur_mm)
        )
        parametre.origine = "calcule"
        parametre.save(update_fields=["origine"])
    return parametre


def dupliquer_vers_epaisseurs(modele, epaisseurs):
    """Crée un paramètre par épaisseur (sauf celles qui existent déjà) à partir de `modele` : mêmes réglages, perçage,
    percement et chevauchement proportionnels à l'épaisseur, vitesses calculées. Retourne (créés, déjà existants)."""
    if modele.procede == ParametreCoupe.Procede.LASER:
        raise ErreurParametre("Au laser, une épaisseur absente du tableau du constructeur n'est pas réalisable : pas de duplication.")
    if not usinabilite_de(modele):
        raise ErreurParametre("Renseignez d'abord l'usinabilité de la famille de matière (ou du paramètre modèle).")
    valeurs = {
        f.name: getattr(modele, f.name) for f in ParametreCoupe._meta.concrete_fields
        if f.name not in ("id", "epaisseur_mm", "origine")
    }
    crees, existants = [], []
    for epaisseur in sorted({float(e) for e in epaisseurs if float(e) > 0}):
        if ParametreCoupe.objects.filter(
            procede=modele.procede, famille=modele.famille, matiere=modele.matiere, epaisseur_mm=epaisseur
        ).exists():
            existants.append(epaisseur)
            continue
        propres = dict(valeurs)
        for champ in CHAMPS_PROPORTIONNELS:
            propres[champ] = round(propres[champ] * epaisseur / modele.epaisseur_mm, 2)
        parametre = ParametreCoupe(epaisseur_mm=epaisseur, origine="calcule", **propres)
        parametre.save()
        calculer_vitesses(parametre)
        crees.append(parametre)
    return crees, existants


def format_compatible(format_tole, procede):
    """(compatible, message) : le format de tôle tient-il dans la machine du procédé (capacité des réglages de coupe) ?"""
    r = reglage(procede)
    if r.accepte(format_tole):
        return True, ""
    nom = "du laser" if procede == "laser" else "du jet d'eau"
    return False, f"Dépasse la capacité {nom} ({r.libelle_capacite})."
