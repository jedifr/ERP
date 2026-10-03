"""Données fournies aux modèles de documents : dictionnaires de textes déjà mis en forme.

Chaque `contexte_<document>(objet)` reprend exactement les règles des PDF d'origine (chiffrage/documents.py) :
mêmes refus (ligne sans prix…), mêmes montants (arrondi par ligne), mêmes dates. Les chemins disponibles
pour l'éditeur sont listés dans VARIABLES (palette « Variables » de l'éditeur)."""

import base64
import html
import mimetypes
from pathlib import Path

from django.conf import settings

from comptes.models import Societe
from comptes.montants import ZERO, D0, arrondir, arrondir_prix, pourcent
from comptes.pdf import _lignes_pied, montant, quantite

from .models import ModeleDocument

T = ModeleDocument.Type


def _e(texte):
    return html.escape(str(texte or ""))


def _br(lignes):
    return "<br>".join(_e(l) for l in lignes if l)


def _date(date):
    return f"{date:%d/%m/%Y}" if date else "—"


def _erreur(message):
    from chiffrage.documents import DocumentError

    return DocumentError(message)


# --- éléments communs ---------------------------------------------------------------------------------------

def _logo(societe):
    if not societe.logo:
        return ""
    chemin = Path(settings.MEDIA_ROOT) / societe.logo.name
    if not chemin.exists():
        return ""
    type_mime = mimetypes.guess_type(chemin.name)[0] or "image/png"
    return f"data:{type_mime};base64," + base64.b64encode(chemin.read_bytes()).decode()


def _societe():
    s = Societe.charger()
    adresse = [s.adresse, f"{s.code_postal} {s.ville}".strip()]
    coordonnees = [f"<b>{_e(s.raison_sociale)}</b>", *(_e(l) for l in adresse if l)]
    if s.telephone:
        coordonnees.append(_e(f"Tél. {s.telephone}"))
    if s.email:
        coordonnees.append(_e(s.email))
    if s.site_web:
        coordonnees.append(_e(s.site_web))
    pied = _lignes_pied(s)
    return {
        "nom": s.raison_sociale, "forme_juridique": s.forme_juridique, "adresse": s.adresse,
        "code_postal": s.code_postal, "ville": s.ville, "telephone": s.telephone, "email": s.email,
        "site_web": s.site_web, "siret": s.siret, "tva": s.tva_intracommunautaire, "capital": s.capital,
        "rcs": s.rcs, "iban": s.iban, "bic": s.bic, "logo": _logo(s),
        "adresse_html": _br(adresse), "coordonnees_html": "<br>".join(coordonnees),
        "pied_html": "<br>".join(_e(l) for l in pied),
        "mentions_devis": s.mentions_devis, "mentions_commande": s.mentions_commande,
        "mentions_livraison": s.mentions_livraison,
    }


def _adresse_ou_principale(client, adresse, champ):
    return adresse or client.adresses.filter(**{champ: True}).order_by("-est_principale", "id").first()


def _bloc(client, adresse, contact=None):
    lignes = []
    if adresse:
        lignes = [adresse.adresse, f"{adresse.code_postal} {adresse.ville}".strip()]
        if adresse.pays_id:
            lignes.append(str(adresse.pays))
    contact_texte = f"À l'attention de {contact.prenom} {contact.nom}".replace("  ", " ") if contact else ""
    corps = _br(lignes) if lignes else "<i>Adresse non renseignée</i>"
    return {
        "nom": client.raison_sociale, "adresse": "\n".join(l for l in lignes if l), "adresse_html": corps,
        "contact": contact_texte,
        "bloc_html": f"<b>{_e(client.raison_sociale)}</b>" + (f"<br>{_e(contact_texte)}" if contact_texte else "") + f"<br>{corps}",
    }


def _base(titre, numero, date, references, filigrane=""):
    return {
        "societe": _societe(),
        "document": {
            "titre": titre, "numero": numero, "date": date, "filigrane": filigrane,
            "references": references, "references_html": _br(references),
        },
    }


def _totaux(lignes_ht_ttc_taux):
    """[(ht, ttc, taux)] -> totaux et TVA par taux (chaque ligne déjà arrondie au centime)."""
    total_ht = total_ttc = ZERO
    par_taux = {}
    for ht, ttc, taux in lignes_ht_ttc_taux:
        total_ht += ht
        total_ttc += ttc
        par_taux[taux] = arrondir(par_taux.get(taux, ZERO) + (ttc - ht))
    return {
        "ht": montant(arrondir(total_ht)), "ttc": montant(arrondir(total_ttc)),
        "tva": [{"taux": f"{pourcent(t)} %".replace(".", ","), "montant": montant(par_taux[t])} for t in sorted(par_taux)],
        "tva_totale": montant(arrondir(total_ttc - total_ht)),
    }


def _designation(article, libelle_ligne=""):
    libelle = libelle_ligne or article.libelle
    return {
        "reference": article.reference, "libelle": libelle,
        "designation_html": f"<b>{_e(article.reference)}</b>" + (f"<br>{_e(libelle)}" if libelle else ""),
    }


# --- documents ----------------------------------------------------------------------------------------------

def contexte_devis(devis):
    lignes_devis = list(devis.lignes.select_related("article", "taux_tva").prefetch_related("operations").all())
    if not lignes_devis:
        raise _erreur("Ce devis n'a aucune ligne.")
    non_chiffrees = [l.article.reference for l in lignes_devis if l.prix_vente_total is None]
    if non_chiffrees:
        raise _erreur("Lignes non chiffrées : " + ", ".join(non_chiffrees) + ". Recalculez le chiffrage.")

    references = [f"Date : {devis.date_creation:%d/%m/%Y}"]
    if devis.date_validite:
        references.append(f"Valable jusqu'au {devis.date_validite:%d/%m/%Y}")
    if devis.devis_origine_id:
        references += [
            f"Indice {devis.indice}",
            f"Annule et remplace {devis.devis_origine_id} (indice {devis.devis_origine.indice})",
        ]
        if devis.motif_revision:
            references.append(f"Modification : {devis.motif_revision}")
    filigrane = "" if devis.statut == devis.Statut.VALIDE else "PROVISOIRE"
    contexte = _base(f"DEVIS {devis.numero}", devis.numero, f"{devis.date_creation:%d/%m/%Y}", references, filigrane)

    lignes, calcul = [], []
    for ligne in lignes_devis:
        ht, ttc = arrondir(ligne.prix_vente_total), ligne.prix_vente_ttc
        taux = D0(ligne.taux_tva.taux) if ligne.taux_tva_id else ZERO
        calcul.append((ht, ttc, taux))
        pu = arrondir_prix(ligne.prix_vente_unitaire) if ligne.prix_vente_unitaire is not None else ZERO
        lignes.append({
            **_designation(ligne.article), "quantite": quantite(ligne.quantite), "pu_ht": montant(pu),
            "tva": f"{pourcent(taux)} %".replace(".", ","), "total_ht": montant(ht),
        })
    paiement = getattr(devis.client, "conditions_paiement", None)
    contexte.update({
        "facturation": _bloc(devis.client, _adresse_ou_principale(devis.client, devis.adresse_facturation, "est_facturation"), devis.contact),
        "livraison": _bloc(devis.client, _adresse_ou_principale(devis.client, devis.adresse_livraison, "est_livraison")),
        "lignes": lignes, "totaux": _totaux(calcul),
        "delai": devis.delai, "reglement": paiement.libelle if paiement else "",
        "mentions": contexte["societe"]["mentions_devis"],
        "indice": devis.indice, "validite": _date(devis.date_validite),
    })
    return contexte


def contexte_ar_commande(commande):
    lignes_commande = list(commande.lignes.select_related("article", "taux_tva").all())
    if not lignes_commande:
        raise _erreur("Cette commande n'a aucune ligne.")
    sans_prix = [l.article.reference for l in lignes_commande if l.prix_vente_unitaire is None]
    if sans_prix:
        raise _erreur("Lignes sans prix de vente : " + ", ".join(sans_prix) + ".")
    references = [f"Date de commande : {commande.date_commande:%d/%m/%Y}"]
    if commande.reference_client:
        references.append(f"Votre référence : {commande.reference_client}")
    if commande.devis_id:
        references.append(f"Notre offre : {commande.devis_id} (indice {commande.devis.indice})")
    contexte = _base(
        f"ACCUSÉ DE RÉCEPTION DE COMMANDE {commande.numero}", commande.numero, f"{commande.date_commande:%d/%m/%Y}",
        references, "ANNULÉE" if commande.statut == commande.Statut.ANNULEE else "",
    )
    lignes, calcul = [], []
    for ligne in lignes_commande:
        ht, ttc = ligne.montant_ht, ligne.montant_ttc
        taux = D0(ligne.taux_tva.taux) if ligne.taux_tva_id else ZERO
        calcul.append((ht, ttc, taux))
        lignes.append({
            **_designation(ligne.article, ligne.designation), "quantite": quantite(ligne.quantite_commandee),
            "pu_ht": montant(arrondir_prix(ligne.prix_vente_unitaire)), "tva": f"{pourcent(taux)} %".replace(".", ","),
            "total_ht": montant(ht), "livraison_prevue": _date(ligne.date_livraison_prevue),
        })
    paiement = getattr(commande.client, "conditions_paiement", None)
    contexte.update({
        "facturation": _bloc(commande.client, commande.adresse_facturation),
        "livraison": _bloc(commande.client, commande.adresse_livraison),
        "commande": {"numero": commande.numero, "reference_client": commande.reference_client},
        "lignes": lignes, "totaux": _totaux(calcul),
        "reglement": paiement.libelle if paiement else "", "mentions": contexte["societe"]["mentions_commande"],
    })
    return contexte


def _stock_disponible(ligne):
    from stock.models import Lot, stock_actif_pour

    if not settings.STOCK_ACTIF or not stock_actif_pour(ligne.article):
        return ""
    lots = list(Lot.objects.filter(article=ligne.article, quantite__gt=0).order_by("id"))
    if not lots:
        return "Rien en stock"
    total = sum(l.quantite for l in lots)
    coulees = [l.numero_coulee for l in lots if l.numero_coulee]
    return f"{total:g} en stock" + (f" (coulées : {', '.join(coulees)})" if coulees else "")


def contexte_bon_preparation(commande):
    lignes_commande = list(commande.lignes.select_related("article").prefetch_related("ordres_fabrication").all())
    if not lignes_commande:
        raise _erreur("Cette commande n'a aucune ligne.")
    dates = [l.date_livraison_prevue for l in lignes_commande if l.date_livraison_prevue]
    references = [f"Client : {commande.client.raison_sociale}"]
    if commande.reference_client:
        references.append(f"Réf. client : {commande.reference_client}")
    references.append(f"À livrer le : {_date(min(dates) if dates else None)}")
    contexte = _base(
        f"BON DE PRÉPARATION {commande.numero}", commande.numero, f"{commande.date_commande:%d/%m/%Y}",
        references, "ANNULÉE" if commande.statut == commande.Statut.ANNULEE else "",
    )
    lignes = []
    for ligne in lignes_commande:
        ordres = ", ".join(o.numero for o in ligne.ordres_fabrication.all())
        if ligne.article.nature == ligne.article.Nature.FABRIQUE:
            origine = ordres or "OF à créer"
        else:
            origine = _stock_disponible(ligne) or "Achat / prélèvement"
        lignes.append({
            **_designation(ligne.article, ligne.designation), "quantite": quantite(ligne.quantite_commandee),
            "a_livrer": quantite(max(ligne.quantite_commandee - ligne.quantite_livree, 0)),
            "livraison_prevue": _date(ligne.date_livraison_prevue), "origine": origine,
        })
    contexte.update({
        "facturation": _bloc(commande.client, commande.adresse_facturation),
        "livraison": _bloc(commande.client, commande.adresse_livraison),
        "commande": {"numero": commande.numero, "reference_client": commande.reference_client},
        "lignes": lignes,
    })
    return contexte


def _tracabilite(ligne_livraison):
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


def contexte_bon_livraison(livraison):
    lignes_livraison = list(livraison.lignes.select_related("commande_ligne__article"))
    if not lignes_livraison:
        raise _erreur("Cette livraison n'a aucune ligne.")
    commande = livraison.commande
    references = [f"Date : {livraison.date_livraison:%d/%m/%Y}", f"Commande : {commande.numero}"]
    if commande.reference_client:
        references.append(f"Votre référence : {commande.reference_client}")
    contexte = _base(
        f"BON DE LIVRAISON {livraison.numero}", livraison.numero, f"{livraison.date_livraison:%d/%m/%Y}",
        references, "ANNULÉ" if livraison.statut == livraison.Statut.ANNULEE else "",
    )
    lignes = []
    for ligne in lignes_livraison:
        cl = ligne.commande_ligne
        lignes.append({
            **_designation(cl.article, cl.designation), "livre": quantite(ligne.quantite_livree),
            "commande": quantite(cl.quantite_commandee), "reliquat": quantite(max(cl.quantite_commandee - cl.quantite_livree, 0)),
            "coulees": ", ".join(_tracabilite(ligne)),
        })
    contexte.update({
        "facturation": _bloc(commande.client, commande.adresse_facturation),
        "livraison": _bloc(commande.client, commande.adresse_livraison),
        "commande": {"numero": commande.numero, "reference_client": commande.reference_client},
        "lignes": lignes, "mentions": contexte["societe"]["mentions_livraison"],
    })
    return contexte


def contexte_fiche_fabrication(of):
    commande = of.commande
    references = [
        f"Commande : {of.commande_id}", f"Client : {commande.client.raison_sociale}",
        f"Lancé le : {of.date_lancement:%d/%m/%Y}", f"Livraison prévue : {_date(of.date_livraison_prevue)}",
    ]
    article = of.article
    contexte = _base(f"ORDRE DE FABRICATION {of.numero}", of.numero, f"{of.date_lancement:%d/%m/%Y}", references)
    composants = []
    for c in of.composants.select_related("article").all():
        composants.append({
            **_designation(c.article), "dimensions": " × ".join(f"{v:g}" for v in (c.longueur_mm, c.largeur_mm) if v),
            "par_piece": quantite(c.quantite_par_unite), "a_sortir": quantite(c.quantite_necessaire),
        })
    operations = [
        {"ordre": str(op.ordre), "poste": op.poste.nom, "temps": f"{op.temps_prevu:g} min".replace(".", ",") if op.temps_prevu is not None else "—"}
        for op in of.operations.select_related("poste").all()
    ]
    contexte.update({
        "facturation": _bloc(commande.client, commande.adresse_facturation),
        "livraison": _bloc(commande.client, commande.adresse_livraison),
        "commande": {"numero": commande.numero, "reference_client": commande.reference_client},
        "of": {
            "numero": of.numero, "reference": article.reference, "libelle": article.libelle,
            "quantite": quantite(of.quantite), "date_lancement": _date(of.date_lancement),
            "livraison_prevue": _date(of.date_livraison_prevue),
        },
        "composants": composants, "operations": operations,
    })
    return contexte


CONTEXTES = {
    T.DEVIS: contexte_devis, T.AR_COMMANDE: contexte_ar_commande, T.BON_PREPARATION: contexte_bon_preparation,
    T.BON_LIVRAISON: contexte_bon_livraison, T.FICHE_FABRICATION: contexte_fiche_fabrication,
}


# --- palette de variables de l'éditeur ---------------------------------------------------------------------

_SOCIETE = ("Société", [
    ("societe.nom", "Raison sociale"), ("societe.forme_juridique", "Forme juridique"),
    ("societe.coordonnees_html", "Coordonnées (bloc)"), ("societe.adresse_html", "Adresse"),
    ("societe.telephone", "Téléphone"), ("societe.email", "E-mail"), ("societe.site_web", "Site web"),
    ("societe.siret", "SIRET"), ("societe.tva", "N° de TVA"), ("societe.capital", "Capital"), ("societe.rcs", "RCS"),
    ("societe.iban", "IBAN"), ("societe.bic", "BIC"), ("societe.pied_html", "Pied de page légal (bloc)"),
])
_DOCUMENT = ("Document", [
    ("document.titre", "Titre"), ("document.numero", "Numéro"), ("document.date", "Date"),
    ("document.references_html", "Références (bloc)"), ("document.filigrane", "Filigrane (PROVISOIRE, ANNULÉ…)"),
])
_CLIENT = ("Client", [
    ("facturation.nom", "Facturé à : entreprise"), ("facturation.adresse_html", "Facturé à : adresse"),
    ("facturation.contact", "Facturé à : contact"), ("facturation.bloc_html", "Facturé à : bloc complet"),
    ("livraison.nom", "Livré à : entreprise"), ("livraison.adresse_html", "Livré à : adresse"),
    ("livraison.bloc_html", "Livré à : bloc complet"),
])
_COMMANDE = ("Commande", [("commande.numero", "N° de commande"), ("commande.reference_client", "Référence du client")])
_TOTAUX = ("Totaux", [("totaux.ht", "Total HT"), ("totaux.tva_totale", "Total TVA"), ("totaux.ttc", "Total TTC")])

VARIABLES = {
    T.DEVIS: [_SOCIETE, _DOCUMENT, _CLIENT, _TOTAUX, ("Conditions", [
        ("delai", "Délai"), ("reglement", "Conditions de règlement"), ("mentions", "Mentions légales du devis"),
        ("indice", "Indice"), ("validite", "Valable jusqu'au")]),
        ("Ligne (dans un tableau répété sur « lignes »)", [
            ("ligne.designation_html", "Désignation"), ("ligne.reference", "Référence"), ("ligne.libelle", "Libellé"),
            ("ligne.quantite", "Quantité"), ("ligne.pu_ht", "PU HT"), ("ligne.tva", "TVA"), ("ligne.total_ht", "Total HT")])],
    T.AR_COMMANDE: [_SOCIETE, _DOCUMENT, _CLIENT, _COMMANDE, _TOTAUX, ("Conditions", [
        ("reglement", "Conditions de règlement"), ("mentions", "Mentions sur les AR")]),
        ("Ligne (répété sur « lignes »)", [
            ("ligne.designation_html", "Désignation"), ("ligne.quantite", "Quantité"), ("ligne.pu_ht", "PU HT"),
            ("ligne.tva", "TVA"), ("ligne.total_ht", "Total HT"), ("ligne.livraison_prevue", "Livraison prévue")])],
    T.BON_PREPARATION: [_SOCIETE, _DOCUMENT, _CLIENT, _COMMANDE,
        ("Ligne (répété sur « lignes »)", [
            ("ligne.designation_html", "Désignation"), ("ligne.quantite", "Commandé"), ("ligne.a_livrer", "À livrer"),
            ("ligne.livraison_prevue", "Livraison prévue"), ("ligne.origine", "OF / stock")])],
    T.BON_LIVRAISON: [_SOCIETE, _DOCUMENT, _CLIENT, _COMMANDE, ("Conditions", [("mentions", "Mentions sur les BL")]),
        ("Ligne (répété sur « lignes »)", [
            ("ligne.designation_html", "Désignation"), ("ligne.livre", "Livré"), ("ligne.commande", "Commandé"),
            ("ligne.reliquat", "Reliquat"), ("ligne.coulees", "N° de coulée")])],
    T.FICHE_FABRICATION: [_SOCIETE, _DOCUMENT, _CLIENT, _COMMANDE, ("Ordre de fabrication", [
        ("of.numero", "N° d'OF"), ("of.reference", "Article"), ("of.libelle", "Libellé"), ("of.quantite", "Quantité à fabriquer"),
        ("of.date_lancement", "Date de lancement"), ("of.livraison_prevue", "Livraison prévue")]),
        ("Composant (répété sur « composants »)", [
            ("composant.designation_html", "Composant"), ("composant.dimensions", "Dimensions"),
            ("composant.par_piece", "Par pièce"), ("composant.a_sortir", "À sortir")]),
        ("Opération (répété sur « operations »)", [
            ("operation.ordre", "N°"), ("operation.poste", "Poste"), ("operation.temps", "Temps prévu")])],
}


def contexte_exemple(type_document):
    """Données fictives pour prévisualiser un modèle sans choisir de vrai document."""
    societe = _societe()
    bloc_f = {"nom": "Métallerie Durand", "adresse": "12 rue des Forges\n69003 Lyon", "adresse_html": "12 rue des Forges<br>69003 Lyon",
              "contact": "À l'attention de Marie Durand", "bloc_html": "<b>Métallerie Durand</b><br>12 rue des Forges<br>69003 Lyon"}
    bloc_l = {"nom": "Métallerie Durand — Atelier", "adresse": "4 zone industrielle\n69800 Saint-Priest",
              "adresse_html": "4 zone industrielle<br>69800 Saint-Priest",
              "bloc_html": "<b>Métallerie Durand — Atelier</b><br>4 zone industrielle<br>69800 Saint-Priest"}
    ligne = {"designation_html": "<b>FLASQUE-100</b><br>Flasque de pompe", "reference": "FLASQUE-100", "libelle": "Flasque de pompe",
             "quantite": "10", "pu_ht": "45,00 €", "tva": "20 %", "total_ht": "450,00 €", "livraison_prevue": "15/12/2026",
             "a_livrer": "10", "origine": "OF-CDE-0001-1", "livre": "6", "commande": "10", "reliquat": "4", "coulees": "C-2026-0412"}
    contexte = _base(f"{ModeleDocument.Type(type_document).label.upper()} EXEMPLE-0001", "EXEMPLE-0001", "03/10/2026",
                     ["Date : 03/10/2026", "Votre référence : BC-7788"])
    contexte["societe"] = societe
    contexte.update({
        "facturation": bloc_f, "livraison": bloc_l, "lignes": [ligne, {**ligne, "designation_html": "<b>SUPPORT-20</b><br>Support moteur", "total_ht": "180,00 €"}],
        "totaux": {"ht": "630,00 €", "ttc": "756,00 €", "tva_totale": "126,00 €", "tva": [{"taux": "20 %", "montant": "126,00 €"}]},
        "delai": "6 semaines", "reglement": "30 jours net", "mentions": "Mentions légales de l'exemple.",
        "indice": "A", "validite": "02/11/2026", "commande": {"numero": "CDE-0001", "reference_client": "BC-7788"},
        "of": {"numero": "OF-CDE-0001-1", "reference": "FLASQUE-100", "libelle": "Flasque de pompe", "quantite": "10",
               "date_lancement": "03/10/2026", "livraison_prevue": "15/12/2026"},
        "composants": [{"designation_html": "<b>TOLE-S235-3MM</b>", "dimensions": "500 × 300", "par_piece": "1", "a_sortir": "10"}],
        "operations": [{"ordre": "1", "poste": "Découpe laser", "temps": "30 min"}, {"ordre": "2", "poste": "Plieuse", "temps": "15 min"}],
    })
    return contexte
