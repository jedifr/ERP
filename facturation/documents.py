"""Documents PDF de la facturation : facture et avoir (avec les mentions légales obligatoires) et lettre de relance.

La facture porte : numéro, date d'émission, date de livraison ou d'exécution, identité du vendeur (forme, capital, RCS, SIRET,
TVA intracommunautaire), du client (adresse, SIRET, TVA), désignation, quantités, prix unitaires HT, taux et montants de TVA par
taux, total HT et TTC, échéance, conditions d'escompte, pénalités de retard et indemnité forfaitaire de 40 €, mention
d'exonération de TVA selon le régime du client, coordonnées bancaires."""

from chiffrage.documents import (
    DocumentError,
    _bloc_facturation_livraison,
    _cadre_signature,
    _titre_et_references,
)
from comptes.models import Societe
from comptes.montants import ZERO, D0, arrondir, arrondir_prix, pourcent
from comptes.pdf import Paragraph, Spacer, Table, TableStyle, GRIS, construire_pdf, echapper, entete_societe, montant, mm, quantite, styles, tableau_lignes
from commercial.models import Tiers

MENTIONS_PAR_DEFAUT = (
    "Pas d'escompte pour règlement anticipé. En cas de retard de paiement, une pénalité égale à trois fois le taux d'intérêt "
    "légal sera exigible, ainsi qu'une indemnité forfaitaire pour frais de recouvrement de 40 € (art. L441-10 du Code de commerce)."
)
MENTIONS_TVA = {
    Tiers.RegimeFiscal.INTRA_UE: "Livraison intracommunautaire exonérée de TVA, article 262 ter I du CGI.",
    Tiers.RegimeFiscal.HORS_UE: "Exportation : exonération de TVA, article 262 I du CGI.",
    Tiers.RegimeFiscal.FRANCE_EXONERE: "Opération exonérée de TVA.",
}


def _designation(ligne):
    cl = ligne.commande_ligne
    texte = f"<b>{echapper(cl.article.reference)}</b>"
    libelle = cl.designation or cl.article.libelle
    if libelle:
        texte += f"<br/>{echapper(libelle)}"
    return texte


def _identite_client(client):
    morceaux = []
    if client.siret:
        morceaux.append(f"SIRET {client.siret}")
    if client.numero_tva:
        morceaux.append(f"TVA {client.numero_tva}")
    return " — ".join(morceaux)


def generer_pdf_facture(facture):
    """Facture ou avoir en PDF. Refuse un document dont les lignes ne sont pas chiffrées ou dont le montant saisi diffère du total
    des lignes (un document légal doit être cohérent)."""
    st = styles()
    societe = Societe.charger()
    commande = facture.commande
    client = commande.client
    lignes_facture = list(facture.lignes.select_related("commande_ligne__article", "commande_ligne__taux_tva").all())
    if not lignes_facture:
        raise DocumentError("Cette facture n'a aucune ligne : ajoutez-les (ou préparez-la depuis les livraisons) avant de l'imprimer.")
    ecart = facture.ecart_avec_les_lignes
    if ecart:
        raise DocumentError(
            f"Le montant HT saisi diffère de {pourcent(abs(ecart))} € du total des lignes : corrigez le montant ou les lignes avant d'imprimer."
        )
    avoir = facture.est_avoir
    titre = f"{'AVOIR' if avoir else 'FACTURE'} {facture.numero}"

    references = [f"Date : {facture.date_facturation:%d/%m/%Y}"]
    livraisons = [l.date_livraison for l in commande.livraisons.all() if getattr(l, "date_livraison", None)] if hasattr(commande, "livraisons") else []
    references.append(f"Date de livraison / d'exécution : {max(livraisons):%d/%m/%Y}" if livraisons else f"Date de livraison / d'exécution : {facture.date_facturation:%d/%m/%Y}")
    if not avoir and facture.date_echeance:
        references.append(f"Échéance : {facture.date_echeance:%d/%m/%Y}")
    references.append(f"Commande : {commande.numero}")
    if commande.reference_client:
        references.append(f"Votre référence : {commande.reference_client}")
    if avoir and facture.facture_origine_id:
        references.append(f"Avoir sur la facture {facture.facture_origine_id} du {facture.facture_origine.date_facturation:%d/%m/%Y}")
        if facture.motif:
            references.append(f"Motif : {facture.motif}")

    lignes, par_taux = [], {}
    total_ht = total_ttc = ZERO
    for ligne in lignes_facture:
        ht, ttc, taux = ligne.montant_ht, ligne.montant_ttc, D0(ligne._taux())
        total_ht += ht
        total_ttc += ttc
        par_taux.setdefault(taux, [ZERO, ZERO])
        par_taux[taux][0] += ht
        par_taux[taux][1] += ttc - ht
        signe = -1 if avoir else 1
        lignes.append([
            Paragraph(_designation(ligne), st["normal"]),
            Paragraph(quantite(ligne.quantite), st["droite"]),
            Paragraph(montant(arrondir_prix(ligne._prix())), st["droite"]),
            Paragraph(f"{pourcent(taux)} %".replace(".", ","), st["droite"]),
            Paragraph(montant(ht), st["droite"]),
        ])
    table = tableau_lignes(
        ["Désignation", "Qté", "PU HT", "TVA", "Total HT"], lignes, [80 * mm, 20 * mm, 28 * mm, 16 * mm, 30 * mm], alignements_droite=(1, 2, 3, 4),
    )

    totaux = [["Total HT", montant(arrondir(total_ht))]]
    for taux in sorted(par_taux):
        totaux.append([f"TVA {pourcent(taux)} % (base {montant(arrondir(par_taux[taux][0]))})".replace(".", ","), montant(arrondir(par_taux[taux][1]))])
    totaux.append(["Total TTC", montant(arrondir(total_ttc))])
    table_totaux = Table(totaux, colWidths=[74 * mm, 34 * mm])
    table_totaux.hAlign = "RIGHT"
    table_totaux.setStyle(TableStyle([
        ("ALIGN", (1, 0), (1, -1), "RIGHT"), ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("LINEABOVE", (0, -1), (-1, -1), 0.8, GRIS), ("FONTSIZE", (0, 0), (-1, -1), 9),
    ]))

    conditions = []
    if not avoir:
        paiement = getattr(client, "conditions_paiement", None)
        if paiement:
            conditions.append(f"<b>Conditions de règlement :</b> {echapper(paiement.libelle)}")
        if facture.date_echeance:
            conditions.append(f"<b>Date limite de règlement :</b> {facture.date_echeance:%d/%m/%Y}")
        if societe.iban:
            conditions.append(f"<b>Virement :</b> IBAN {echapper(societe.iban)}" + (f" — BIC {echapper(societe.bic)}" if societe.bic else ""))
        if facture.statut_paiement == facture.StatutPaiement.PAYE and facture.date_paiement:
            conditions.append(f"<b>Facture acquittée le {facture.date_paiement:%d/%m/%Y}.</b>")
    mention_tva = MENTIONS_TVA.get(client.regime_fiscal)
    if mention_tva and not any(t for t in par_taux if t):
        conditions.append(echapper(mention_tva))

    client_table = _bloc_facturation_livraison(client, commande.adresse_facturation, commande.adresse_livraison)
    identite = _identite_client(client)
    elements = [
        entete_societe(societe, st), Spacer(1, 8 * mm),
        _titre_et_references(titre, references), Spacer(1, 5 * mm),
        client_table,
    ]
    if identite:
        elements += [Spacer(1, 1 * mm), Paragraph(echapper(identite), st["petit"])]
    elements += [Spacer(1, 5 * mm), table, Spacer(1, 4 * mm), table_totaux, Spacer(1, 6 * mm)]
    if conditions:
        elements += [Paragraph("<br/>".join(conditions), st["normal"]), Spacer(1, 4 * mm)]
    elements += [Paragraph(echapper((societe.mentions_facture or "").strip() or MENTIONS_PAR_DEFAUT), st["petit"])]
    return construire_pdf(elements, titre)


def generer_pdf_relance(facture, niveau=None):
    """Lettre de relance de paiement (rappel, relance, dernière relance) pour une facture échue."""
    from .relances import RelanceError, composer, niveau_suivant

    if facture.est_avoir:
        raise DocumentError("On ne relance pas un avoir.")
    niveau = niveau or niveau_suivant(facture)
    try:
        objet, corps = composer(facture, niveau)
    except (RelanceError, KeyError) as exc:
        raise DocumentError(str(exc))
    st = styles()
    societe = Societe.charger()
    commande = facture.commande
    adresse = commande.adresse_facturation
    destinataire = [f"<b>{echapper(commande.client.raison_sociale)}</b>"]
    if adresse:
        destinataire += [echapper(adresse.adresse), echapper(f"{adresse.code_postal} {adresse.ville}")]
    import datetime

    elements = [
        entete_societe(societe, st), Spacer(1, 10 * mm),
        Paragraph("<br/>".join(destinataire), st["normal"]), Spacer(1, 6 * mm),
        Paragraph(f"Le {datetime.date.today():%d/%m/%Y}", st["droite"]), Spacer(1, 6 * mm),
        Paragraph(f"<b>Objet : {echapper(objet)}</b>", st["normal"]), Spacer(1, 5 * mm),
        Paragraph(echapper(corps).replace("\n", "<br/>"), st["normal"]),
    ]
    return construire_pdf(elements, f"Relance {facture.numero}")
