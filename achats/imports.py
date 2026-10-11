"""Import de tarifs fournisseurs (voir comptes/imports.py) : une ligne par article et fournisseur, avec son prix.

Le lien article ↔ fournisseur est créé s'il manque. Un tarif en cours qui commence avant la date d'effet est clos la veille
(c'est une hausse ou une baisse) ; ces clôtures font partie du lot et sont rétablies si on l'annule."""

import datetime

from comptes.imports import ChampImport, ImportTableau, LigneIgnoree, date, enregistrer, nombre
from commercial.imports import trouver_tiers
from technique.imports import trouver_article

from .models import ArticleFournisseur, TarifAchatArticle


def creer_tarif(v, journal):
    article, fournisseur = trouver_article(v["article"]), trouver_tiers(v["fournisseur"])
    if article.nature == article.Nature.FABRIQUE:
        raise LigneIgnoree("un article fabriqué n'a pas de fournisseur")
    lien = ArticleFournisseur.objects.filter(article=article, fournisseur=fournisseur).first()
    if lien is None:
        lien = enregistrer(ArticleFournisseur(article=article, fournisseur=fournisseur, reference_fournisseur=v.get("reference_fournisseur", ""),
                                              designation_fournisseur=v.get("designation_fournisseur", "")), journal)
    prix = nombre(v.get("prix_unitaire"), decimal=True)
    if prix is None:
        if not journal.crees:
            raise LigneIgnoree("ce fournisseur est déjà rattaché à l'article et la ligne n'a pas de prix")
        return lien
    debut = date(v.get("date_debut")) or datetime.date.today()
    if lien.tarifs.filter(date_debut__gte=debut).exists():
        raise LigneIgnoree(f"un tarif commence déjà le {debut:%d/%m/%Y} ou après")
    en_cours = lien.tarifs.filter(date_fin__isnull=True, date_debut__lt=debut).first()
    if en_cours is not None:
        en_cours.date_fin = debut - datetime.timedelta(days=1)
        en_cours.save(update_fields=["date_fin"])
        journal.modifie(en_cours, {"date_fin": None}, {"date_fin": en_cours.date_fin.isoformat()})
    tarif = TarifAchatArticle(article_fournisseur=lien, prix_unitaire=prix, frais_port=nombre(v.get("frais_port"), decimal=True), date_debut=debut)
    return enregistrer(tarif, journal)


IMPORT_TARIFS = ImportTableau(
    cle="tarifs_fournisseurs", titre="Importer des tarifs fournisseurs", modele=TarifAchatArticle, creer=creer_tarif,
    aide="L'article se désigne par sa référence, le fournisseur par son code ou sa raison sociale. Date d'effet : aujourd'hui si vide ; "
         "le tarif en cours est clos la veille.",
    exemple="article;fournisseur;ref_fournisseur;prix;date\nTOLE-S235-3;FOU-007;S235-3000x1500-3;0,98;01/11/2026",
    champs=[
        ChampImport("article", "Article", obligatoire=True, synonymes=("reference", "ref", "code article", "reference interne", "ref interne")),
        ChampImport("fournisseur", "Fournisseur", obligatoire=True, synonymes=("code fournisseur", "fournisseur code", "raison sociale", "societe")),
        ChampImport("reference_fournisseur", "Référence fournisseur", synonymes=("ref fournisseur", "reference chez le fournisseur",
                    "ref frn", "code fournisseur article", "reference catalogue", "ref catalogue")),
        ChampImport("designation_fournisseur", "Désignation fournisseur", synonymes=("designation", "libelle", "libelle fournisseur", "description")),
        ChampImport("prix_unitaire", "Prix unitaire", synonymes=("prix", "pu", "prix achat", "prix d achat", "tarif", "prix net", "cout")),
        ChampImport("frais_port", "Frais de port", synonymes=("port", "transport", "frais de livraison")),
        ChampImport("date_debut", "Date d'effet", synonymes=("date", "date de debut", "debut", "a partir du", "valable du", "date tarif"),
                    motif=r"\d{2}/\d{2}/\d{2,4}|\d{4}-\d{2}-\d{2}"),
    ],
)
