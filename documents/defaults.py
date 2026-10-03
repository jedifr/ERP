"""Mise en page par défaut des documents de vente (celle des PDF d'origine) et blocs de l'éditeur.

Le HTML ne contient que des éléments ordinaires : la logique passe par `data-repeat`, `data-if` et
`{{ variable }}` (voir moteur.py), que GrapesJS conserve sans les déformer — une boucle `{% for %}` à l'intérieur
d'un tableau serait, elle, déplacée hors du tableau par l'analyseur HTML du navigateur."""

from .models import ModeleDocument

T = ModeleDocument.Type

# Réglages de page : toujours appliqués, non modifiables dans l'éditeur visuel (GrapesJS ne gère pas @page).
CSS_BASE = """
@page { size: A4; margin: 15mm 18mm 26mm 18mm;
  @bottom-right { content: "Page " counter(page) " / " counter(pages); font-size: 7pt; color: #6b7280;
                  font-family: "Liberation Sans", Helvetica, Arial, FreeSans, "DejaVu Sans", sans-serif; } }
html { font-family: "Liberation Sans", Helvetica, Arial, FreeSans, "DejaVu Sans", sans-serif; font-size: 9pt;
       color: #000; line-height: 1.35; }
body { margin: 0; }
table { border-collapse: collapse; }
img { max-width: 100%; }
"""

# Pour l'aperçu dans l'éditeur : approche de la page A4 (la page réelle est rendue par WeasyPrint).
CSS_CANVAS = """
html, body { background: #fff; }
body { box-sizing: border-box; max-width: 210mm; min-height: 297mm; padding: 12mm 14mm; margin: 0 auto;
       font-family: "Liberation Sans", Helvetica, Arial, FreeSans, "DejaVu Sans", sans-serif; font-size: 9pt; line-height: 1.35; }
table { border-collapse: collapse; }
img { max-width: 100%; }
/* Éléments répétés sur chaque page ou en filigrane : affichés à leur place dans le flux, pour pouvoir les éditer. */
.doc-pied { position: static !important; margin-top: 8mm; }
.doc-filigrane { position: static !important; transform: none !important; font-size: 16pt !important; text-align: left !important; }
"""

CSS_DEFAUT = """
.doc-entete { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 8mm; }
.doc-logo { max-width: 45mm; max-height: 22mm; text-align: right; }
.doc-logo img { max-width: 45mm; max-height: 22mm; }
.doc-titre-ligne { display: flex; justify-content: space-between; align-items: flex-end; margin-bottom: 5mm; }
.doc-titre { font-size: 18pt; font-weight: bold; color: #b45309; margin: 0; max-width: 100mm; line-height: 1.2; }
.doc-references { text-align: right; }
.doc-adresses { display: flex; gap: 6mm; margin-bottom: 6mm; }
.doc-adresse { width: 50%; }
.doc-adresse-titre { font-weight: bold; margin-bottom: 1mm; }
.doc-intro { margin: 0 0 4mm 0; }
.doc-lignes { width: 100%; margin-bottom: 4mm; }
.doc-lignes thead tr { background: #374151; }
.doc-lignes th { color: #fff; font-size: 8pt; text-align: left; padding: 2mm 1.5mm; }
.doc-lignes td { padding: 1.8mm 1.5mm; border-bottom: 0.25pt solid #e5e7eb; vertical-align: top; }
.doc-lignes tr { page-break-inside: avoid; }
.doc-lignes .num { text-align: right; }
.doc-totaux { width: 74mm; margin-left: auto; margin-bottom: 6mm; }
.doc-totaux td { padding: 1mm 1.5mm; }
.doc-totaux .num { text-align: right; }
.doc-total-ttc td { font-weight: bold; border-top: 0.8pt solid #6b7280; }
.doc-conditions { margin: 0 0 2mm 0; }
.doc-mentions { font-size: 7.5pt; color: #6b7280; white-space: pre-line; margin: 0 0 5mm 0; }
.doc-signature { width: 80mm; height: 32mm; border: 0.6pt solid #6b7280; margin-left: auto; padding: 2mm; page-break-inside: avoid; }
.doc-case { text-align: right; }
.doc-section-titre { font-weight: bold; margin: 5mm 0 2mm 0; }
.doc-pied { position: fixed; bottom: -17mm; left: 0; right: 0; text-align: center; font-size: 7pt; color: #6b7280;
            border-top: 0.25pt solid #e5e7eb; padding-top: 1.5mm; }
.doc-filigrane { position: fixed; top: 110mm; left: 0; right: 0; text-align: center; font-size: 60pt; font-weight: bold;
                 color: rgba(217, 51, 51, 0.16); transform: rotate(-35deg); }
.doc-saut-de-page { break-after: page; }
"""

# --- fragments ----------------------------------------------------------------------------------------------

ENTETE = (
    '<div class="doc-entete"><div class="doc-coordonnees">{{ societe.coordonnees_html }}</div>'
    '<div class="doc-logo"><img data-if="societe.logo" data-src="{{ societe.logo }}" alt="Logo"></div></div>'
)
TITRE = (
    '<div class="doc-titre-ligne"><h1 class="doc-titre">{{ document.titre }}</h1>'
    '<div class="doc-references">{{ document.references_html }}</div></div>'
)
ADRESSES = (
    '<div class="doc-adresses">'
    '<div class="doc-adresse"><div class="doc-adresse-titre">Facturé à</div><div>{{ facturation.bloc_html }}</div></div>'
    '<div class="doc-adresse"><div class="doc-adresse-titre">Livré à</div><div>{{ livraison.bloc_html }}</div></div></div>'
)
PIED = '<div class="doc-pied">{{ societe.pied_html }}</div>'
FILIGRANE = '<div class="doc-filigrane" data-if="document.filigrane">{{ document.filigrane }}</div>'
SAUT_DE_PAGE = '<div class="doc-saut-de-page"></div>'


def _tableau(entetes, cellules, liste="lignes", alias="ligne", classe="doc-lignes"):
    """entetes: [(libellé, numérique)] ; cellules: [expression ou texte] (même longueur)."""
    def cls(num):
        return ' class="num"' if num else ""

    tete = "".join(f"<th{cls(num)}>{libelle}</th>" for libelle, num in entetes)
    corps = "".join(f"<td{cls(num)}>{valeur}</td>" for (_, num), valeur in zip(entetes, cellules))
    return (
        f'<table class="{classe}"><thead><tr>{tete}</tr></thead>'
        f'<tbody><tr data-repeat="{liste}" data-as="{alias}">{corps}</tr></tbody></table>'
    )


LIGNES_DEVIS = _tableau(
    [("Désignation", False), ("Qté", True), ("PU HT", True), ("TVA", True), ("Total HT", True)],
    ["{{ ligne.designation_html }}", "{{ ligne.quantite }}", "{{ ligne.pu_ht }}", "{{ ligne.tva }}", "{{ ligne.total_ht }}"],
)
LIGNES_AR = _tableau(
    [("Désignation", False), ("Qté", True), ("PU HT", True), ("TVA", True), ("Total HT", True), ("Livraison prévue", True)],
    ["{{ ligne.designation_html }}", "{{ ligne.quantite }}", "{{ ligne.pu_ht }}", "{{ ligne.tva }}", "{{ ligne.total_ht }}",
     "{{ ligne.livraison_prevue }}"],
)
LIGNES_PREPARATION = _tableau(
    [("Désignation", False), ("Commandé", True), ("À livrer", True), ("Livraison prévue", True), ("Origine / stock", False), ("Prêt", True)],
    ["{{ ligne.designation_html }}", "{{ ligne.quantite }}", "{{ ligne.a_livrer }}", "{{ ligne.livraison_prevue }}",
     "{{ ligne.origine }}", "[&nbsp;&nbsp;&nbsp;]"],
)
LIGNES_LIVRAISON = _tableau(
    [("Désignation", False), ("Livré", True), ("Commandé", True), ("Reliquat", True), ("N° de coulée", False)],
    ["{{ ligne.designation_html }}", "{{ ligne.livre }}", "{{ ligne.commande }}", "{{ ligne.reliquat }}", "{{ ligne.coulees }}"],
)
COMPOSANTS = _tableau(
    [("Composant", False), ("Dimensions (mm)", False), ("Par pièce", True), ("À sortir", True)],
    ["{{ composant.designation_html }}", "{{ composant.dimensions }}", "{{ composant.par_piece }}", "{{ composant.a_sortir }}"],
    liste="composants", alias="composant",
)
OPERATIONS = _tableau(
    [("N°", True), ("Poste / opération", False), ("Temps prévu", True), ("Fait", True)],
    ["{{ operation.ordre }}", "{{ operation.poste }}", "{{ operation.temps }}", "[&nbsp;&nbsp;&nbsp;]"],
    liste="operations", alias="operation",
)
TOTAUX = (
    '<table class="doc-totaux"><tbody>'
    '<tr><td>Total HT</td><td class="num">{{ totaux.ht }}</td></tr>'
    '<tr data-repeat="totaux.tva" data-as="t"><td>TVA {{ t.taux }}</td><td class="num">{{ t.montant }}</td></tr>'
    '<tr class="doc-total-ttc"><td>Total TTC</td><td class="num">{{ totaux.ttc }}</td></tr></tbody></table>'
)
CONDITIONS_DEVIS = (
    '<p class="doc-conditions" data-if="delai"><b>Délai :</b> {{ delai }}</p>'
    '<p class="doc-conditions" data-if="reglement"><b>Règlement :</b> {{ reglement }}</p>'
)
CONDITIONS_AR = '<p class="doc-conditions" data-if="reglement"><b>Règlement :</b> {{ reglement }}</p>'
MENTIONS = '<p class="doc-mentions" data-if="mentions">{{ mentions }}</p>'
SIGNATURE_CLIENT = (
    '<p>Bon pour accord — date, nom, signature et cachet :</p><div class="doc-signature"><b>Le client</b></div>'
)
SIGNATURE_DESTINATAIRE = (
    '<p>Marchandise reçue en bon état — date, nom et signature :</p><div class="doc-signature"><b>Le destinataire</b></div>'
)
INTRO_AR = (
    '<p class="doc-intro">Nous accusons réception de votre commande et vous en remercissons. '
    "Elle sera livrée aux dates indiquées ci-dessous.</p>"
)
PREPARE_PAR = '<p>Préparé par : ____________________ &nbsp;&nbsp; Date : ____________________</p>'
RESUME_OF = (
    '<p><b>{{ of.reference }}</b> — {{ of.libelle }}<br>Quantité à fabriquer : <b>{{ of.quantite }}</b></p>'
)


def _page(*blocs):
    return "\n".join(blocs)


MODELES = {
    T.DEVIS: _page(FILIGRANE, ENTETE, TITRE, ADRESSES, LIGNES_DEVIS, TOTAUX, CONDITIONS_DEVIS, MENTIONS, SIGNATURE_CLIENT, PIED),
    T.AR_COMMANDE: _page(FILIGRANE, ENTETE, TITRE, ADRESSES, INTRO_AR, LIGNES_AR, TOTAUX, CONDITIONS_AR, MENTIONS, PIED),
    T.BON_PREPARATION: _page(FILIGRANE, ENTETE, TITRE, ADRESSES, LIGNES_PREPARATION, PREPARE_PAR, PIED),
    T.BON_LIVRAISON: _page(FILIGRANE, ENTETE, TITRE, ADRESSES, LIGNES_LIVRAISON, MENTIONS, SIGNATURE_DESTINATAIRE, PIED),
    T.FICHE_FABRICATION: _page(
        ENTETE, TITRE, ADRESSES, RESUME_OF,
        '<div class="doc-section-titre">Nomenclature</div>', COMPOSANTS,
        '<div class="doc-section-titre">Gamme</div>', OPERATIONS, PIED,
    ),
}


def modele_par_defaut(type_document):
    return MODELES[type_document], CSS_DEFAUT.strip()


def blocs_editeur(type_document):
    """Blocs « Document » proposés dans l'éditeur (ceux du modèle par défaut, à remettre en place si supprimés)."""
    tables = {
        T.DEVIS: ("Tableau des lignes", LIGNES_DEVIS), T.AR_COMMANDE: ("Tableau des lignes", LIGNES_AR),
        T.BON_PREPARATION: ("Tableau des lignes", LIGNES_PREPARATION), T.BON_LIVRAISON: ("Tableau des lignes", LIGNES_LIVRAISON),
    }
    blocs = [
        ("en-tete", "En-tête société (logo + coordonnées)", ENTETE),
        ("titre", "Titre et références", TITRE),
        ("adresses", "Facturé à / Livré à", ADRESSES),
    ]
    if type_document in tables:
        libelle, html = tables[type_document]
        blocs.append(("lignes", libelle, html))
    if type_document in (T.DEVIS, T.AR_COMMANDE):
        blocs.append(("totaux", "Totaux HT / TVA / TTC", TOTAUX))
    if type_document == T.DEVIS:
        blocs += [("conditions", "Délai et règlement", CONDITIONS_DEVIS), ("signature", "Bon pour accord", SIGNATURE_CLIENT)]
    if type_document == T.AR_COMMANDE:
        blocs += [("intro", "Phrase d'accusé de réception", INTRO_AR), ("conditions", "Règlement", CONDITIONS_AR)]
    if type_document == T.BON_LIVRAISON:
        blocs.append(("signature", "Réception de la marchandise", SIGNATURE_DESTINATAIRE))
    if type_document == T.BON_PREPARATION:
        blocs.append(("prepare", "Préparé par / date", PREPARE_PAR))
    if type_document == T.FICHE_FABRICATION:
        blocs += [("resume", "Article et quantité", RESUME_OF), ("composants", "Nomenclature", COMPOSANTS), ("operations", "Gamme", OPERATIONS)]
    if type_document != T.FICHE_FABRICATION:
        blocs.append(("mentions", "Mentions légales", MENTIONS))
    blocs += [("pied", "Pied de page (répété sur chaque page)", PIED), ("saut", "Saut de page", SAUT_DE_PAGE)]
    if type_document != T.FICHE_FABRICATION:
        blocs.append(("filigrane", "Filigrane (PROVISOIRE / ANNULÉ)", FILIGRANE))
    return [{"id": i, "label": l, "contenu": c} for i, l, c in blocs]
