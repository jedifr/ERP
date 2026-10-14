"""Moteur de chiffrage.

Implémente le pipeline décrit dans le cahier des charges (Phase 2) :
ligne de devis -> coût matière (article + composants via nomenclature)
-> coût des opérations (somme des étapes de gamme) -> marge -> prix de vente.
"""

from django.db.models import Q

from comptes.montants import D, D0, ZERO, arrondir, arrondir_prix, arrondir_prix_vente
from commercial.models import TauxTVA, Tiers
from technique.models import Article, Gamme, PosteTravail, TarifPoste

from .models import DevisLigne, DevisLigneOperation


class ChiffrageError(Exception):
    """Donnée de référence manquante ou incohérente empêchant le calcul. `lien` : (adresse, texte) de la page où la corriger,
    affiché à la suite du message (voir chiffrage.admin.message_erreur)."""

    def __init__(self, message="", lien=None):
        super().__init__(message)
        self.lien = lien


def lien_article(article):
    """Lien vers la fiche de l'article dont une donnée manque."""
    from django.urls import reverse

    return reverse("admin:technique_article_change", args=[article.pk]), "Renseigner le coût de l'article"


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
        # temps_fixe/temps_variable (Gamme) sont exprimés en MINUTES, alors que
        # TarifPoste.cout_horaire est un €/HEURE : diviser par 60 avant de les
        # multiplier est indispensable, sous peine de gonfler le coût x60.
        # Étape de découpe : pas de réglage « par lot » ; la mise en place se compte par tôle (chiffrage/reglage.py).
        reglage = 0 if getattr(etape, "origine", "") == "decoupe" else (etape.temps_fixe or 0)
        temps_minutes = D(reglage + (etape.temps_variable or 0) * quantite)
        return arrondir_prix(temps_minutes / 60 * D(tarif.cout_horaire))
    return arrondir_prix(D0(etape.cout_forfaitaire) * D(quantite))


def cout_sous_ensemble(article, quantite, date_reference, devis=None, chemin=()):
    """Coût de revient de `quantite` unités d'un article FABRIQUE utilisé comme composant d'un autre : sa matière (nomenclature,
    récursivement) + ses opérations au coût (gamme active à `date_reference`), sans marge — la marge s'applique sur l'article vendu."""
    if article.pk in chemin:
        raise ChiffrageError(f"Nomenclature en boucle : « {article} » se contient lui-même (directement ou via un sous-ensemble).")
    total = cout_matiere_article(article, quantite, devis=devis, date_reference=date_reference, chemin=chemin + (article.pk,))
    for etape in gamme_active(article, date_reference):
        total += cout_etape_gamme(etape, quantite, date_reference)
    return arrondir_prix(total)


def cout_composant(nomenclature_ligne, quantite_parent=1, date_reference=None, devis=None, chemin=()):
    """Coût d'un composant de nomenclature, pour UNE unité de l'article parent. Un composant lui-même fabriqué (sous-ensemble) est
    valorisé à son coût de revient pour les `quantite_parent` pièces (réglages répartis sur le lot)."""
    article = nomenclature_ligne.article_composant
    quantite = D(nomenclature_ligne.quantite)
    if article.nature == Article.Nature.FABRIQUE:
        import datetime

        quantite_parent = quantite_parent or 1
        date_reference = date_reference or datetime.date.today()
        total = cout_sous_ensemble(article, float(quantite * D(quantite_parent)), date_reference, devis=devis, chemin=chemin)
        return total / D(quantite_parent)
    cout_unitaire = D(article.cout_unitaire)
    if article.cout_unitaire is None:
        raise ChiffrageError(f"L'article « {article} » n'a pas de coût unitaire renseigné.", lien=lien_article(article))

    if article.unite_cout == Article.UniteCout.PIECE:
        return quantite * cout_unitaire

    if article.unite_cout == Article.UniteCout.SURFACE:
        if not nomenclature_ligne.longueur_mm or not nomenclature_ligne.largeur_mm:
            raise ChiffrageError(
                f"Longueur/largeur manquantes pour le composant « {article} » (unité : surface)."
            )
        surface_m2 = D((nomenclature_ligne.longueur_mm * nomenclature_ligne.largeur_mm) / 1_000_000)
        return surface_m2 * cout_unitaire * quantite

    if article.unite_cout == Article.UniteCout.LONGUEUR:
        if not nomenclature_ligne.longueur_mm:
            raise ChiffrageError(f"Longueur manquante pour le composant « {article} » (unité : longueur).")
        longueur_m = D(nomenclature_ligne.longueur_mm / 1000)
        if article.poids_lineique:
            poids = longueur_m * D(article.poids_lineique)
            return poids * cout_unitaire * quantite
        return longueur_m * cout_unitaire * quantite

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
        poids = D(volume_dm3 * article.matiere.densite)
        return poids * cout_unitaire * quantite

    raise ChiffrageError(f"Unité de coût non définie pour l'article « {article} ».")


def cout_matiere_article(article, quantite, devis=None, date_reference=None, chemin=()):
    """Coût matière pour `quantite` unités de `article`. Seul un article FABRIQUE
    est décomposé via sa nomenclature ; toute autre nature — matière première,
    ou achetée telle quelle (service acheté, consommable, composant) — est
    costée directement depuis son cout_unitaire. Dans un devis, un article créé depuis une pièce importée du devis est
    chiffré avec l'imbrication retenue de son groupe de pièces (matière répartie entre les pièces)."""
    if article.nature == Article.Nature.FABRIQUE:
        import datetime

        from decoupe.models import PieceDecoupe

        date_ref = date_reference or (devis.date_creation if devis is not None else None) or datetime.date.today()
        from decoupe.services.imbrication_devis import cout_matiere_piece_devis
        from decoupe.services.matiere import ErreurMatiere, cout_matiere_imbrication, piece_de_chiffrage

        if devis is not None:
            from decoupe.models import PieceProfile
            from decoupe.services.profiles import ErreurProfile, ErreurPrixProfile, cout_matiere_piece_profile

            debit = PieceProfile.objects.filter(article=article, devis=devis).select_related("section", "section__article").first()
            if debit is not None:
                try:
                    return arrondir_prix(cout_matiere_piece_profile(debit, max(1, round(quantite))))
                except (ErreurProfile, ErreurPrixProfile) as exc:
                    raise ChiffrageError(f"Article « {article} » : matière du profilé impossible — {exc}") from exc
            piece_devis = PieceDecoupe.objects.filter(article=article, devis=devis, tole__isnull=False, format_tole__isnull=False).first()
            if piece_devis is not None:
                composants = article.composants.select_related("article_composant").exclude(article_composant=piece_devis.tole)
                cout_par_unite = sum((cout_composant(n, quantite, date_ref, devis, chemin) for n in composants), ZERO)
                try:
                    matiere = cout_matiere_piece_devis(piece_devis, max(1, round(quantite)))
                except ErreurMatiere as exc:
                    raise ChiffrageError(f"Article « {article} » : matière par imbrication impossible — {exc}") from exc
                return arrondir_prix(cout_par_unite * D(quantite) + matiere)
        piece = piece_de_chiffrage(article)
        composants = article.composants.select_related("article_composant")
        if piece is not None:
            # La tôle de la pièce est chiffrée par imbrication (surface consommée avec les chutes, selon la quantité) :
            # sa ligne de nomenclature (rectangle) n'est pas comptée une seconde fois.
            composants = composants.exclude(article_composant=piece.tole)
        cout_par_unite = sum((cout_composant(n, quantite, date_ref, devis, chemin) for n in composants), ZERO)
        total = cout_par_unite * D(quantite)
        if piece is not None:
            try:
                total += cout_matiere_imbrication(piece, max(1, round(quantite))).cout_total
            except ErreurMatiere as exc:
                raise ChiffrageError(f"Article « {article} » : matière par imbrication impossible — {exc}") from exc
        return arrondir_prix(total)

    if article.cout_unitaire is None:
        raise ChiffrageError(f"L'article « {article} » n'a pas de coût unitaire renseigné.", lien=lien_article(article))
    return arrondir_prix(D(quantite) * D(article.cout_unitaire))


def _vendu(cout, taux):
    """Prix de vente (au centime) d'un coût majoré du taux de marge (en %)."""
    return arrondir(D(cout) * (1 + D0(taux) / 100))


def _taux_marge_matiere(devis, ligne):
    if devis.taux_marge_globale is not None:
        return D(devis.taux_marge_globale)
    if ligne.taux_marge_matiere_applique is not None:
        return D(ligne.taux_marge_matiere_applique)
    return D0(ligne.article.taux_marge_defaut)


def _taux_marge_operation(devis, poste, operation_existante):
    if devis.taux_marge_globale is not None:
        return D(devis.taux_marge_globale)
    if operation_existante is not None and operation_existante.taux_marge_applique is not None:
        return D(operation_existante.taux_marge_applique)
    return D0(poste.taux_marge_defaut)


def _synchroniser_operations_ligne(devis, ligne):
    from .reglage import part_ligne

    etapes = list(gamme_active(ligne.article, devis.date_creation))
    reglage = part_ligne(devis, ligne.article)
    # Le réglage machine (par tôle) est ajouté à l'étape de découpe de l'article : il part dans le prix des opérations de la ligne.
    ordre_decoupe = next((e.ordre for e in etapes if e.origine == "decoupe"), None)
    if ordre_decoupe is None:
        reglage = type(reglage)()
    ligne.prix_vente_reglage = None
    ligne.prix_vente_reglage_operations = None
    ligne.note_reglage = ""
    reglages_operations = ZERO
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
        if etape.ordre == ordre_decoupe:
            cout += reglage.cout
        taux = _taux_marge_operation(devis, etape.poste, operation if operation.pk else None)
        operation.cout_calcule = cout
        operation.taux_marge_applique = taux
        operation.prix_vente = _vendu(cout, taux)
        operation.save()
        if etape.ordre != ordre_decoupe and etape.poste.mode_calcul == PosteTravail.ModeCalcul.HORAIRE and etape.temps_fixe:
            # temps de réglage par lot de l'opération (déjà dans son coût) : somme informative des prix de vente de ces réglages
            tarif = tarif_poste_valide(etape.poste, devis.date_creation)
            reglages_operations += _vendu(arrondir_prix(D(etape.temps_fixe) / 60 * D(tarif.cout_horaire)), taux)
        if etape.ordre == ordre_decoupe and (reglage.cout or reglage.note):
            ligne.prix_vente_reglage = _vendu(reglage.cout, taux)
            ligne.note_reglage = (reglage.note + (" — " + " ; ".join(reglage.avertissements) if reglage.avertissements else ""))[:250]
    ligne.prix_vente_reglage_operations = reglages_operations or None
    ligne.save(update_fields=["prix_vente_reglage", "prix_vente_reglage_operations", "note_reglage"])


def calculer_ligne(devis, ligne):
    """Calcule et enregistre le coût matière, le prix de vente matière et les
    opérations d'UNE ligne. Peut lever ChiffrageError si les données de cette
    ligne (ou de son article) sont insuffisantes — n'affecte jamais les
    autres lignes du devis, contrairement à calculer_devis() qui s'arrête à
    la première ligne en erreur. Utilisé isolément par le recalcul/aperçu en
    direct d'une ligne, pour qu'une ligne à problème n'empêche pas les
    autres lignes du même devis de se recalculer normalement."""
    ligne.cout_matiere_calcule = cout_matiere_article(ligne.article, ligne.quantite, devis=devis)
    taux = _taux_marge_matiere(devis, ligne)
    ligne.taux_marge_matiere_applique = taux
    ligne.prix_vente_matiere = _vendu(ligne.cout_matiere_calcule, taux)
    ligne.save()

    if ligne.article.nature == Article.Nature.FABRIQUE:
        _synchroniser_operations_ligne(devis, ligne)
    else:
        ligne.operations.all().delete()
    operations = list(DevisLigneOperation.objects.filter(devis_ligne=ligne).order_by("ordre"))
    calcule = D0(ligne.prix_vente_matiere) + sum((D0(op.prix_vente) for op in operations), ZERO)
    avant_remise = calcule
    if ligne.prix_vente_unitaire_force is not None:
        avant_remise = arrondir(D(ligne.prix_vente_unitaire_force) * D(ligne.quantite))
    cible = avant_remise
    if ligne.remise_pct:
        cible = arrondir(avant_remise * (1 - D(ligne.remise_pct) / 100))
    ligne.prix_vente_avant_remise = avant_remise
    if cible != calcule:
        _ajuster_prix(ligne, operations, calcule, cible)
    ligne.save(update_fields=["prix_vente_avant_remise"])


def _repartir(cible, parts):
    """Répartit `cible` au prorata de `parts` (au centime) ; le premier montant absorbe l'écart d'arrondi."""
    total = sum(parts, ZERO)
    if total <= 0:
        return [cible] + [ZERO] * (len(parts) - 1)
    reparties = [arrondir(p * cible / total) for p in parts]
    reparties[0] += cible - sum(reparties, ZERO)
    return reparties


def _ajuster_prix(ligne, operations, calcule, cible):
    """Ramène le prix de vente de la ligne (matière + opérations) à `cible` — prix unitaire forcé, puis remise de ligne. La
    cible est répartie entre la matière et chaque opération au prorata du prix calculé, pour que tous les totaux (devis,
    commande, marge prévue) restent cohérents."""
    parts = [D0(ligne.prix_vente_matiere)] + [D0(op.prix_vente) for op in operations]
    reparties = _repartir(cible, parts)
    ligne.prix_vente_matiere = reparties[0]
    for op, montant in zip(operations, reparties[1:]):
        op.prix_vente = montant
        op.save(update_fields=["prix_vente"])
    if calcule > 0:  # les réglages (information) suivent la même proportion
        for champ in ("prix_vente_reglage", "prix_vente_reglage_operations"):
            if getattr(ligne, champ) is not None:
                setattr(ligne, champ, arrondir(D(getattr(ligne, champ)) * cible / calcule))
    ligne.save(update_fields=["prix_vente_matiere", "prix_vente_reglage", "prix_vente_reglage_operations"])


def calculer_devis(devis):
    """Recalcule le coût matière et les opérations de toutes les lignes d'un
    devis (action admin "Recalculer le chiffrage"). S'arrête à la première
    ligne en erreur — voir calculer_ligne() pour un recalcul isolé d'une
    seule ligne, insensible aux erreurs sur les autres lignes."""
    for ligne in devis.lignes.select_related("article", "article__matiere").all():
        calculer_ligne(devis, ligne)


def previsualiser_ligne(
    devis,
    article,
    quantite,
    taux_marge_matiere_applique=None,
    prix_vente_unitaire_force=None,
    taux_tva=None,
    remise_pct=None,
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
    cout_matiere = cout_matiere_article(article, quantite, devis=devis)
    taux = _taux_marge_matiere(devis, ligne_apercu)

    prix_vente_matiere = _vendu(cout_matiere, taux)

    prix_vente_operations = ZERO
    cout_operations = ZERO
    if article.nature == Article.Nature.FABRIQUE:
        from .reglage import part_ligne

        reglage = part_ligne(devis, article, quantite) if devis.pk else None
        decoupe_vue = False
        for etape in gamme_active(article, devis.date_creation):
            cout_etape = cout_etape_gamme(etape, quantite, devis.date_creation)
            if reglage is not None and etape.origine == "decoupe" and not decoupe_vue:
                cout_etape += reglage.cout
                decoupe_vue = True
            taux_operation = _taux_marge_operation(devis, etape.poste, None)
            prix_vente_operations += _vendu(cout_etape, taux_operation)
            cout_operations += cout_etape

    # Prix forcé puis remise de ligne : mêmes règles que calculer_ligne (voir _ajuster_prix).
    avant_remise = prix_vente_matiere + prix_vente_operations
    if prix_vente_unitaire_force is not None:
        avant_remise = arrondir(D(prix_vente_unitaire_force) * D(quantite))
    cible = arrondir(avant_remise * (1 - D(remise_pct) / 100)) if remise_pct else avant_remise
    if cible != prix_vente_matiere + prix_vente_operations:
        prix_vente_matiere, prix_vente_operations = _repartir(cible, [prix_vente_matiere, prix_vente_operations])
    prix_vente_total = prix_vente_matiere + prix_vente_operations
    taux_tva_valeur = D(taux_tva.taux) if taux_tva is not None else ZERO

    return {
        "cout_total": D(cout_matiere) + cout_operations,
        "cout_matiere_calcule": cout_matiere,
        "taux_marge_matiere_applique": taux,
        "prix_vente_matiere": prix_vente_matiere,
        "prix_vente_operations": prix_vente_operations,
        "prix_vente_total": prix_vente_total,
        "prix_vente_avant_remise": avant_remise,
        "prix_vente_unitaire": (
            D(prix_vente_unitaire_force) if prix_vente_unitaire_force is not None and not remise_pct
            else arrondir_prix_vente(prix_vente_total / D(quantite)) if quantite else None
        ),
        "prix_vente_ttc": arrondir(prix_vente_total * (1 + taux_tva_valeur / 100)),
    }


def resoudre_taux_tva(article, client):
    """Taux de TVA applicable à `article` vendu à `client`, selon son régime
    fiscal (Tiers.regime_fiscal) : un client non soumis à la TVA française
    (exonéré, intracommunautaire, hors UE) applique toujours 0 %, quel que
    soit l'article — sinon le taux "normal" configuré sur l'article
    (Article.taux_tva), ou le taux par défaut du référentiel si l'article
    n'en a pas de spécifique."""
    if client.regime_fiscal != Tiers.RegimeFiscal.FRANCE:
        taux = TauxTVA.objects.filter(taux=0).first()
        if taux is None:
            raise ChiffrageError("Aucun taux de TVA à 0 % dans le référentiel (client non soumis à la TVA française).")
        return taux

    if article.taux_tva_id:
        return article.taux_tva

    taux = TauxTVA.objects.filter(est_defaut=True).first()
    if taux is None:
        raise ChiffrageError("Aucun taux de TVA par défaut dans le référentiel.")
    return taux


def taux_tva_document(client):
    """Taux de TVA proposé pour un devis ou une commande de `client` (onglet Général) : 0 % pour un client
    non soumis à la TVA française, sinon le taux par défaut du référentiel. None si le référentiel n'a pas
    le taux voulu (les lignes gardent alors le leur)."""
    if client is None:
        return None
    if client.regime_fiscal != Tiers.RegimeFiscal.FRANCE:
        return TauxTVA.objects.filter(taux=0).first()
    return TauxTVA.objects.filter(est_defaut=True).first()


def previsualiser_ligne_commande(article, quantite, date_reference, taux_marge=None):
    """Prix de vente d'une ligne de commande à partir de la quantité, du
    coût matière (nomenclature pour un article FABRIQUE, coût unitaire
    sinon) et du coût des opérations de gamme — même principe que
    previsualiser_ligne (devis), mais sans les surcharges de marge par
    devis : `taux_marge` (colonne « Taux de marge » de la ligne de
    commande) remplace la marge matière par défaut de l'article ; les
    opérations gardent les marges par défaut de leurs postes."""
    cout_matiere = cout_matiere_article(article, quantite, date_reference=date_reference)
    taux_matiere = article.taux_marge_defaut if taux_marge is None else taux_marge
    prix_matiere = _vendu(cout_matiere, taux_matiere)

    prix_operations = ZERO
    cout_operations = ZERO
    if article.nature == Article.Nature.FABRIQUE:
        for etape in gamme_active(article, date_reference):
            cout_etape = cout_etape_gamme(etape, quantite, date_reference)
            prix_operations += _vendu(cout_etape, etape.poste.taux_marge_defaut)
            cout_operations += cout_etape

    prix_total = prix_matiere + prix_operations
    return {
        "prix_vente_unitaire": arrondir_prix_vente(prix_total / D(quantite)) if quantite else None,
        "montant_ht": prix_total,
        # Détail affiché par l'aperçu de l'assistant « Composer un article » de la commande (mêmes clés que le devis).
        "prix_vente_matiere": prix_matiere,
        "prix_vente_operations": prix_operations,
        "prix_vente_total": prix_total,
        "cout_total": D(cout_matiere) + cout_operations,
        "taux_marge": taux_matiere,
    }
