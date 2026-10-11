"""Facture électronique Factur-X (profil EN 16931) : un PDF contenant la facture structurée en XML (CII).

Le PDF lisible est celui de `documents.generer_pdf_facture` (polices incorporées) ; le XML est construit ici depuis la facture, avec
les mêmes montants (`documents.calculer_facture`). Avant génération, les données obligatoires sont contrôlées (société, client,
SIRET, numéros de TVA) et chaque manque est signalé avec un lien vers la page où le corriger ; le XML est ensuite validé contre le
schéma officiel (XSD) par la bibliothèque `factur-x`, qui l'incorpore au PDF avec ses métadonnées XMP.

Limites : la conformité PDF/A-3 stricte et les règles métier EN 16931 (Schematron) se vérifient avec un validateur externe
(veraPDF, Mustang, validateur FNFE) ; la facture reste à transmettre par une plateforme agréée."""

import datetime
import re
from decimal import Decimal
from xml.sax.saxutils import escape

from django.urls import reverse

from chiffrage.documents import DocumentError
from comptes.models import Societe
from comptes.montants import arrondir
from commercial.models import Tiers

from .documents import MENTIONS_TVA, calculer_facture, generer_pdf_facture

PROFIL = "urn:cen.eu:en16931:2017"
RAISONS_EXONERATION = {
    "K": "Livraison intracommunautaire exonérée de TVA, article 262 ter I du CGI",
    "G": "Exportation hors Union européenne, exonération de TVA, article 262 I du CGI",
    "E": "Opération exonérée de TVA",
    "Z": "Taux de TVA nul",
}


def _chiffres(valeur):
    return re.sub(r"\D", "", valeur or "")


def _decimal(valeur, decimales=2):
    return f"{Decimal(valeur):.{decimales}f}"


def _date(date):
    return f"{date:%Y%m%d}"


def _controler(facture, societe, client, adresse, calcul):
    """Liste (message, lien) des données manquantes ou invalides ; vide si la facture est prête."""
    from stdnum.fr import siret as controle_siret
    from stdnum.fr import tva as controle_tva

    manques = []
    page_societe = (reverse("admin:comptes_societe_changelist"), "Compléter la fiche société")
    if not societe.raison_sociale or societe.raison_sociale == "Mon entreprise":
        manques.append(("La raison sociale de la société n'est pas renseignée.", page_societe))
    if not (societe.adresse and societe.code_postal and societe.ville):
        manques.append(("L'adresse de la société (rue, code postal, ville) n'est pas renseignée.", page_societe))
    siret = _chiffres(societe.siret)
    if not controle_siret.is_valid(siret):
        manques.append((f"Le SIRET de la société est absent ou invalide (« {societe.siret} »).", page_societe))
    if any(g["categorie"] == "S" for g in calcul["groupes"]) and not controle_tva.is_valid((societe.tva_intracommunautaire or "").replace(" ", "")):
        manques.append((f"Le numéro de TVA intracommunautaire de la société est absent ou invalide (« {societe.tva_intracommunautaire} »).", page_societe))
    page_client = (reverse("admin:commercial_tiers_change", args=[client.pk]), "Compléter la fiche du client")
    if adresse is None or not (adresse.adresse and adresse.code_postal and adresse.ville):
        manques.append(("L'adresse de facturation du client (rue, code postal, ville) est absente sur la commande.", page_client))
    elif adresse.pays_id is None:
        manques.append(("Le pays de l'adresse de facturation du client n'est pas renseigné.", page_client))
    france = adresse is not None and adresse.pays_id == "FR"
    if france and not controle_siret.is_valid(_chiffres(client.siret)):
        manques.append((f"Le SIRET du client est absent ou invalide (« {client.siret} ») : il est obligatoire sur une facture entre assujettis français.", page_client))
    if any(g["categorie"] == "K" for g in calcul["groupes"]) and not client.numero_tva:
        manques.append(("Le numéro de TVA intracommunautaire du client est obligatoire pour une livraison intracommunautaire.", page_client))
    if facture.est_avoir and not facture.facture_origine_id:
        manques.append(("Un avoir doit référencer la facture qu'il corrige.", (reverse("admin:facturation_facture_change", args=[facture.pk]), "Ouvrir l'avoir")))
    if facture.montant_ttc is not None and arrondir(abs(Decimal(facture.montant_ttc))) != calcul["total_ttc"]:
        manques.append((
            f"Le montant TTC saisi ({abs(Decimal(facture.montant_ttc))} €) diffère du total des lignes ({calcul['total_ttc']} €) : "
            "la TVA est calculée par taux sur la base HT.", (reverse("admin:facturation_facture_change", args=[facture.pk]), "Corriger la facture"),
        ))
    return manques


def _partie(balise, nom, siret, rue, code_postal, ville, pays, tva=None):
    siren = _chiffres(siret)[:9]
    contenu = f"<ram:Name>{escape(nom)}</ram:Name>"
    if siren:
        contenu += f'<ram:SpecifiedLegalOrganization><ram:ID schemeID="0002">{siren}</ram:ID></ram:SpecifiedLegalOrganization>'
    contenu += (
        f"<ram:PostalTradeAddress><ram:PostcodeCode>{escape(code_postal)}</ram:PostcodeCode><ram:LineOne>{escape(rue)}</ram:LineOne>"
        f"<ram:CityName>{escape(ville)}</ram:CityName><ram:CountryID>{escape(pays)}</ram:CountryID></ram:PostalTradeAddress>"
    )
    if tva:
        contenu += f'<ram:SpecifiedTaxRegistration><ram:ID schemeID="VA">{escape(tva.replace(" ", ""))}</ram:ID></ram:SpecifiedTaxRegistration>'
    return f"<ram:{balise}>{contenu}</ram:{balise}>"


def construire_xml(facture, societe, client, adresse, calcul):
    """XML CII profil EN 16931 de la facture (ou de l'avoir : type 381, montants positifs)."""
    avoir = facture.est_avoir
    commande = facture.commande
    lignes = ""
    for numero, c in enumerate(calcul["lignes"], start=1):
        ligne = c["ligne"]
        article = ligne.commande_ligne.article
        libelle = ligne.commande_ligne.designation or article.libelle or article.reference
        lignes += (
            "<ram:IncludedSupplyChainTradeLineItem>"
            f"<ram:AssociatedDocumentLineDocument><ram:LineID>{numero}</ram:LineID></ram:AssociatedDocumentLineDocument>"
            f"<ram:SpecifiedTradeProduct><ram:SellerAssignedID>{escape(article.reference)}</ram:SellerAssignedID><ram:Name>{escape(libelle)}</ram:Name></ram:SpecifiedTradeProduct>"
            f"<ram:SpecifiedLineTradeAgreement><ram:NetPriceProductTradePrice><ram:ChargeAmount>{_decimal(c['prix'], 4)}</ram:ChargeAmount></ram:NetPriceProductTradePrice></ram:SpecifiedLineTradeAgreement>"
            f'<ram:SpecifiedLineTradeDelivery><ram:BilledQuantity unitCode="C62">{_decimal(c["quantite"], 4)}</ram:BilledQuantity></ram:SpecifiedLineTradeDelivery>'
            "<ram:SpecifiedLineTradeSettlement><ram:ApplicableTradeTax><ram:TypeCode>VAT</ram:TypeCode>"
            f"<ram:CategoryCode>{c['categorie']}</ram:CategoryCode><ram:RateApplicablePercent>{_decimal(c['taux'])}</ram:RateApplicablePercent></ram:ApplicableTradeTax>"
            f"<ram:SpecifiedTradeSettlementLineMonetarySummation><ram:LineTotalAmount>{_decimal(c['total_ht'])}</ram:LineTotalAmount></ram:SpecifiedTradeSettlementLineMonetarySummation>"
            "</ram:SpecifiedLineTradeSettlement></ram:IncludedSupplyChainTradeLineItem>"
        )

    vendeur = _partie("SellerTradeParty", societe.raison_sociale, societe.siret, societe.adresse.replace("\n", ", "), societe.code_postal, societe.ville, "FR", societe.tva_intracommunautaire)
    acheteur = _partie("BuyerTradeParty", client.raison_sociale, client.siret, adresse.adresse.replace("\n", ", "), adresse.code_postal, adresse.ville, adresse.pays_id, client.numero_tva)
    reference_commande = commande.reference_client or commande.numero
    accord = (
        f"<ram:ApplicableHeaderTradeAgreement><ram:BuyerReference>{escape(commande.reference_client or commande.numero)}</ram:BuyerReference>{vendeur}{acheteur}"
        f"<ram:BuyerOrderReferencedDocument><ram:IssuerAssignedID>{escape(reference_commande)}</ram:IssuerAssignedID></ram:BuyerOrderReferencedDocument></ram:ApplicableHeaderTradeAgreement>"
    )
    livraisons = [l.date_livraison for l in commande.livraisons.all() if getattr(l, "date_livraison", None)]
    date_livraison = max(livraisons) if livraisons else facture.date_facturation
    livraison = (
        "<ram:ApplicableHeaderTradeDelivery><ram:ActualDeliverySupplyChainEvent><ram:OccurrenceDateTime>"
        f'<udt:DateTimeString format="102">{_date(date_livraison)}</udt:DateTimeString></ram:OccurrenceDateTime></ram:ActualDeliverySupplyChainEvent></ram:ApplicableHeaderTradeDelivery>'
    )

    taxes = ""
    for g in calcul["groupes"]:
        motif = "" if g["categorie"] == "S" else f"<ram:ExemptionReason>{escape(RAISONS_EXONERATION[g['categorie']])}</ram:ExemptionReason>"
        taxes += (
            f"<ram:ApplicableTradeTax><ram:CalculatedAmount>{_decimal(g['tva'])}</ram:CalculatedAmount><ram:TypeCode>VAT</ram:TypeCode>{motif}"
            f"<ram:BasisAmount>{_decimal(g['base'])}</ram:BasisAmount><ram:CategoryCode>{g['categorie']}</ram:CategoryCode>"
            f"<ram:RateApplicablePercent>{_decimal(g['taux'])}</ram:RateApplicablePercent></ram:ApplicableTradeTax>"
        )
    paiement = ""
    if societe.iban and not avoir:
        bic = f"<ram:PayeeSpecifiedCreditorFinancialInstitution><ram:BICID>{escape(societe.bic.replace(' ', ''))}</ram:BICID></ram:PayeeSpecifiedCreditorFinancialInstitution>" if societe.bic else ""
        paiement = (
            "<ram:SpecifiedTradeSettlementPaymentMeans><ram:TypeCode>58</ram:TypeCode>"
            f"<ram:PayeePartyCreditorFinancialAccount><ram:IBANID>{escape(societe.iban.replace(' ', ''))}</ram:IBANID></ram:PayeePartyCreditorFinancialAccount>{bic}"
            "</ram:SpecifiedTradeSettlementPaymentMeans>"
        )
    echeance = facture.date_echeance if not avoir else None
    conditions = getattr(client, "conditions_paiement", None)
    termes = ""
    if echeance or (conditions and not avoir):
        description = f"<ram:Description>{escape(conditions.libelle)}</ram:Description>" if conditions else ""
        date_echeance = f'<ram:DueDateDateTime><udt:DateTimeString format="102">{_date(echeance)}</udt:DateTimeString></ram:DueDateDateTime>' if echeance else ""
        termes = f"<ram:SpecifiedTradePaymentTerms>{description}{date_echeance}</ram:SpecifiedTradePaymentTerms>"
    totaux = (
        "<ram:SpecifiedTradeSettlementHeaderMonetarySummation>"
        f"<ram:LineTotalAmount>{_decimal(calcul['total_ht'])}</ram:LineTotalAmount><ram:TaxBasisTotalAmount>{_decimal(calcul['total_ht'])}</ram:TaxBasisTotalAmount>"
        f'<ram:TaxTotalAmount currencyID="EUR">{_decimal(calcul["total_tva"])}</ram:TaxTotalAmount><ram:GrandTotalAmount>{_decimal(calcul["total_ttc"])}</ram:GrandTotalAmount>'
        f"<ram:DuePayableAmount>{_decimal(calcul['total_ttc'])}</ram:DuePayableAmount></ram:SpecifiedTradeSettlementHeaderMonetarySummation>"
    )
    origine = ""
    if avoir and facture.facture_origine_id:
        origine = (
            f"<ram:InvoiceReferencedDocument><ram:IssuerAssignedID>{escape(facture.facture_origine_id)}</ram:IssuerAssignedID>"
            f'<ram:FormattedIssueDateTime><qdt:DateTimeString format="102">{_date(facture.facture_origine.date_facturation)}</qdt:DateTimeString></ram:FormattedIssueDateTime></ram:InvoiceReferencedDocument>'
        )
    reglement = (
        f"<ram:ApplicableHeaderTradeSettlement><ram:PaymentReference>{escape(facture.numero)}</ram:PaymentReference><ram:InvoiceCurrencyCode>EUR</ram:InvoiceCurrencyCode>"
        f"{paiement}{taxes}{termes}{totaux}{origine}</ram:ApplicableHeaderTradeSettlement>"
    )
    note = ""
    if avoir and facture.motif:
        note = f"<ram:IncludedNote><ram:Content>{escape(facture.motif)}</ram:Content></ram:IncludedNote>"
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rsm:CrossIndustryInvoice xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100" '
        'xmlns:ram="urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100" '
        'xmlns:udt="urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100" '
        'xmlns:qdt="urn:un:unece:uncefact:data:standard:QualifiedDataType:100">'
        f"<rsm:ExchangedDocumentContext><ram:GuidelineSpecifiedDocumentContextParameter><ram:ID>{PROFIL}</ram:ID></ram:GuidelineSpecifiedDocumentContextParameter></rsm:ExchangedDocumentContext>"
        f"<rsm:ExchangedDocument><ram:ID>{escape(facture.numero)}</ram:ID><ram:TypeCode>{'381' if avoir else '380'}</ram:TypeCode>"
        f'<ram:IssueDateTime><udt:DateTimeString format="102">{_date(facture.date_facturation)}</udt:DateTimeString></ram:IssueDateTime>{note}</rsm:ExchangedDocument>'
        f"<rsm:SupplyChainTradeTransaction>{lignes}{accord}{livraison}{reglement}</rsm:SupplyChainTradeTransaction></rsm:CrossIndustryInvoice>"
    )
    return xml.encode("utf-8")


def _intention_de_sortie_srgb(pdf):
    """Ajoute au PDF l'intention de sortie sRGB (profil ICC) que le PDF/A exige pour les couleurs RVB ou niveaux de gris."""
    import io

    from PIL import ImageCms
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject, NumberObject, TextStringObject

    profil = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    ecrivain = PdfWriter(clone_from=PdfReader(io.BytesIO(bytes(pdf))))
    flux = DecodedStreamObject()
    flux.set_data(profil)
    flux.update({NameObject("/N"): NumberObject(3)})
    flux_ref = ecrivain._add_object(flux)
    intention = DictionaryObject({
        NameObject("/Type"): NameObject("/OutputIntent"), NameObject("/S"): NameObject("/GTS_PDFA1"),
        NameObject("/OutputConditionIdentifier"): TextStringObject("sRGB IEC61966-2.1"), NameObject("/Info"): TextStringObject("sRGB IEC61966-2.1"),
        NameObject("/DestOutputProfile"): flux_ref,
    })
    ecrivain._root_object[NameObject("/OutputIntents")] = ArrayObject([ecrivain._add_object(intention)])
    sortie = io.BytesIO()
    ecrivain.write(sortie)
    return sortie.getvalue()


def generer_facturx(facture):
    """PDF Factur-X (profil EN 16931) de la facture ou de l'avoir. Lève DocumentError, avec le lien de correction du premier manque,
    si la facture n'est pas prête."""
    from facturx import generate_from_binary

    societe = Societe.charger()
    client = facture.commande.client
    adresse = facture.commande.adresse_facturation
    if not facture.lignes.exists():
        raise DocumentError("Cette facture n'a aucune ligne : ajoutez-les avant de générer le Factur-X.")
    calcul = calculer_facture(facture)
    manques = _controler(facture, societe, client, adresse, calcul)
    if manques:
        message = "Factur-X impossible : " + " ; ".join(m for m, _ in manques)
        raise DocumentError(message, lien=manques[0][1])
    xml = construire_xml(facture, societe, client, adresse, calcul)
    pdf = generer_pdf_facture(facture, police_embarquee=True)
    titre = f"{'Avoir' if facture.est_avoir else 'Facture'} {facture.numero}"
    try:
        facturx_pdf = generate_from_binary(
            bytes(pdf), xml, flavor="factur-x", level="en16931", check_xsd=True,
            pdf_metadata={"author": societe.raison_sociale, "title": titre, "subject": f"{titre} — {client.raison_sociale}"},
        )
        return _intention_de_sortie_srgb(facturx_pdf)
    except Exception as exc:  # XML non conforme au schéma, PDF illisible : message clair plutôt qu'une erreur 500
        raise DocumentError(f"Factur-X impossible : {exc}") from exc
