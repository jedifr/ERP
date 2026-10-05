"""Création de paramètres de coupe sans relevé : calcul des vitesses depuis l'usinabilité et duplication d'un
paramètre relevé vers d'autres épaisseurs (voir vitesses.py pour les formules)."""

from django.db import transaction

from decoupe.models import ParametreCoupe, VitesseCoupe

from .vitesses import CHAMPS_PROPORTIONNELS, vitesses_depuis_usinabilite


class ErreurParametre(Exception):
    pass


def usinabilite_de(parametre):
    return parametre.usinabilite or parametre.matiere.usinabilite


def calculer_vitesses(parametre):
    """Remplace les vitesses de `parametre` par celles calculées depuis l'usinabilité. Refuse d'écraser des vitesses
    relevées sur la machine."""
    if parametre.origine == "machine" and parametre.vitesses.exists():
        raise ErreurParametre(f"« {parametre} » : vitesses relevées sur la machine, elles ne sont pas remplacées par un calcul.")
    usinabilite = usinabilite_de(parametre)
    if not usinabilite:
        raise ErreurParametre(f"« {parametre} » : renseignez l'usinabilité (de la matière ou du paramètre).")
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
    if not usinabilite_de(modele):
        raise ErreurParametre("Renseignez d'abord l'usinabilité de la matière (ou du paramètre modèle).")
    valeurs = {
        f.name: getattr(modele, f.name) for f in ParametreCoupe._meta.concrete_fields
        if f.name not in ("id", "epaisseur_mm", "origine")
    }
    crees, existants = [], []
    for epaisseur in sorted({float(e) for e in epaisseurs if float(e) > 0}):
        if ParametreCoupe.objects.filter(procede=modele.procede, matiere=modele.matiere, epaisseur_mm=epaisseur).exists():
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
