"""Imports de tableaux du référentiel technique : articles, matières, nomenclatures (voir comptes/imports.py). Un doublon est
ignoré, jamais écrasé."""

from comptes.imports import ChampImport, ImportTableau, LigneIgnoree, choix, enregistrer, nombre, oui_non

from .models import Article, FamilleMatiere, Matiere, Nomenclature

OUI_NON = [("oui", "Oui"), ("non", "Non")]


def trouver_article(valeur):
    article = Article.objects.filter(pk=valeur).first()
    if article is None:
        raise LigneIgnoree(f"article « {valeur} » introuvable")
    return article


# ---------------------------------------------------------------- articles

def creer_article(v, journal):
    if Article.objects.filter(pk=v["reference"]).exists():
        raise LigneIgnoree("référence déjà prise")
    nature = choix(v.get("nature"), Article.Nature.choices)
    if not nature:
        raise LigneIgnoree("nature manquante : choisissez une colonne ou une valeur pour toutes les lignes")
    matiere = None
    if v.get("matiere"):
        matiere = Matiere.objects.filter(pk__iexact=v["matiere"]).first()
        if matiere is None:
            raise LigneIgnoree(f"matière « {v['matiere']} » inconnue")
    article = Article(
        reference=v["reference"], libelle=v.get("libelle", ""), nature=nature, matiere=matiere,
        unite_cout=choix(v.get("unite_cout"), Article.UniteCout.choices + [("m2", "m²"), ("m", "m"), ("kg", "kg"), ("u", "u"), ("piece", "pièce")]),
        epaisseur=nombre(v.get("epaisseur")), cout_unitaire=nombre(v.get("cout_unitaire"), decimal=True),
        taux_marge_defaut=nombre(v.get("taux_marge_defaut"), decimal=True), gere_en_stock=oui_non(v.get("gere_en_stock")),
        stock_mini=nombre(v.get("stock_mini")), quantite_reappro=nombre(v.get("quantite_reappro")),
    )
    article.unite_cout = {"m2": "surface", "m": "longueur", "kg": "poids", "u": "piece"}.get(article.unite_cout, article.unite_cout)
    return enregistrer(article, journal)


IMPORT_ARTICLES = ImportTableau(
    cle="articles", titre="Importer des articles", modele=Article, creer=creer_article,
    aide="Nature : matière première, fabriqué, composant, consommable ou service acheté (une valeur peut être imposée à tout le "
         "fichier). Unité de coût : m², m, kg ou pièce. Une référence déjà prise est ignorée.",
    exemple="reference;libelle;nature;matiere;epaisseur;unite_cout;cout_unitaire\nTOLE-S235-3;Tôle S235 3 mm;matière première;S235;3;kg;0,95",
    champs=[
        ChampImport("reference", "Référence", obligatoire=True, synonymes=("ref", "code", "code article", "article", "reference article",
                    "n article", "part number", "pn")),
        ChampImport("libelle", "Libellé", synonymes=("designation", "description", "nom", "intitule", "libelle article")),
        ChampImport("nature", "Nature", synonymes=("type", "type article", "famille", "categorie"), choix=Article.Nature.choices),
        ChampImport("matiere", "Matière", synonymes=("nuance", "materiau", "matiere premiere", "material")),
        ChampImport("epaisseur", "Épaisseur", synonymes=("ep", "epaisseur mm", "ep mm", "thickness")),
        ChampImport("unite_cout", "Unité de coût", synonymes=("unite", "unite de prix", "unite achat", "u")),
        ChampImport("cout_unitaire", "Coût unitaire", synonymes=("cout", "prix", "prix achat", "prix d achat", "prix unitaire", "pu", "cout achat")),
        ChampImport("taux_marge_defaut", "Taux de marge (%)", synonymes=("marge", "taux de marge", "coefficient de marge")),
        ChampImport("gere_en_stock", "Géré en stock", synonymes=("stock", "stocke", "en stock"), choix=OUI_NON),
        ChampImport("stock_mini", "Stock minimum", synonymes=("stock mini", "seuil", "seuil d alerte", "mini")),
        ChampImport("quantite_reappro", "Quantité de réapprovisionnement", synonymes=("qte reappro", "reappro", "quantite a commander", "lot d achat")),
    ],
)


# ---------------------------------------------------------------- matières

def creer_matiere(v, journal):
    if Matiere.objects.filter(pk__iexact=v["nom"]).exists():
        raise LigneIgnoree("matière déjà présente")
    famille = None
    if v.get("famille"):
        famille = FamilleMatiere.objects.filter(nom__iexact=v["famille"]).first()
        if famille is None:
            raise LigneIgnoree(f"famille « {v['famille']} » inconnue")
    matiere = Matiere(nom=v["nom"], densite=nombre(v["densite"]), usinabilite=nombre(v.get("usinabilite")), famille=famille)
    return enregistrer(matiere, journal)


IMPORT_MATIERES = ImportTableau(
    cle="matieres", titre="Importer des matières", modele=Matiere, creer=creer_matiere,
    aide="Densité en kg/dm³. Sans famille, la matière est rattachée d'après son nom (S235 → Acier…).",
    exemple="nom;densite;famille\nS355;7,85;Acier\n5754;2,66;Aluminium",
    champs=[
        ChampImport("nom", "Nom", obligatoire=True, synonymes=("nuance", "matiere", "designation", "materiau", "grade")),
        ChampImport("densite", "Densité", obligatoire=True, synonymes=("masse volumique", "kg dm3", "density")),
        ChampImport("usinabilite", "Usinabilité", synonymes=("indice usinabilite", "machinability")),
        ChampImport("famille", "Famille", synonymes=("famille de matiere", "matiere generique", "type")),
    ],
)


# ---------------------------------------------------------------- nomenclatures

def creer_ligne_nomenclature(v, journal):
    parent, composant = trouver_article(v["article_parent"]), trouver_article(v["article_composant"])
    if Nomenclature.objects.filter(article_parent=parent, article_composant=composant).exists():
        raise LigneIgnoree("ce composant est déjà dans la nomenclature")
    quantite = nombre(v["quantite"])
    return enregistrer(Nomenclature(article_parent=parent, article_composant=composant, quantite=quantite,
                                    longueur_mm=nombre(v.get("longueur_mm")), largeur_mm=nombre(v.get("largeur_mm"))), journal)


IMPORT_NOMENCLATURES = ImportTableau(
    cle="nomenclatures", titre="Importer des lignes de nomenclature", modele=Nomenclature, creer=creer_ligne_nomenclature,
    aide="Une ligne par composant : référence de l'article fabriqué, référence du composant, quantité (longueur et largeur en mm pour une tôle ou un profilé).",
    exemple="parent;composant;quantite;longueur;largeur\nCHASSIS-01;TOLE-S235-3;2;500;300",
    champs=[
        ChampImport("article_parent", "Article parent", obligatoire=True, synonymes=("parent", "article fabrique", "ensemble",
                    "assemblage", "reference parent", "ref parent", "produit")),
        ChampImport("article_composant", "Article composant", obligatoire=True, synonymes=("composant", "reference composant",
                    "ref composant", "piece", "sous ensemble", "matiere", "article")),
        ChampImport("quantite", "Quantité", obligatoire=True, synonymes=("qte", "qt", "nombre", "nb", "quantity")),
        ChampImport("longueur_mm", "Longueur (mm)", synonymes=("longueur", "long", "l")),
        ChampImport("largeur_mm", "Largeur (mm)", synonymes=("largeur", "larg")),
    ],
)
