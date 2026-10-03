"""Moteur de modèles de documents : HTML (édité dans GrapesJS) + données de l'ERP -> PDF (WeasyPrint).

Pas de moteur de gabarits généraliste (Django, Jinja) : un modèle modifiable par un utilisateur ne doit
jamais pouvoir exécuter quoi que ce soit côté serveur. Le langage est volontairement minimal et reste du
HTML valide, que GrapesJS conserve tel quel :

- `{{ chemin.vers.valeur }}`          valeur lue dans les données du document (échappée) ;
                                       les chemins se terminant par `_html` sont insérés tels quels
                                       (blocs que l'ERP construit lui-même, déjà échappés) ;
- `data-repeat="lignes"`              répète l'élément pour chaque élément de la liste ;
                                       dans l'élément, l'élément courant est `ligne` (ou `data-as="x"`) ;
- `data-if="chemin"` / `"!chemin"`    affiche l'élément seulement si la valeur est non vide (ou vide).

Rien d'autre n'est interprété : `{% … %}`, scripts, gestionnaires d'événements, ressources externes
(`http://`, `file://`) sont ignorés ou bloqués. Les données passées au moteur sont des dictionnaires et des
listes de textes déjà mis en forme, jamais des objets de la base."""

import html
import logging
import re
from html.parser import HTMLParser

logger = logging.getLogger(__name__)

ELEMENTS_VIDES = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
ELEMENTS_INTERDITS = {"script", "iframe", "object", "embed", "link", "meta", "base", "form"}
_VARIABLE = re.compile(r"\{\{\s*([A-Za-z_]\w*(?:\.\w+)*)\s*\}\}")
_ATTRIBUTS_MOTEUR = re.compile(r"""\s+data-(?:if|repeat|as)\s*=\s*(?:"[^"]*"|'[^']*')""")
_ATTRIBUT = re.compile(r"""\bdata-(if|repeat|as)\s*=\s*(?:"([^"]*)"|'([^']*)')""")
_EVENEMENTS = re.compile(r"""\s+on\w+\s*=\s*(?:"[^"]*"|'[^']*')""", re.IGNORECASE)


class ErreurModele(Exception):
    """Le modèle ne peut pas être rendu (HTML inexploitable, trop volumineux…)."""


class _Element:
    __slots__ = ("balise", "debut", "enfants")

    def __init__(self, balise, debut):
        self.balise, self.debut, self.enfants = balise, debut, []


class _Arbre(HTMLParser):
    """Arbre minimal, assez fidèle pour réécrire le HTML (le HTML produit par GrapesJS est bien formé)."""

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.racine = _Element(None, "")
        self.pile = [self.racine]

    def handle_starttag(self, tag, attrs):
        element = _Element(tag, self.get_starttag_text())
        self.pile[-1].enfants.append(element)
        if tag not in ELEMENTS_VIDES:
            self.pile.append(element)

    def handle_startendtag(self, tag, attrs):
        self.pile[-1].enfants.append(_Element(tag, self.get_starttag_text()))

    def handle_endtag(self, tag):
        # Ferme jusqu'à l'élément ouvert correspondant ; une balise fermante orpheline est ignorée.
        for i in range(len(self.pile) - 1, 0, -1):
            if self.pile[i].balise == tag:
                del self.pile[i:]
                return

    def handle_data(self, data):
        self.pile[-1].enfants.append(data)

    def handle_entityref(self, name):
        self.pile[-1].enfants.append(f"&{name};")

    def handle_charref(self, name):
        self.pile[-1].enfants.append(f"&#{name};")

    def handle_comment(self, data):
        pass


def _lire(contexte, chemin, inconnues=None):
    valeur = contexte
    for morceau in chemin.split("."):
        if isinstance(valeur, dict) and morceau in valeur:
            valeur = valeur[morceau]
        elif isinstance(valeur, (list, tuple)) and morceau.isdigit() and int(morceau) < len(valeur):
            valeur = valeur[int(morceau)]
        else:
            if inconnues is not None:
                inconnues.add(chemin)
            return ""
    return "" if valeur is None else valeur


def _substituer(texte, contexte, inconnues, attribut=False):
    def remplacer(m):
        chemin = m.group(1)
        valeur = _lire(contexte, chemin, inconnues)
        if isinstance(valeur, (list, dict)):
            return ""
        valeur = str(valeur)
        if chemin.rsplit(".", 1)[-1].endswith("_html") and not attribut:
            return valeur
        return html.escape(valeur, quote=True)

    return _VARIABLE.sub(remplacer, texte)


def _vrai(contexte, expression, inconnues):
    expression = expression.strip()
    inverse = expression.startswith("!")
    valeur = _lire(contexte, expression.lstrip("!").strip(), inconnues)
    present = bool(valeur) and valeur not in ("0", "0,00 €")
    return (not present) if inverse else present


def _rendre(noeud, contexte, inconnues, sortie):
    if isinstance(noeud, str):
        sortie.append(_substituer(noeud, contexte, inconnues))
        return
    if noeud.balise is None:
        for enfant in noeud.enfants:
            _rendre(enfant, contexte, inconnues, sortie)
        return
    if noeud.balise in ELEMENTS_INTERDITS:
        return

    commandes = {m.group(1): m.group(2) if m.group(2) is not None else m.group(3) for m in _ATTRIBUT.finditer(noeud.debut)}
    debut = _EVENEMENTS.sub("", _ATTRIBUTS_MOTEUR.sub("", noeud.debut))
    # `data-src` devient `src` au rendu : l'éditeur n'essaie pas de charger « {{ societe.logo }} » comme une adresse.
    if re.search(r"\sdata-src\s*=", debut):
        # GrapesJS ajoute lui-même un `src` de substitution (image grise) : c'est `data-src` qui doit gagner.
        debut = re.sub(r"""\ssrc\s*=\s*(?:"[^"]*"|'[^']*')""", "", debut)
        debut = re.sub(r"(\s)data-src(\s*=)", r"\1src\2", debut)

    contextes = [contexte]
    if "repeat" in commandes:
        liste = _lire(contexte, commandes["repeat"].strip(), inconnues)
        alias = (commandes.get("as") or "ligne").strip()
        contextes = [
            {**contexte, alias: element, "rang": i + 1} for i, element in enumerate(liste if isinstance(liste, list) else [])
        ]
    for ctx in contextes:
        if "if" in commandes and not _vrai(ctx, commandes["if"], inconnues):
            continue
        sortie.append(_substituer(debut, ctx, inconnues, attribut=True))
        for enfant in noeud.enfants:
            _rendre(enfant, ctx, inconnues, sortie)
        if noeud.balise not in ELEMENTS_VIDES:
            sortie.append(f"</{noeud.balise}>")


def appliquer(html_modele, contexte):
    """Rendu du modèle avec les données : (html, chemins_inconnus)."""
    if len(html_modele) > 2_000_000:
        raise ErreurModele("Le modèle est trop volumineux.")
    arbre = _Arbre()
    try:
        arbre.feed(html_modele)
        arbre.close()
    except Exception as exc:  # HTML irrécupérable
        raise ErreurModele(f"HTML illisible : {exc}") from exc
    inconnues, sortie = set(), []
    _rendre(arbre.racine, contexte, inconnues, sortie)
    return "".join(sortie), sorted(inconnues)


def variables_utilisees(html_modele):
    """Chemins `{{ … }}` présents dans le modèle (pour avertir des fautes de frappe à l'enregistrement)."""
    return sorted({m.group(1) for m in _VARIABLE.finditer(html_modele)})


def page_complete(corps_html, css_modele, css_base):
    return (
        '<!DOCTYPE html><html lang="fr"><head><meta charset="utf-8">'
        f"<style>{css_base}</style><style>{_assainir_css(css_modele)}</style></head><body>{corps_html}</body></html>"
    )


def _assainir_css(css):
    """Retire `@import` (chargement externe) ; les `url()` externes sont de toute façon bloqués au rendu."""
    return re.sub(r"@import[^;]*;", "", css or "", flags=re.IGNORECASE).replace("</style", "")


def vers_pdf(corps_html, css_modele, css_base, titre=""):
    from weasyprint import HTML

    # Seules les images intégrées (`data:`) sont chargées : ni fichier local, ni requête réseau.
    from weasyprint.urls import URLFetcher

    document = HTML(
        string=page_complete(corps_html, css_modele, css_base), url_fetcher=URLFetcher(allowed_protocols={"data"})
    ).render()
    if titre:
        document.metadata.title = titre
    return document


def ecrire(documents):
    """Fusionne un ou plusieurs documents WeasyPrint rendus en un seul PDF (octets)."""
    documents = list(documents)
    pages = [page for d in documents for page in d.pages]
    return documents[0].copy(pages).write_pdf()
