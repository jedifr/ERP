"""Moteur de chiffrage.

Implémente le pipeline décrit dans le cahier des charges (Phase 2) :
ligne de devis -> coût matière (article + composants via nomenclature)
-> coût des opérations (somme des étapes de gamme) -> marge -> prix de vente.
"""

from django.db.models import Q

from technique.models import Article, Gamme, PosteTravail, TarifPoste

from .models import DevisLigne, DevisLigneOperation


class ChiffrageError(Exception):
    """Donnée de référence manquante ou incohérente empêchant le calcul."""


def _valide_a_la_date(date_reference):
    """Filtre Q pour une ligne historisée (date_debut/date_fin) active à `date_reference`."""
    return Q(date_fin__isnull=True) | Q(date_fin__gte=date_reference)


def tarif_poste_valide(poste, date_reference):
    tarif = (
        TarifPoste.objects.filter(poste=poste, date_debut__lte=date_reference)
        .filter(_valide_a_la_date(date_reference))
        .order_by("-date_debut")
        .first()
    )
    if tarif is None:
        raise ChiffrageError(f"Aucun tarif valide pour le poste « {poste} » à la date {date_reference}.")
    return tarif


def gamme_active(article, date_reference):
    return (
        Gamme.objects.filter(article=article, date_debut__lte=date_reference)
        .filter(_valide_a_la_date(date_reference))
        .order_by("ordre")
    )


def cout_etape_gamme(etape, quantite, date_reference):
    if etape.poste.mode_calcul == PosteTravail.ModeCalcul.HORAIRE:
        tarif = tarif_poste_valide(etape.poste, date_reference)
        temps = (etape.temps_fixe or 0) + (etape.temps_variable or 0) * quantite
        return temps * tarif.cout_horaire
    return (etape.cout_forfaitaire or 0) * quantite


def cout_composant(nomenclature_ligne):
    """Coût d'un composant de nomenclature, pour UNE unité de l'article parent."""
    article = nomenclature_ligne.article_composant
    quantite = nomenclature_ligne.quantite
    if article.cout_unitaire is None:
        raise ChiffrageError(f"L'article « {article} » n'a pas de coût unitaire renseigné.")

    if article.unite_cout == Article.UniteCout.PIECE:
        return quantite * article.cout_unitaire

    if article.unite_cout == Article.UniteCout.SURFACE:
        if not nomenclature_ligne.longueur_mm or not nomenclature_ligne.largeur_mm:
            raise ChiffrageError(
                f"Longueur/largeur manquantes pour le composant « {article} » (unité : surface)."
            )
        surface_m2 = (nomenclature_ligne.longueur_mm * nomenclature_ligne.largeur_mm) / 1_000_000
        return surface_m2 * article.cout_unitaire * quantite

    if article.unite_cout == Article.UniteCout.LONGUEUR:
        if not nomenclature_ligne.longueur_mm:
            raise ChiffrageError(f"Longueur manquante pour le composant « {article} » (unité : longueur).")
        longueur_m = nomenclature_ligne.longueur_mm / 1000
        if article.poids_lineique:
            poids = longueur_m * article.poids_lineique
            return poids * article.cout_unitaire * quantite
        return longueur_m * article.cout_unitaire * quantite

    if article.unite_cout == Article.UniteCout.POIDS:
        if not nomenclature_ligne.longueur_mm or not nomenclature_ligne.largeur_mm or not article.epaisseur:
            raise ChiffrageError(
                f"Longueur/largeur/épaisseur manquantes pour le composant « {article} » (unité : poids)."
            )
        if not article.matiere_id:
            raise ChiffrageError(f"Matière manquante pour le composant « {article} » (unité : poids).")
        volume_dm3 = (
            nomenclature_ligne.longueur_mm * nomenclature_ligne.largeur_mm * article.epaisseur
        ) / 1_000_000
        poids = volume_dm3 * article.matiere.densite
        return poids * article.cout_unitaire * quantite

    raise ChiffrageError(f"Unité de coût non définie pour l'article « {article} ».")


def cout_matiere_article(article, quantite):
    """Coût matière pour `quantite` unités de `article` (matière première ou fabriqué)."""
    if article.nature == Article.Nature.MATIERE_PREMIERE:
        if article.cout_unitaire is None:
            raise ChiffrageError(f"L'article « {article} » n'a pas de coût unitaire renseigné.")
        return quantite * article.cout_unitaire

    cout_par_unite = sum(cout_composant(n) for n in article.composants.select_related("article_composant"))
    return cout_par_unite * quantite


def _taux_marge_matiere(devis, ligne):
    if devis.taux_marge_globale is not None:
        return devis.taux_marge_globale
    if ligne.taux_marge_matiere_applique is not None:
        return ligne.taux_marge_matiere_applique
    return ligne.article.taux_marge_defaut or 0


def _taux_marge_operation(devis, poste, operation_existante):
    if devis.taux_marge_globale is not None:
        return devis.taux_marge_globale
    if operation_existante is not None and operation_existante.taux_marge_applique is not None:
        return operation_existante.taux_marge_applique
    return poste.taux_marge_defaut or 0


def _synchroniser_operations_ligne(devis, ligne):
    etapes = list(gamme_active(ligne.article, devis.date_creation))
    ordres_actifs = {etape.ordre for etape in etapes}

    existantes = {op.ordre: op for op in ligne.operations.all()}
    for ordre, operation in existantes.items():
        if ordre not in ordres_actifs:
            operation.delete()

    for etape in etapes:
        operation = existantes.get(etape.ordre)
        if operation is None:
            operation = DevisLigneOperation(devis_ligne=ligne, poste=etape.poste, ordre=etape.ordre)

        operation.poste = etape.poste
        cout = cout_etape_gamme(etape, ligne.quantite, devis.date_creation)
        taux = _taux_marge_operation(devis, etape.poste, operation if operation.pk else None)
        operation.cout_calcule = cout
        operation.taux_marge_applique = taux
        operation.prix_vente = cout * (1 + taux / 100)
        operation.save()


def calculer_devis(devis):
    """Recalcule le coût matière et les opérations de toutes les lignes d'un devis."""
    for ligne in devis.lignes.select_related("article", "article__matiere").all():
        ligne.cout_matiere_calcule = cout_matiere_article(ligne.article, ligne.quantite)
        taux = _taux_marge_matiere(devis, ligne)
        ligne.taux_marge_matiere_applique = taux
        if ligne.prix_vente_unitaire_force is not None:
            ligne.prix_vente_matiere = ligne.prix_vente_unitaire_force * ligne.quantite
        else:
            ligne.prix_vente_matiere = ligne.cout_matiere_calcule * (1 + taux / 100)
        ligne.save()

        if ligne.article.nature == Article.Nature.FABRIQUE:
            _synchroniser_operations_ligne(devis, ligne)
        else:
            ligne.operations.all().delete()


def previsualiser_ligne(
    devis,
    article,
    quantite,
    taux_marge_matiere_applique=None,
    prix_vente_unitaire_force=None,
    taux_tva=None,
):
    """Aperçu du coût/prix d'une ligne pas encore enregistrée (ligne tout juste
    ajoutée dans l'inline de la fiche Devis, ou dans le constructeur), en
    réutilisant exactement les mêmes règles que calculer_devis — sans rien
    persister en base (ni DevisLigne, ni DevisLigneOperation)."""
    ligne_apercu = DevisLigne(
        devis=devis,
        article=article,
        quantite=quantite,
        taux_marge_matiere_applique=taux_marge_matiere_applique,
    )
    cout_matiere = cout_matiere_article(article, quantite)
    taux = _taux_marge_matiere(devis, ligne_apercu)

    if prix_vente_unitaire_force is not None:
        prix_vente_matiere = prix_vente_unitaire_force * quantite
    else:
        prix_vente_matiere = cout_matiere * (1 + taux / 100)

    prix_vente_operations = 0
    if article.nature == Article.Nature.FABRIQUE:
        for etape in gamme_active(article, devis.date_creation):
            cout_etape = cout_etape_gamme(etape, quantite, devis.date_creation)
            taux_operation = _taux_marge_operation(devis, etape.poste, None)
            prix_vente_operations += cout_etape * (1 + taux_operation / 100)

    prix_vente_total = prix_vente_matiere + prix_vente_operations
    taux_tva_valeur = taux_tva.taux if taux_tva is not None else 0

    return {
        "cout_matiere_calcule": cout_matiere,
        "taux_marge_matiere_applique": taux,
        "prix_vente_matiere": prix_vente_matiere,
        "prix_vente_operations": prix_vente_operations,
        "prix_vente_total": prix_vente_total,
        "prix_vente_ttc": prix_vente_total * (1 + taux_tva_valeur / 100),
    }
