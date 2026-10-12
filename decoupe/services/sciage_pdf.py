"""Liste de débits d'une optimisation de sciage en PDF (WeasyPrint) : par section, le récapitulatif des débits avec leur croquis et
la synthèse, puis la liste des découpes barre par barre. En-tête et numéro de page répétés sur chaque page."""

from django.template.loader import render_to_string

from .sciage import calculer_plan, lignes_de, svg_barre


def contexte_liste_debits(optimisation):
    plan = calculer_plan(optimisation, lignes_de(optimisation))
    for s in plan.sections:
        # dessin plus étroit que l'écran, hachures propres au document
        s.groupes_pdf = [{**g, "svg": svg_barre(g["barre"], largeur=520, hauteur=22, hachure=f"pdf-h{s.section.pk}-{i}")} for i, g in enumerate(s.groupes)]
    return {"o": optimisation, "plan": plan}


def generer_pdf_liste_debits(optimisation):
    from weasyprint import HTML
    from weasyprint.urls import URLFetcher

    html = render_to_string("decoupe/sciage_liste_debits.html", contexte_liste_debits(optimisation))
    document = HTML(string=html, url_fetcher=URLFetcher(allowed_protocols={"data"})).render()
    document.metadata.title = f"Liste de débits {optimisation.numero}"
    return document.write_pdf()
