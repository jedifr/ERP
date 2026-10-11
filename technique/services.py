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
        libelle=article.libelle,
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


@transaction.atomic
def renommer_article(article, nouvelle_reference):
    """Change la référence d'un article (clé primaire) en reportant le changement sur tout ce qui s'y rattache : nomenclatures, gammes,
    lignes de devis et de commande, ordres de fabrication, stock, fournisseurs, comptes d'article, pièces à découper… et sur l'historique
    de ces objets. L'article est recréé sous sa nouvelle référence, les liens sont repointés, puis l'ancien est supprimé. Retourne
    le nouvel article."""
    nouvelle = (nouvelle_reference or "").strip()
    if not nouvelle:
        raise DuplicationError("Indiquez la nouvelle référence.")
    if nouvelle == article.reference:
        raise DuplicationError("La nouvelle référence est identique à l'ancienne.")
    if len(nouvelle) > Article._meta.pk.max_length:
        raise DuplicationError(f"La référence ne peut dépasser {Article._meta.pk.max_length} caractères.")
    if Article.objects.filter(pk=nouvelle).exists():
        raise DuplicationError(f"La référence « {nouvelle} » existe déjà.")
    ancienne = article.reference
    valeurs = {f.attname: getattr(article, f.attname) for f in Article._meta.concrete_fields}
    valeurs["reference"] = nouvelle
    nouveau = Article(**valeurs)
    _valider_et_sauver(nouveau)
    for lien in Article._meta.related_objects:
        champ = lien.field
        modele = lien.related_model
        modele.objects.filter(**{champ.name: ancienne}).update(**{champ.attname: nouvelle})
        historique = getattr(modele, "history", None)
        if historique is not None:  # simple_history garde la clé étrangère comme une simple colonne
            historique.model.objects.filter(**{champ.attname: ancienne}).update(**{champ.attname: nouvelle})
    Article.objects.filter(pk=ancienne).delete()
    return nouveau
