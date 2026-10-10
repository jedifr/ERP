"""Champs modifiables par lots des articles, matières, postes, formats et gammes (voir comptes/lots.py)."""

from decimal import ROUND_HALF_UP, Decimal

from comptes.lots import ChampLot, Inapplicable, _arrondi, _nombre

from .models import Article


def _kg_par_m2(article):
    """Poids d'un m² de la tôle (kg) : épaisseur (mm) × densité (kg/dm³)."""
    if not article.epaisseur or not article.matiere_id:
        raise Inapplicable("épaisseur et matière nécessaires pour convertir entre €/kg et €/m²")
    return Decimal(str(article.epaisseur)) * Decimal(str(article.matiere.densite))


def _prix_dans(article, unite):
    """Prix actuel de l'article exprimé en `unite` (« natif », « kg » ou « m2 »)."""
    ancien = article.cout_unitaire
    if ancien is None:
        return None
    ancien = Decimal(str(ancien))
    natif = article.unite_cout
    if unite in (None, "", "natif"):
        return ancien
    if unite == "kg":
        if natif == Article.UniteCout.POIDS:
            return ancien
        if natif == Article.UniteCout.SURFACE:
            return ancien / _kg_par_m2(article)
    if unite == "m2":
        if natif == Article.UniteCout.SURFACE:
            return ancien
        if natif == Article.UniteCout.POIDS:
            return ancien * _kg_par_m2(article)
    raise Inapplicable(f"prix exprimé {article.get_unite_cout_display() if natif else 'sans unité'} : conversion impossible")


def calcul_prix(objet, valeur, mode, unite):
    """Prix d'achat : remplacer, augmenter de x % ou ajouter x, saisi dans l'unité de l'article, en €/kg ou en €/m² (converti avec
    l'épaisseur et la densité) puis ramené dans l'unité de l'article."""
    if objet.nature == Article.Nature.FABRIQUE:
        raise Inapplicable("article fabriqué : son coût est calculé")
    v = _nombre(valeur)
    ancien = _prix_dans(objet, unite)
    if mode != "fixe" and ancien is None:
        raise Inapplicable("pas de prix actuel")
    nouveau = v if mode == "fixe" else ancien * (1 + v / 100) if mode == "pct" else ancien + v
    if unite in (None, "", "natif"):
        resultat = nouveau
    elif unite == "kg":
        resultat = nouveau if objet.unite_cout == Article.UniteCout.POIDS else nouveau * _kg_par_m2(objet)
    else:
        resultat = nouveau if objet.unite_cout == Article.UniteCout.SURFACE else nouveau / _kg_par_m2(objet)
    if objet.unite_cout is None and unite not in (None, "", "natif"):
        raise Inapplicable("unité de coût de l'article non définie")
    return {"cout_unitaire": _arrondi(Article._meta.get_field("cout_unitaire"), resultat)}


def calcul_unite(objet, valeur, mode, unite):
    """Change l'unité de coût en conservant le prix : €/kg ↔ €/m² (converti avec l'épaisseur et la densité)."""
    if objet.nature == Article.Nature.FABRIQUE:
        raise Inapplicable("article fabriqué : pas d'unité de coût")
    cible = valeur
    if objet.unite_cout == cible:
        return {}
    if objet.cout_unitaire is None:
        return {"unite_cout": cible}
    poids, surface = Article.UniteCout.POIDS, Article.UniteCout.SURFACE
    if {objet.unite_cout, cible} != {poids, surface}:
        raise Inapplicable("conversion possible seulement entre €/kg et €/m²")
    facteur = _kg_par_m2(objet)
    ancien = Decimal(str(objet.cout_unitaire))
    nouveau = ancien * facteur if (objet.unite_cout, cible) == (poids, surface) else ancien / facteur
    return {"unite_cout": cible, "cout_unitaire": _arrondi(Article._meta.get_field("cout_unitaire"), nouveau)}


def champs_article():
    from commercial.models import TauxTVA

    return [
        ChampLot("cout_unitaire", "Prix d'achat", genre="nombre", calcul=calcul_prix, aide="Saisi dans l'unité de l'article, ou en €/kg ou €/m² (converti avec l'épaisseur et la densité de la tôle).",
                 unites=[("natif", "unité de l'article"), ("kg", "€/kg"), ("m2", "€/m²")]),
        ChampLot("unite_cout", "Unité de coût", genre="choix", calcul=calcul_unite, choix=lambda: Article.UniteCout.choices,
                 aide="Convertit aussi le prix pour garder la même valeur (€/kg ↔ €/m²)."),
        ChampLot("taux_marge_defaut", "Taux de marge par défaut (%)"),
        ChampLot("taux_tva", "Taux de TVA", genre="fk", queryset=lambda: TauxTVA.objects.order_by("taux"), vide_permis=True),
        ChampLot("gere_en_stock", "Géré en stock", genre="tristate", choix=[("oui", "Oui"), ("non", "Non"), ("defaut", "Défaut de la nature")], modes=("fixe",)),
        ChampLot("stock_mini", "Stock minimum"),
        ChampLot("quantite_reappro", "Quantité de réapprovisionnement"),
    ]


def champs_matiere():
    from .models import FamilleMatiere

    return [
        ChampLot("densite", "Densité (kg/dm³)"),
        ChampLot("usinabilite", "Usinabilité"),
        ChampLot("famille", "Famille de matière", genre="fk", queryset=lambda: FamilleMatiere.objects.all(), vide_permis=True),
    ]


def champs_poste():
    return [
        ChampLot("taux_marge_defaut", "Taux de marge par défaut (%)"),
        ChampLot("temps_mise_en_place_min", "Mise en place d'une tôle (min)"),
        ChampLot("nombre_machines", "Nombre de machines", modes=("fixe", "ajout")),
    ]


def champs_tarif_poste():
    return [ChampLot("cout_horaire", "Coût horaire (€/h)", aide="Remplacer, augmenter de x % (hausse tarifaire) ou ajouter x €.")]


def champs_gamme():
    from .models import PosteTravail

    return [
        ChampLot("poste", "Poste de travail", genre="fk", queryset=lambda: PosteTravail.objects.order_by("nom")),
        ChampLot("temps_fixe", "Temps de réglage (min)"),
        ChampLot("temps_variable", "Temps par pièce (min)"),
        ChampLot("cout_forfaitaire", "Coût forfaitaire par pièce (€)", aide="Postes forfaitaires (sous-traitance)."),
    ]
