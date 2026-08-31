"""Duplication d'un article (matière première ou fabriqué).

Pour un article fabriqué, la nomenclature (composants) et la gamme (étapes)
sont dupliquées avec lui. Le stock (lots/mouvements) ne l'est jamais — la
copie démarre à zéro. Action "Dupliquer et modifier" de l'admin Article.
"""

from django.db import transaction

from .models import Article, Gamme, Nomenclature


class DuplicationError(Exception):
    """Donnée invalide empêchant la duplication."""


def erreur_lisible(exc):
    message_dict = getattr(exc, "message_dict", None)
    if message_dict:
        return " ; ".join(f"{champ} : {', '.join(msgs)}" for champ, msgs in message_dict.items())
    messages = getattr(exc, "messages", None)
    if messages:
        return " ; ".join(messages)
    return str(exc)


def _valider_et_sauver(instance):
    try:
        instance.full_clean()
    except Exception as exc:  # ValidationError Django
        raise DuplicationError(erreur_lisible(exc)) from exc
    instance.save()
    return instance


def _reference_copie(reference_source):
    base = f"{reference_source}-COPIE"
    if not Article.objects.filter(pk=base).exists():
        return base
    n = 2
    while Article.objects.filter(pk=f"{base}-{n}").exists():
        n += 1
    return f"{base}-{n}"


@transaction.atomic
def dupliquer_article(article):
    """Crée une copie de `article` (nouvelle référence générée automatiquement),
    avec sa nomenclature et sa gamme s'il est fabriqué. Retourne la copie."""
    copie = Article(
        reference=_reference_copie(article.reference),
        nature=article.nature,
        matiere=article.matiere,
        unite_cout=article.unite_cout,
        epaisseur=article.epaisseur,
        type_profil=article.type_profil,
        poids_lineique=article.poids_lineique,
        cout_unitaire=article.cout_unitaire,
        taux_marge_defaut=article.taux_marge_defaut,
        gere_en_stock=article.gere_en_stock,
        stock_mini=article.stock_mini,
        quantite_reappro=article.quantite_reappro,
    )
    _valider_et_sauver(copie)

    for ligne in article.composants.all():
        _valider_et_sauver(
            Nomenclature(
                article_parent=copie,
                article_composant=ligne.article_composant,
                longueur_mm=ligne.longueur_mm,
                largeur_mm=ligne.largeur_mm,
                quantite=ligne.quantite,
            )
        )

    for etape in article.gamme_etapes.all():
        _valider_et_sauver(
            Gamme(
                article=copie,
                poste=etape.poste,
                ordre=etape.ordre,
                temps_fixe=etape.temps_fixe,
                temps_variable=etape.temps_variable,
                cout_forfaitaire=etape.cout_forfaitaire,
                date_debut=etape.date_debut,
                date_fin=etape.date_fin,
            )
        )

    return copie
