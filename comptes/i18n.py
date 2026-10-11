"""Compilation des catalogues de traduction sans les outils gettext (msgfmt) : lit `locale/<langue>/LC_MESSAGES/django.po` et écrit le `.mo`
correspondant. Format .mo : https://www.gnu.org/software/gettext/manual/html_node/MO-Files.html. Gère les pluriels (msgid_plural, msgstr[n])."""

import array
import ast
import struct
from pathlib import Path


def _chaine(ligne):
    return ast.literal_eval(ligne.strip())


def lire_po(chemin):
    """{msgid ou 'msgid\\0msgid_plural': msgstr ou 'msgstr0\\0msgstr1'} d'un fichier .po (entrées non vides seulement)."""
    entrees, courant, section = {}, {}, None
    texte = Path(chemin).read_text(encoding="utf-8")

    def clore():
        if "msgid" in courant:
            msgid = courant["msgid"]
            if "msgid_plural" in courant:
                msgid += "\0" + courant["msgid_plural"]
                msgstr = "\0".join(courant[f"msgstr[{i}]"] for i in range(len([k for k in courant if k.startswith("msgstr[")])))
            else:
                msgstr = courant.get("msgstr", "")
            if msgstr.replace("\0", ""):
                entrees[msgid] = msgstr
        courant.clear()

    for ligne in texte.splitlines():
        if not ligne.strip():
            clore()
            section = None
            continue
        if ligne.startswith("#"):
            continue
        if ligne.startswith('"'):
            courant[section] += _chaine(ligne)
            continue
        cle, _, valeur = ligne.partition(" ")
        section = cle
        courant[section] = _chaine(valeur)
    clore()
    return entrees


def compiler(chemin_po):
    """Contenu binaire du .mo correspondant au .po."""
    entrees = lire_po(chemin_po)
    cles = sorted(entrees)
    ids, chaines = b"", b""
    offsets = []
    for cle in cles:
        a, b = cle.encode("utf-8"), entrees[cle].encode("utf-8")
        offsets.append((len(ids), len(a), len(chaines), len(b)))
        ids += a + b"\0"
        chaines += b + b"\0"
    n = len(cles)
    debut_cles = 7 * 4 + 16 * n
    debut_valeurs = debut_cles + len(ids)
    table_cles, table_valeurs = array.array("i"), array.array("i")
    for o1, l1, o2, l2 in offsets:
        table_cles += array.array("i", [l1, o1 + debut_cles])
        table_valeurs += array.array("i", [l2, o2 + debut_valeurs])
    en_tete = struct.pack("Iiiiiii", 0x950412DE, 0, n, 7 * 4, 7 * 4 + n * 8, 0, 0)
    return en_tete + table_cles.tobytes() + table_valeurs.tobytes() + ids + chaines


def compiler_fichier(chemin_po):
    chemin_po = Path(chemin_po)
    chemin_mo = chemin_po.with_suffix(".mo")
    chemin_mo.write_bytes(compiler(chemin_po))
    return chemin_mo
