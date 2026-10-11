"""Import de tableaux (CSV collé depuis un tableur ou fichier) avec rapprochement automatique des colonnes.

Principe commun à tous les écrans « Importer un tableau » :
1. on colle un tableau ou on choisit un fichier (séparateur ; , ou tabulation détecté sur la première ligne) ;
2. chaque colonne est rapprochée d'un champ de l'ERP d'après son en-tête — nom exact du champ ou de son libellé, synonyme
   connu, nom approchant (mots en commun, faute de frappe) — ou, à défaut, d'après ses valeurs (14 chiffres : SIRET…).
   L'utilisateur corrige au besoin ; une correspondance peut être mémorisée comme profil d'import, appliqué d'office la
   prochaine fois qu'un fichier aura les mêmes en-têtes ;
3. un aperçu ligne par ligne (« sera créé » / « ignorée : motif ») est calculé par un essai à blanc dans une transaction
   annulée, puis l'import réel crée un lot annulable (comptes/lots.py).

Chaque import se déclare par un `ImportTableau` : ses champs (`ChampImport`) et une fonction `creer(valeurs, journal)`
qui crée les objets d'une ligne ou lève `LigneIgnoree(motif)`."""

import csv
import io
import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

NIVEAUX = {
    "profil": "Profil mémorisé",
    "exact": "Exact",
    "synonyme": "Synonyme",
    "approchant": "Approchant",
    "contenu": "D'après le contenu",
    "manuel": "Choisi à la main",
    "": "Ignorée",
}
# Niveaux à faire vérifier d'un coup d'œil (couleur d'avertissement à l'écran).
NIVEAUX_A_VERIFIER = ("approchant", "contenu")
LIGNES_APERCU = 200
_MOTS_VIDES = {"de", "du", "la", "le", "les", "des", "d", "l", "n", "no", "num", "numero", "nr"}


class LigneIgnoree(Exception):
    """La ligne n'est pas importée ; le message est le motif affiché."""


@dataclass
class ChampImport:
    nom: str  # clé passée à `creer`
    libelle: str
    obligatoire: bool = False
    synonymes: tuple = ()
    motif: str = ""  # expression régulière reconnaissant les valeurs (rapprochement d'après le contenu)
    choix: object = None  # [(valeur, libellé)] ou fonction : une valeur peut alors être imposée à tout le fichier
    aide: str = ""

    def liste_choix(self):
        return list(self.choix() if callable(self.choix) else (self.choix or []))


@dataclass
class ImportTableau:
    cle: str  # identifiant stable (profils mémorisés)
    titre: str
    modele: type  # modèle tracé dans le lot
    champs: list
    creer: object  # (valeurs: {nom: texte}, journal: Journal) -> libellé court de l'objet créé
    aide: str = ""
    exemple: str = ""

    def champ(self, nom):
        return next((c for c in self.champs if c.nom == nom), None)


@dataclass
class Journal:
    """Ce qu'a fait une ligne : objets créés et modifications d'objets existants (pour le lot annulable)."""

    crees: list = field(default_factory=list)
    modifications: list = field(default_factory=list)

    def cree(self, objet):
        self.crees.append((objet._meta.label, objet.pk))
        return objet

    def modifie(self, objet, avant, apres):
        self.modifications.append({"pk": str(objet.pk), "avant": avant, "apres": apres})


# ---------------------------------------------------------------- lecture et rapprochement

def normaliser(texte):
    """« N° SIRET » → « n siret » : minuscules, sans accents, ponctuation et soulignés remplacés par des espaces."""
    texte = unicodedata.normalize("NFKD", str(texte or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", texte.lower()).strip()


def lire(texte):
    """(en-têtes, lignes) du tableau ; lignes vides écartées, lignes complétées à la largeur des en-têtes."""
    texte = (texte or "").replace("﻿", "").strip("\n\r ")
    if not texte:
        return [], []
    premiere = texte.splitlines()[0]
    delimiteur = max(";\t,", key=premiere.count)
    lignes = [[(c or "").strip() for c in ligne] for ligne in csv.reader(io.StringIO(texte), delimiter=delimiteur)]
    entetes = lignes[0]
    corps = [(l + [""] * len(entetes))[: len(entetes)] for l in lignes[1:] if any(l)]
    return entetes, corps


def signature(entetes):
    return "|".join(normaliser(e) for e in entetes)


def _bigrammes(texte):
    t = texte.replace(" ", "")
    return [t[i : i + 2] for i in range(len(t) - 1)]


def similarite(a, b):
    """Coefficient de Dice sur les bigrammes (0 à 1) : tolère fautes de frappe et petites variantes."""
    A, B = _bigrammes(a), _bigrammes(b)
    if not A or not B:
        return 0.0
    reste = list(B)
    communs = 0
    for x in A:
        if x in reste:
            reste.remove(x)
            communs += 1
    return 2 * communs / (len(A) + len(B))


def _mots(texte):
    return {m for m in texte.split() if len(m) > 2 and m not in _MOTS_VIDES}


def candidats(entete, valeurs, champs):
    """[(score, nom du champ, niveau, raison)] pour une colonne, du plus probable au moins probable."""
    e = normaliser(entete)
    sortie = []
    for champ in champs:
        noms = [normaliser(champ.nom), normaliser(champ.libelle)]
        synonymes = [normaliser(s) for s in champ.synonymes]
        if e and e in noms:
            sortie.append((100, champ.nom, "exact", "nom du champ"))
            continue
        if e and e in synonymes:
            sortie.append((90, champ.nom, "synonyme", "synonyme connu"))
            continue
        proche, meilleur = max(((n, similarite(e, n)) for n in noms + synonymes), key=lambda x: x[1])
        commun = next((n for n in noms + synonymes if _mots(e) & _mots(n)), None)
        if e and (meilleur >= 0.75 or commun):
            sortie.append((round(55 + 30 * meilleur), champ.nom, "approchant", f"proche de « {commun or proche} »"))
            continue
        if champ.motif:
            pleines = [v for v in valeurs if v]
            bonnes = sum(1 for v in pleines if re.fullmatch(champ.motif, v.replace(" ", ""), re.IGNORECASE))
            if pleines and bonnes / len(pleines) >= 0.8:
                sortie.append((50, champ.nom, "contenu", f"{bonnes}/{len(pleines)} valeurs au bon format"))
    return sorted(sortie, reverse=True)


def rapprocher(entetes, lignes, champs, profil=None):
    """Champ proposé pour chaque colonne : [{"champ": nom ou "", "niveau": …, "raison": …}]. Chaque champ ne va qu'à une
    colonne (la mieux notée). Un profil mémorisé l'emporte pour les en-têtes qu'il connaît."""
    connus = {c.nom for c in champs}
    proposes = [{"champ": "", "niveau": "", "raison": "aucun nom ni format reconnu"} for _ in entetes]
    pris = set()
    if profil:
        for i, entete in enumerate(entetes):
            nom = profil.correspondance.get(normaliser(entete))
            if nom is not None and (nom == "" or (nom in connus and nom not in pris)):
                proposes[i] = {"champ": nom, "niveau": "profil" if nom else "", "raison": f"profil « {profil.nom} »"}
                pris.add(nom)
    libres = [i for i, e in enumerate(entetes) if not (profil and normaliser(e) in profil.correspondance)]
    tous = []
    for i in libres:
        for score, nom, niveau, raison in candidats(entetes[i], [l[i] for l in lignes[:50]], champs):
            tous.append((score, -i, i, nom, niveau, raison))
    for score, _, i, nom, niveau, raison in sorted(tous, reverse=True):
        if proposes[i]["champ"] or nom in pris:
            if not proposes[i]["champ"] and nom in pris and score >= 85:
                proposes[i]["raison"] = f"aussi candidate pour « {next(c.libelle for c in champs if c.nom == nom)} », déjà prise"
            continue
        proposes[i] = {"champ": nom, "niveau": niveau, "raison": raison}
        pris.add(nom)
    return proposes


# ---------------------------------------------------------------- conversions communes

def choix(valeur, liste, defaut=None):
    """Code du choix dont le code ou le libellé correspond (sans casse ni accents) ; `defaut` si vide ; LigneIgnoree sinon."""
    v = normaliser(valeur)
    if not v:
        return defaut
    for code, libelle in liste:
        if v in (normaliser(code), normaliser(libelle)):
            return code
    raise LigneIgnoree(f"« {valeur} » inconnu (attendu : {', '.join(str(l) for _, l in liste)})")


def oui_non(valeur, defaut=None):
    v = normaliser(valeur)
    if not v:
        return defaut
    if v in ("oui", "o", "vrai", "true", "yes", "y", "1", "x"):
        return True
    if v in ("non", "n", "faux", "false", "no", "0"):
        return False
    raise LigneIgnoree(f"« {valeur} » : oui ou non attendu")


def nombre(valeur, decimal=False):
    """« 1 240,50 » → 1240.5 ; vide → None."""
    v = (valeur or "").replace(" ", "").replace(" ", "").replace("€", "").replace(",", ".")
    if not v:
        return None
    try:
        return Decimal(v) if decimal else float(v)
    except (InvalidOperation, ValueError):
        raise LigneIgnoree(f"« {valeur} » n'est pas un nombre") from None


def date(valeur):
    """JJ/MM/AAAA ou AAAA-MM-JJ ; vide → None."""
    import datetime

    v = (valeur or "").strip()
    if not v:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d/%m/%y", "%d-%m-%Y"):
        try:
            return datetime.datetime.strptime(v, fmt).date()
        except ValueError:
            pass
    raise LigneIgnoree(f"« {valeur} » n'est pas une date (JJ/MM/AAAA)")


def enregistrer(objet, journal):
    """full_clean puis save ; une erreur de validation devient le motif de la ligne ignorée."""
    try:
        objet.full_clean()
    except ValidationError as exc:
        raise LigneIgnoree(" ; ".join(exc.messages)) from None
    objet.save()
    return journal.cree(objet)


# ---------------------------------------------------------------- exécution

class _EssaiABlanc(Exception):
    pass


def executer(spec, lignes, correspondance, defauts, utilisateur=None, essai=True):
    """Importe les lignes. `correspondance` : [nom de champ ou "" pour chaque colonne] ; `defauts` : {champ: valeur imposée
    quand le champ n'a pas de colonne}. En essai, tout est annulé à la fin. Retourne {lignes: [(n°, valeurs, objet, motif)],
    crees, ignores, lot}."""
    from . import lots

    rapport = {"lignes": [], "crees": 0, "ignores": 0, "lot": None}
    crees, modifications = [], []
    try:
        with transaction.atomic():
            for numero, cellules in enumerate(lignes, start=2):  # 1 = en-têtes
                valeurs = {nom: v for nom, v in defauts.items() if v not in (None, "")}
                for nom, v in zip(correspondance, cellules):
                    if nom and v:
                        valeurs[nom] = v
                manquants = [c.libelle for c in spec.champs if c.obligatoire and not valeurs.get(c.nom)]
                journal = Journal()
                try:
                    if manquants:
                        raise LigneIgnoree(", ".join(manquants) + " manquant(s)")
                    with transaction.atomic():  # une ligne en échec est défaite sans toucher aux autres
                        objet = spec.creer(valeurs, journal)
                except LigneIgnoree as exc:
                    rapport["ignores"] += 1
                    rapport["lignes"].append((numero, valeurs, None, str(exc)))
                    continue
                except IntegrityError:
                    rapport["ignores"] += 1
                    rapport["lignes"].append((numero, valeurs, None, "déjà présent ou en conflit avec un enregistrement existant"))
                    continue
                rapport["crees"] += 1
                rapport["lignes"].append((numero, valeurs, str(objet), ""))
                crees += journal.crees
                modifications += journal.modifications
            if essai:
                raise _EssaiABlanc
            if crees or modifications:
                rapport["lot"] = lots._enregistrer_lot(
                    utilisateur, spec.modele, f"{spec.titre} : {rapport['crees']} ligne(s)", modifications, crees
                )
    except _EssaiABlanc:
        pass
    return rapport
