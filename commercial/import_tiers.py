"""Création en masse de tiers depuis un tableau (CSV collé ou fichier) : une ligne par tiers, colonnes
`code; raison_sociale; type; siret; numero_tva; regime_fiscal; conditions_paiement; devise`. Seuls `code` et `raison_sociale` sont obligatoires ;
un code déjà pris est ignoré (jamais écrasé). Le lot est tracé et annulable (comptes/lots.py)."""

import csv
import io

from django.db import transaction

from .models import ConditionPaiement, Devise, Tiers

ALIAS = {
    "code": "code", "raison_sociale": "raison_sociale", "raison sociale": "raison_sociale", "nom": "raison_sociale", "type": "type_tiers", "type_tiers": "type_tiers",
    "siret": "siret", "tva": "numero_tva", "numero_tva": "numero_tva", "n° tva": "numero_tva", "regime": "regime_fiscal", "regime_fiscal": "regime_fiscal",
    "regime fiscal": "regime_fiscal", "conditions": "conditions_paiement", "conditions_paiement": "conditions_paiement", "devise": "devise",
}


def _cle(texte):
    return (texte or "").strip().lower().replace("é", "e").replace("è", "e")


def _choix(valeur, choix):
    v = _cle(valeur)
    for code, libelle in choix:
        if v in (_cle(code), _cle(str(libelle))):
            return code
    return None


def lire(texte):
    """Lignes du tableau : liste de dictionnaires à clés normalisées (délimiteur ; , ou tabulation détecté sur la première ligne)."""
    texte = (texte or "").strip("﻿\n\r ")
    if not texte:
        return []
    premiere = texte.splitlines()[0]
    delimiteur = max(";\t,", key=premiere.count)
    lecteur = csv.DictReader(io.StringIO(texte), delimiter=delimiteur)
    lignes = []
    for brute in lecteur:
        ligne = {}
        for k, v in brute.items():
            cle = ALIAS.get(_cle(k)) or ALIAS.get(_cle(k).replace("_", " "))
            if k and cle:
                ligne[cle] = (v or "").strip()
        lignes.append(ligne)
    return lignes


@transaction.atomic
def importer(texte, utilisateur):
    """Crée les tiers du tableau. Retourne {crees: [code], ignores: [(ligne, motif)], lot}."""
    from comptes import lots

    rapport = {"crees": [], "ignores": [], "lot": None}
    crees = []
    for numero, ligne in enumerate(lire(texte), start=2):  # 1 = en-têtes
        code, nom = ligne.get("code", ""), ligne.get("raison_sociale", "")
        if not code or not nom:
            rapport["ignores"].append((f"ligne {numero}", "code et raison sociale obligatoires"))
            continue
        if Tiers.objects.filter(pk=code).exists():
            rapport["ignores"].append((code, "code déjà pris"))
            continue
        type_tiers = _choix(ligne.get("type_tiers", ""), Tiers.TypeTiers.choices) or Tiers.TypeTiers.CLIENT
        regime = _choix(ligne.get("regime_fiscal", ""), Tiers.RegimeFiscal.choices) or Tiers.RegimeFiscal.FRANCE
        conditions = ConditionPaiement.objects.filter(libelle__iexact=ligne["conditions_paiement"]).first() if ligne.get("conditions_paiement") else None
        devise = Devise.objects.filter(pk=ligne["devise"].upper()).first() if ligne.get("devise") else None
        tiers = Tiers(code=code, raison_sociale=nom, type_tiers=type_tiers, regime_fiscal=regime, siret=ligne.get("siret", ""),
                      numero_tva=ligne.get("numero_tva", ""), conditions_paiement=conditions, devise=devise)
        try:
            tiers.full_clean()
        except Exception as exc:  # ValidationError : on indique le motif et on passe à la ligne suivante
            rapport["ignores"].append((code, " ; ".join(getattr(exc, "messages", [str(exc)]))))
            continue
        tiers.save()
        crees.append(("commercial.Tiers", code))
        rapport["crees"].append(code)
    if crees:
        rapport["lot"] = lots._enregistrer_lot(utilisateur, Tiers, f"Import de {len(crees)} tiers", [], crees)
    return rapport
