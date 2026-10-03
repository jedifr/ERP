"""Du modèle de document au PDF : choix du modèle actif, rendu, repli sur le PDF d'origine."""

import logging
from functools import wraps

from . import moteur
from .contextes import CONTEXTES
from .defaults import CSS_BASE
from .models import ModeleDocument

logger = logging.getLogger(__name__)


def rendre(html, css, contexte):
    """(document WeasyPrint, chemins inconnus) pour un modèle et des données."""
    corps, inconnues = moteur.appliquer(html, contexte)
    titre = contexte.get("document", {}).get("titre", "")
    return moteur.vers_pdf(corps, css, CSS_BASE, titre), inconnues


def pdf_depuis_modele(modele, objet):
    """PDF (octets) d'un objet avec le modèle donné. DocumentError si l'objet n'est pas imprimable."""
    contexte = CONTEXTES[modele.type_document](objet)
    document, _ = rendre(modele.html, modele.css, contexte)
    return moteur.ecrire([document])


def pdf_depuis_modele_multiple(modele, objets):
    """Un seul PDF pour plusieurs objets (une fiche par page)."""
    objets = list(objets)
    if not objets:
        from chiffrage.documents import DocumentError

        raise DocumentError("Aucun document à imprimer.")
    documents = [rendre(modele.html, modele.css, CONTEXTES[modele.type_document](o))[0] for o in objets]
    return moteur.ecrire(documents)


def personnalisable(type_document, multiple=False):
    """Décorateur des générateurs de PDF d'origine : si un modèle actif existe pour ce document, il est
    utilisé ; sinon (ou si le rendu échoue pour une raison technique) c'est le PDF d'origine qui sort,
    pour qu'un modèle cassé ne bloque jamais l'impression. Les refus « métier » (DocumentError : ligne sans
    prix…) passent tels quels."""

    def decorateur(fonction):
        @wraps(fonction)
        def enveloppe(objet):
            modele = ModeleDocument.actif_pour(type_document)
            if modele is None:
                return fonction(objet)
            from chiffrage.documents import DocumentError

            try:
                return (pdf_depuis_modele_multiple if multiple else pdf_depuis_modele)(modele, objet)
            except DocumentError:
                raise
            except Exception:
                logger.exception("Modèle de document « %s » inutilisable : PDF d'origine utilisé.", type_document)
                return fonction(objet)

        return enveloppe

    return decorateur
