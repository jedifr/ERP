"""Format d'échange ISACOMPTA (fichier texte à largeur fixe, fins de ligne CRLF, ASCII).

Structure d'un fichier (relevée sur des exports réels de ventes, d'achats et de banque) :

    VER   02000008550                  version du format
    DOS …                              dossier (laissé vide)
    EXO …                              exercice (laissé vide)
    ECR …                              une écriture : journal, date, pièce, libellé, type
    MVT …                              un mouvement de l'écriture : compte, libellé, débit ou crédit
    ECHMVT …                           (après le mouvement du tiers) échéance : montant, 100 %, date

Largeurs : ECR 296, MVT 133, ECH 97. Les montants s'écrivent avec un point décimal, alignés à droite ; le libellé d'un
mouvement et le compte sont alignés à droite, ceux d'une écriture à gauche.

Ce module ne connaît pas l'ERP : il ne fait que mettre en forme des données simples (voir export_comptable.py)."""

import datetime
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal

FIN_DE_LIGNE = "\r\n"
LIGNE_VERSION = "VER   02000008550"
LARGEUR_ECR, LARGEUR_MVT, LARGEUR_ECH = 296, 133, 97
LARGEUR_LIBELLE = 30
LARGEUR_PIECE = 8
LARGEUR_REFERENCE = 15
LARGEUR_COMPTE = 8


class ErreurFormat(Exception):
    """Donnée qui ne tient pas dans le format (compte trop long…)."""


def ascii_simple(texte):
    """Texte sans accents ni caractère hors ASCII (le fichier est lu par un logiciel Windows ancien)."""
    decompose = unicodedata.normalize("NFKD", texte or "")
    propre = "".join(c for c in decompose if not unicodedata.combining(c))
    propre = propre.replace("’", "'").replace("–", "-").replace("—", "-")
    return "".join(c if 32 <= ord(c) < 127 else " " for c in propre)


def _gauche(texte, largeur):
    return ascii_simple(texte)[:largeur].ljust(largeur)


def _droite(texte, largeur):
    return ascii_simple(texte)[:largeur].rjust(largeur)


def _poser(largeur, *champs):
    """Ligne de `largeur` espaces où chaque (position, texte) est posé à sa place."""
    ligne = [" "] * largeur
    for position, texte in champs:
        ligne[position:position + len(texte)] = texte
    resultat = "".join(ligne)
    if len(resultat) != largeur:
        raise ErreurFormat(f"Ligne de {len(resultat)} caractères au lieu de {largeur}.")
    return resultat


def montant(valeur):
    """Montant positif au format du fichier : « 1234.50 »."""
    return f"{Decimal(valeur).quantize(Decimal('0.01')):f}"


def date_jjmmaaaa(valeur):
    return valeur.strftime("%d%m%Y")


def compte(code):
    """Compte tel qu'écrit dans le fichier : un compte général (chiffres seuls) est complété à 6 chiffres, un compte de tiers
    (411DUPON, 401ESTAC…) reste tel quel. 8 caractères au plus."""
    code = (code or "").strip().upper()
    if code.isdigit() and len(code) < 6:
        code = code.ljust(6, "0")
    if not code:
        raise ErreurFormat("Compte comptable vide.")
    if len(code) > LARGEUR_COMPTE:
        raise ErreurFormat(f"Le compte « {code} » dépasse {LARGEUR_COMPTE} caractères (limite du format).")
    return code


@dataclass
class Mouvement:
    compte: str
    libelle: str
    debit: Decimal = Decimal("0")
    credit: Decimal = Decimal("0")
    tiers: bool = False  # ligne du compte de tiers : porte l'échéance
    echeance: datetime.date | None = None  # None : pas de ligne d'échéance


@dataclass
class Ecriture:
    journal: str  # code à 2 caractères
    date: datetime.date
    piece: str
    libelle: str
    type_piece: str  # « fa » facture, « re » règlement
    reference_externe: str = ""
    mouvements: list = field(default_factory=list)

    def verifier_equilibre(self):
        debit = sum((m.debit for m in self.mouvements), Decimal("0"))
        credit = sum((m.credit for m in self.mouvements), Decimal("0"))
        if debit != credit:
            raise ErreurFormat(f"Écriture « {self.piece or self.libelle} » déséquilibrée : débit {debit}, crédit {credit}.")


def ligne_ecr(e, date_export):
    export = date_jjmmaaaa(date_export)
    return _poser(
        LARGEUR_ECR, (0, "ECR"), (6, _gauche(e.journal, 2)), (8, date_jjmmaaaa(e.date)), (16, _gauche(e.piece, LARGEUR_PIECE)),
        (24, _gauche(e.libelle, LARGEUR_LIBELLE)), (76, "1"), (91, "0" + export + export + "0"), (119, "0EUR"), (125, "0"),
        (136, "0"), (186, _gauche(e.type_piece, 2)), (271, _gauche(e.reference_externe, LARGEUR_REFERENCE)),
    )


def ligne_mvt(m):
    champs = [(0, "MVT"), (8, _droite(compte(m.compte), LARGEUR_COMPTE)), (16, _droite(m.libelle, LARGEUR_LIBELLE))]
    if m.debit:
        champs.append((46, _droite(montant(m.debit), 13)))
    elif m.credit:
        champs.append((59, _droite(montant(m.credit), 13)))
    champs += [(72, _droite("0.00", 11)), (122, "1" if m.tiers else "0")]
    return _poser(LARGEUR_MVT, *champs)


def ligne_ech(m):
    return _poser(
        LARGEUR_ECH, (0, "ECHMVT"), (6, _droite(montant(m.debit or m.credit), 13)), (20, "100.00000"), (29, date_jjmmaaaa(m.echeance))
    )


def fichier(ecritures, date_export):
    """Texte complet du fichier (fins de ligne CRLF, terminé par une fin de ligne)."""
    lignes = [LIGNE_VERSION, "DOS".ljust(44), "EXO".ljust(6)]
    for e in ecritures:
        e.verifier_equilibre()
        lignes.append(ligne_ecr(e, date_export))
        for m in e.mouvements:
            lignes.append(ligne_mvt(m))
            if m.tiers and m.echeance is not None:
                lignes.append(ligne_ech(m))
    return FIN_DE_LIGNE.join(lignes) + FIN_DE_LIGNE
