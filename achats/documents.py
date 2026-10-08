"""Bon de commande fournisseur en PDF : à envoyer au fournisseur (lignes, quantités, prix d'achat, livraison souhaitée)."""

from chiffrage.documents import DocumentError, _bloc_adresse, _titre_et_references
from comptes.models import Societe
from comptes.montants import ZERO, arrondir, arrondir_prix
from comptes.pdf import GRIS, Paragraph, Spacer, Table, TableStyle, construire_pdf, echapper, entete_societe, montant, mm, quantite, styles, tableau_lignes


def _adresse_fournisseur(fournisseur):
    return fournisseur.adresses.filter(est_principale=True).first() or fournisseur.adresses.first()


def generer_pdf_commande_fournisseur(commande):
    st = styles()
    societe = Societe.charger()
    lignes_commande = list(commande.lignes.select_related("article").all())
    if not lignes_commande:
        raise DocumentError("Cette commande fournisseur n'a aucune ligne.")
    titre = f"BON DE COMMANDE {commande.numero}"
    references = [f"Date : {commande.date_commande:%d/%m/%Y}"]
    if commande.date_livraison_prevue:
        references.append(f"Livraison souhaitée le {commande.date_livraison_prevue:%d/%m/%Y}")

    lignes, total = [], ZERO
    for ligne in lignes_commande:
        designation = f"<b>{echapper(ligne.article.reference)}</b>" if ligne.article_id else ""
        libelle = ligne.designation or (ligne.article.libelle if ligne.article_id else "")
        if libelle:
            designation += (("<br/>" if designation else "") + echapper(libelle))
        ht = ligne.montant_ht
        total += ht
        lignes.append([
            Paragraph(designation or "—", st["normal"]),
            Paragraph(quantite(ligne.quantite_commandee), st["droite"]),
            Paragraph(montant(arrondir_prix(ligne.prix_unitaire_achat)), st["droite"]),
            Paragraph(montant(arrondir(ht)), st["droite"]),
        ])
    table = tableau_lignes(
        ["Désignation", "Quantité", "PU HT", "Total HT"], lignes, [96 * mm, 24 * mm, 28 * mm, 30 * mm], alignements_droite=(1, 2, 3),
    )
    totaux = Table([["Total HT", montant(arrondir(total))]], colWidths=[40 * mm, 34 * mm])
    totaux.hAlign = "RIGHT"
    totaux.setStyle(TableStyle([("ALIGN", (1, 0), (1, -1), "RIGHT"), ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"), ("LINEABOVE", (0, 0), (-1, 0), 0.8, GRIS), ("FONTSIZE", (0, 0), (-1, -1), 9)]))

    fournisseur = commande.fournisseur
    adresse = _adresse_fournisseur(fournisseur)
    bloc_fournisseur = _bloc_adresse("Fournisseur", fournisseur, adresse)
    livraison = [f"<b>Livrer à</b>", f"<b>{echapper(societe.raison_sociale)}</b>"]
    if societe.adresse:
        livraison.append(echapper(societe.adresse))
    if societe.code_postal or societe.ville:
        livraison.append(echapper(f"{societe.code_postal} {societe.ville}".strip()))
    bloc_livraison = Paragraph("<br/>".join(livraison), st["normal"])
    adresses = Table([[bloc_fournisseur, bloc_livraison]], colWidths=[87 * mm, 87 * mm])
    adresses.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))

    elements = [
        entete_societe(societe, st), Spacer(1, 8 * mm),
        _titre_et_references(titre, references), Spacer(1, 5 * mm),
        adresses, Spacer(1, 6 * mm), table, Spacer(1, 4 * mm), totaux, Spacer(1, 8 * mm),
        Paragraph("Merci de nous confirmer la réception de cette commande et la date de livraison. Mentionner le numéro de commande sur le bon de livraison et la facture.", st["petit"]),
    ]
    return construire_pdf(elements, titre)
