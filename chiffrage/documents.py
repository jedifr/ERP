"""Documents PDF commerciaux : devis (offre au client) et bon de livraison.

La facture n'en fait pas partie : la facture légale est émise par Tiime."""

from comptes.models import Societe
from comptes.montants import ZERO, D0, arrondir, arrondir_prix, pourcent, somme
from comptes.pdf import (
    GRIS,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    construire_pdf,
    echapper,
    entete_societe,
    montant,
    mm,
    quantite,
    styles,
    tableau_lignes,
)


class DocumentError(Exception):
    """Document non générable dans l'état actuel."""


def _bloc_adresse(titre, tiers, adresse, contact=None):
    st = styles()
    lignes = [f"<b>{echapper(titre)}</b>", f"<b>{echapper(tiers.raison_sociale)}</b>"]
    if contact:
        lignes.append(echapper(f"À l'attention de {contact.prenom} {contact.nom}".replace("  ", " ")))
    if adresse:
        lignes.append(echapper(adresse.adresse))
        lignes.append(echapper(f"{adresse.code_postal} {adresse.ville}"))
        if adresse.pays_id:
            lignes.append(echapper(str(adresse.pays)))
    return Paragraph("<br/>".join(lignes), st["normal"])


def _cadre_signature(libelle):
    st = styles()
    cellule = Paragraph(f"<b>{echapper(libelle)}</b><br/><br/><br/><br/><br/>", st["normal"])
    table = Table([[cellule]], colWidths=[80 * mm], rowHeights=[32 * mm])
    table.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, GRIS), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    table.hAlign = "RIGHT"
    return table


def _titre_et_references(titre, references):
    st = styles()
    refs = Table(
        [[Paragraph(echapper(titre), st["titre"]), Paragraph("<br/>".join(echapper(r) for r in references), st["droite"])]],
        colWidths=[100 * mm, 74 * mm],
    )
    refs.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "BOTTOM"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    return refs


def generer_pdf_devis(devis):
    """PDF de l'offre. Un devis non validé est imprimable mais marqué « PROVISOIRE » ; un devis
    dont une ligne n'est pas chiffrée n'est pas imprimable (un prix absent n'est pas un prix de 0)."""
    st = styles()
    societe = Societe.charger()
    lignes_devis = list(devis.lignes.select_related("article", "taux_tva").prefetch_related("operations").all())
    if not lignes_devis:
        raise DocumentError("Ce devis n'a aucune ligne.")
    non_chiffrees = [l.article.reference for l in lignes_devis if l.prix_vente_total is None]
    if non_chiffrees:
        raise DocumentError("Lignes non chiffrées : " + ", ".join(non_chiffrees) + ". Recalculez le chiffrage.")

    references = [f"Date : {devis.date_creation:%d/%m/%Y}"]
    if devis.date_validite:
        references.append(f"Valable jusqu'au {devis.date_validite:%d/%m/%Y}")
    if devis.devis_origine_id:
        references.append(f"Indice {devis.indice}")
        references.append(f"Annule et remplace {devis.devis_origine_id} (indice {devis.devis_origine.indice})")
        if devis.motif_revision:
            references.append(f"Modification : {devis.motif_revision}")
    titre = f"DEVIS {devis.numero}"

    adresse_fact = devis.adresse_facturation or devis.client.adresses.filter(est_facturation=True).order_by("-est_principale").first()
    adresse_liv = devis.adresse_livraison
    blocs = [[_bloc_adresse("Client", devis.client, adresse_fact, devis.contact)]]
    if adresse_liv and adresse_liv != adresse_fact:
        blocs[0].append(_bloc_adresse("Livraison", devis.client, adresse_liv))
    else:
        blocs[0].append("")
    client_table = Table(blocs, colWidths=[87 * mm, 87 * mm])
    client_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))

    lignes = []
    par_taux = {}
    total_ht = total_ttc = ZERO
    for ligne in lignes_devis:
        ht = arrondir(ligne.prix_vente_total)
        ttc = ligne.prix_vente_ttc
        taux = D0(ligne.taux_tva.taux) if ligne.taux_tva_id else ZERO
        total_ht += ht
        total_ttc += ttc
        par_taux[taux] = arrondir(par_taux.get(taux, ZERO) + (ttc - ht))
        designation = f"<b>{echapper(ligne.article.reference)}</b>"
        if ligne.article.libelle:
            designation += f"<br/>{echapper(ligne.article.libelle)}"
        pu = arrondir_prix(ligne.prix_vente_unitaire) if ligne.prix_vente_unitaire is not None else ZERO
        lignes.append([
            Paragraph(designation, st["normal"]),
            Paragraph(quantite(ligne.quantite), st["droite"]),
            Paragraph(montant(pu), st["droite"]),
            Paragraph(f"{pourcent(taux)} %".replace(".", ","), st["droite"]),
            Paragraph(montant(ht), st["droite"]),
        ])
    table = tableau_lignes(
        ["Désignation", "Qté", "PU HT", "TVA", "Total HT"], lignes,
        [80 * mm, 20 * mm, 28 * mm, 16 * mm, 30 * mm], alignements_droite=(1, 2, 3, 4),
    )

    totaux = [["Total HT", montant(arrondir(total_ht))]]
    for taux in sorted(par_taux):
        totaux.append([f"TVA {pourcent(taux)} %".replace(".", ","), montant(par_taux[taux])])
    totaux.append(["Total TTC", montant(arrondir(total_ttc))])
    table_totaux = Table(totaux, colWidths=[40 * mm, 34 * mm])
    table_totaux.hAlign = "RIGHT"
    table_totaux.setStyle(TableStyle([
        ("ALIGN", (1, 0), (1, -1), "RIGHT"), ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("LINEABOVE", (0, -1), (-1, -1), 0.8, GRIS), ("FONTSIZE", (0, 0), (-1, -1), 9),
    ]))

    conditions = []
    if devis.delai:
        conditions.append(f"<b>Délai :</b> {echapper(devis.delai)}")
    paiement = getattr(devis.client, "conditions_paiement", None)
    if paiement:
        conditions.append(f"<b>Règlement :</b> {echapper(paiement.libelle)}")

    elements = [
        entete_societe(societe, st), Spacer(1, 8 * mm),
        _titre_et_references(titre, references), Spacer(1, 5 * mm),
        client_table, Spacer(1, 6 * mm),
        table, Spacer(1, 4 * mm), table_totaux, Spacer(1, 6 * mm),
    ]
    if conditions:
        elements += [Paragraph("<br/>".join(conditions), st["normal"]), Spacer(1, 4 * mm)]
    if societe.mentions_devis:
        elements += [Paragraph(echapper(societe.mentions_devis), st["petit"]), Spacer(1, 6 * mm)]
    elements += [Paragraph("Bon pour accord — date, nom, signature et cachet :", st["normal"]), Spacer(1, 2 * mm), _cadre_signature("Le client")]
    return construire_pdf(elements, titre, filigrane="" if devis.statut == devis.Statut.VALIDE else "PROVISOIRE")


def _tracabilite(ligne_livraison):
    """Numéros de coulée / certificats des lots sortis pour cette ligne (voir stock.Lot)."""
    from stock.models import MouvementStock

    references = set()
    for mouvement in MouvementStock.objects.filter(
        reference_origine=f"LIVRAISON-{ligne_livraison.livraison_id}",
        type_mouvement=MouvementStock.TypeMouvement.SORTIE,
        lot__article=ligne_livraison.commande_ligne.article,
    ).select_related("lot"):
        coulee = getattr(mouvement.lot, "numero_coulee", "")
        if coulee:
            references.add(coulee)
    return sorted(references)


def generer_pdf_bon_livraison(livraison):
    st = styles()
    societe = Societe.charger()
    lignes_livraison = list(livraison.lignes.select_related("commande_ligne__article"))
    if not lignes_livraison:
        raise DocumentError("Cette livraison n'a aucune ligne.")
    commande = livraison.commande
    titre = f"BON DE LIVRAISON {livraison.numero}"
    references = [f"Date : {livraison.date_livraison:%d/%m/%Y}", f"Commande : {commande.numero}"]
    if commande.reference_client:
        references.append(f"Votre référence : {commande.reference_client}")

    lignes = []
    avec_tracabilite = False
    for ligne in lignes_livraison:
        cl = ligne.commande_ligne
        designation = f"<b>{echapper(cl.article.reference)}</b>"
        libelle = cl.designation or cl.article.libelle
        if libelle:
            designation += f"<br/>{echapper(libelle)}"
        coulees = _tracabilite(ligne)
        avec_tracabilite = avec_tracabilite or bool(coulees)
        lignes.append([
            Paragraph(designation, st["normal"]),
            Paragraph(quantite(ligne.quantite_livree), st["droite"]),
            Paragraph(quantite(cl.quantite_commandee), st["droite"]),
            Paragraph(quantite(max(cl.quantite_commandee - cl.quantite_livree, 0)), st["droite"]),
            Paragraph(echapper(", ".join(coulees)) or "—", st["normal"]),
        ])
    table = tableau_lignes(
        ["Désignation", "Livré", "Commandé", "Reliquat", "N° de coulée"], lignes,
        [70 * mm, 20 * mm, 24 * mm, 22 * mm, 38 * mm], alignements_droite=(1, 2, 3),
    )

    adresse_liv = commande.adresse_livraison
    blocs = [[_bloc_adresse("Livré à", commande.client, adresse_liv), _bloc_adresse("Facturé à", commande.client, commande.adresse_facturation)]]
    client_table = Table(blocs, colWidths=[87 * mm, 87 * mm])
    client_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))

    elements = [
        entete_societe(societe, st), Spacer(1, 8 * mm),
        _titre_et_references(titre, references), Spacer(1, 5 * mm),
        client_table, Spacer(1, 6 * mm), table, Spacer(1, 6 * mm),
    ]
    if societe.mentions_livraison:
        elements += [Paragraph(echapper(societe.mentions_livraison), st["petit"]), Spacer(1, 5 * mm)]
    elements += [
        Paragraph("Marchandise reçue en bon état — date, nom et signature :", st["normal"]), Spacer(1, 2 * mm),
        _cadre_signature("Le destinataire"),
    ]
    return construire_pdf(
        elements, titre, filigrane="ANNULÉ" if livraison.statut == livraison.Statut.ANNULEE else ""
    )


def _bloc_adresses_commande(commande):
    blocs = [[
        _bloc_adresse("Facturé à", commande.client, commande.adresse_facturation),
        _bloc_adresse("Livré à", commande.client, commande.adresse_livraison),
    ]]
    table = Table(blocs, colWidths=[87 * mm, 87 * mm])
    table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    return table


def _designation(ligne):
    texte = f"<b>{echapper(ligne.article.reference)}</b>"
    libelle = ligne.designation or ligne.article.libelle
    if libelle:
        texte += f"<br/>{echapper(libelle)}"
    return texte


def _date_ou_tiret(date):
    return f"{date:%d/%m/%Y}" if date else "—"


def generer_pdf_ar_commande(commande):
    """Accusé de réception de commande, à envoyer au client : lignes, prix, dates de livraison prévues.
    Une ligne sans prix n'est pas imprimable (un prix absent n'est pas un prix de 0)."""
    st = styles()
    societe = Societe.charger()
    lignes_commande = list(commande.lignes.select_related("article", "taux_tva").all())
    if not lignes_commande:
        raise DocumentError("Cette commande n'a aucune ligne.")
    sans_prix = [l.article.reference for l in lignes_commande if l.prix_vente_unitaire is None]
    if sans_prix:
        raise DocumentError("Lignes sans prix de vente : " + ", ".join(sans_prix) + ".")

    titre = f"ACCUSÉ DE RÉCEPTION DE COMMANDE {commande.numero}"
    references = [f"Date de commande : {commande.date_commande:%d/%m/%Y}"]
    if commande.reference_client:
        references.append(f"Votre référence : {commande.reference_client}")
    if commande.devis_id:
        references.append(f"Notre offre : {commande.devis_id} (indice {commande.devis.indice})")

    lignes, par_taux = [], {}
    total_ht = total_ttc = ZERO
    for ligne in lignes_commande:
        ht, ttc = ligne.montant_ht, ligne.montant_ttc
        taux = D0(ligne.taux_tva.taux) if ligne.taux_tva_id else ZERO
        total_ht += ht
        total_ttc += ttc
        par_taux[taux] = arrondir(par_taux.get(taux, ZERO) + (ttc - ht))
        lignes.append([
            Paragraph(_designation(ligne), st["normal"]),
            Paragraph(quantite(ligne.quantite_commandee), st["droite"]),
            Paragraph(montant(arrondir_prix(ligne.prix_vente_unitaire)), st["droite"]),
            Paragraph(f"{pourcent(taux)} %".replace(".", ","), st["droite"]),
            Paragraph(montant(ht), st["droite"]),
            Paragraph(_date_ou_tiret(ligne.date_livraison_prevue), st["droite"]),
        ])
    table = tableau_lignes(
        ["Désignation", "Qté", "PU HT", "TVA", "Total HT", "Livraison prévue"], lignes,
        [60 * mm, 16 * mm, 26 * mm, 14 * mm, 28 * mm, 30 * mm], alignements_droite=(1, 2, 3, 4, 5),
    )
    totaux = [["Total HT", montant(arrondir(total_ht))]]
    for taux in sorted(par_taux):
        totaux.append([f"TVA {pourcent(taux)} %".replace(".", ","), montant(par_taux[taux])])
    totaux.append(["Total TTC", montant(arrondir(total_ttc))])
    table_totaux = Table(totaux, colWidths=[40 * mm, 34 * mm])
    table_totaux.hAlign = "RIGHT"
    table_totaux.setStyle(TableStyle([
        ("ALIGN", (1, 0), (1, -1), "RIGHT"), ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("LINEABOVE", (0, -1), (-1, -1), 0.8, GRIS), ("FONTSIZE", (0, 0), (-1, -1), 9),
    ]))

    conditions = []
    paiement = getattr(commande.client, "conditions_paiement", None)
    if paiement:
        conditions.append(f"<b>Règlement :</b> {echapper(paiement.libelle)}")
    elements = [
        entete_societe(societe, st), Spacer(1, 8 * mm),
        _titre_et_references(titre, references), Spacer(1, 5 * mm),
        _bloc_adresses_commande(commande), Spacer(1, 6 * mm),
        Paragraph("Nous accusons réception de votre commande et vous en remercions. Elle sera livrée aux dates indiquées ci-dessous.", st["normal"]),
        Spacer(1, 4 * mm), table, Spacer(1, 4 * mm), table_totaux, Spacer(1, 6 * mm),
    ]
    if conditions:
        elements += [Paragraph("<br/>".join(conditions), st["normal"]), Spacer(1, 4 * mm)]
    if societe.mentions_commande:
        elements += [Paragraph(echapper(societe.mentions_commande), st["petit"]), Spacer(1, 4 * mm)]
    return construire_pdf(
        elements, titre, filigrane="ANNULÉE" if commande.statut == commande.Statut.ANNULEE else ""
    )


def _stock_disponible(ligne):
    """« 12 en stock (lots : C-123, C-456) » pour une ligne d'article géré en stock, sinon chaîne vide."""
    from django.conf import settings

    from stock.models import Lot, stock_actif_pour

    if not settings.STOCK_ACTIF or not stock_actif_pour(ligne.article):
        return ""
    lots = list(Lot.objects.filter(article=ligne.article, quantite__gt=0).order_by("id"))
    if not lots:
        return "Rien en stock"
    total = sum(l.quantite for l in lots)
    coulees = [l.numero_coulee for l in lots if l.numero_coulee]
    return f"{total:g} en stock" + (f" (coulées : {', '.join(coulees)})" if coulees else "")


def generer_pdf_bon_preparation(commande):
    """Bon de préparation (document interne, sans prix) : ce qu'il faut préparer, fabriquer ou prélever
    pour cette commande, avec les dates de livraison prévues et les ordres de fabrication liés."""
    st = styles()
    societe = Societe.charger()
    lignes_commande = list(commande.lignes.select_related("article").prefetch_related("ordres_fabrication").all())
    if not lignes_commande:
        raise DocumentError("Cette commande n'a aucune ligne.")

    titre = f"BON DE PRÉPARATION {commande.numero}"
    dates = [l.date_livraison_prevue for l in lignes_commande if l.date_livraison_prevue]
    references = [f"Client : {commande.client.raison_sociale}"]
    if commande.reference_client:
        references.append(f"Réf. client : {commande.reference_client}")
    references.append(f"À livrer le : {_date_ou_tiret(min(dates) if dates else None)}")

    lignes = []
    for ligne in lignes_commande:
        reste = max(ligne.quantite_commandee - ligne.quantite_livree, 0)
        ordres = ", ".join(o.numero for o in ligne.ordres_fabrication.all())
        if ligne.article.nature == ligne.article.Nature.FABRIQUE:
            origine = ordres or "OF à créer"
        else:
            origine = _stock_disponible(ligne) or "Achat / prélèvement"
        lignes.append([
            Paragraph(_designation(ligne), st["normal"]),
            Paragraph(quantite(ligne.quantite_commandee), st["droite"]),
            Paragraph(quantite(reste), st["droite"]),
            Paragraph(_date_ou_tiret(ligne.date_livraison_prevue), st["droite"]),
            Paragraph(echapper(origine), st["normal"]),
            Paragraph("[   ]", st["droite"]),
        ])
    table = tableau_lignes(
        ["Désignation", "Commandé", "À livrer", "Livraison prévue", "Origine / stock", "Prêt"], lignes,
        [56 * mm, 20 * mm, 18 * mm, 26 * mm, 42 * mm, 12 * mm], alignements_droite=(1, 2, 3, 5),
    )
    adresse = commande.adresse_livraison
    blocs = Table([[_bloc_adresse("Livraison", commande.client, adresse)]], colWidths=[174 * mm])
    blocs.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    elements = [
        entete_societe(societe, st), Spacer(1, 8 * mm),
        _titre_et_references(titre, references), Spacer(1, 5 * mm),
        blocs, Spacer(1, 6 * mm), table, Spacer(1, 8 * mm),
        Paragraph("Préparé par : ____________________    Date : ____________________", st["normal"]),
    ]
    return construire_pdf(
        elements, titre, filigrane="ANNULÉE" if commande.statut == commande.Statut.ANNULEE else ""
    )


def generer_pdf_ordre_fabrication(of):
    """Fiche de fabrication : quantité, livraison prévue, nomenclature (composants) et gamme (opérations)."""
    st = styles()
    societe = Societe.charger()
    titre = f"ORDRE DE FABRICATION {of.numero}"
    references = [
        f"Commande : {of.commande_id}",
        f"Client : {of.commande.client.raison_sociale}",
        f"Lancé le : {of.date_lancement:%d/%m/%Y}",
        f"Livraison prévue : {_date_ou_tiret(of.date_livraison_prevue)}",
    ]
    article = of.article
    resume = Paragraph(
        f"<b>{echapper(article.reference)}</b>"
        + (f" — {echapper(article.libelle)}" if article.libelle else "")
        + f"<br/>Quantité à fabriquer : <b>{quantite(of.quantite)}</b>",
        st["normal"],
    )

    composants = list(of.composants.select_related("article").all())
    if composants:
        lignes_nomenclature = []
        for c in composants:
            dimensions = " × ".join(f"{v:g}" for v in (c.longueur_mm, c.largeur_mm) if v)
            lignes_nomenclature.append([
                Paragraph(f"<b>{echapper(c.article.reference)}</b>" + (f"<br/>{echapper(c.article.libelle)}" if c.article.libelle else ""), st["normal"]),
                Paragraph(echapper(dimensions) or "—", st["normal"]),
                Paragraph(quantite(c.quantite_par_unite), st["droite"]),
                Paragraph(quantite(c.quantite_necessaire), st["droite"]),
            ])
        nomenclature = tableau_lignes(
            ["Composant", "Dimensions (mm)", "Par pièce", "À sortir"], lignes_nomenclature,
            [80 * mm, 38 * mm, 26 * mm, 30 * mm], alignements_droite=(2, 3),
        )
    else:
        nomenclature = Paragraph("Aucun composant dans la nomenclature.", st["petit"])

    operations = list(of.operations.select_related("poste").all())
    if operations:
        lignes_gamme = [
            [
                Paragraph(str(op.ordre), st["droite"]),
                Paragraph(echapper(op.poste.nom), st["normal"]),
                Paragraph(f"{op.temps_prevu:g} min".replace(".", ",") if op.temps_prevu is not None else "—", st["droite"]),
                Paragraph("[   ]", st["droite"]),
            ]
            for op in operations
        ]
        gamme = tableau_lignes(
            ["N°", "Poste / opération", "Temps prévu", "Fait"], lignes_gamme,
            [16 * mm, 100 * mm, 32 * mm, 26 * mm], alignements_droite=(0, 2, 3),
        )
    else:
        gamme = Paragraph("Aucune opération de gamme.", st["petit"])

    elements = [
        entete_societe(societe, st), Spacer(1, 8 * mm),
        _titre_et_references(titre, references), Spacer(1, 5 * mm),
        resume, Spacer(1, 6 * mm),
        Paragraph("<b>Nomenclature</b>", st["normal"]), Spacer(1, 2 * mm), nomenclature, Spacer(1, 6 * mm),
        Paragraph("<b>Gamme</b>", st["normal"]), Spacer(1, 2 * mm), gamme,
    ]
    return construire_pdf(elements, titre)
