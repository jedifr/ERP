"""Imports de tableaux du commercial : tiers, adresses, contacts (voir comptes/imports.py). Un doublon est ignoré, jamais écrasé."""

from django.db.models import Q

from comptes.imports import ChampImport, ImportTableau, LigneIgnoree, choix, enregistrer, oui_non

from .models import Adresse, ConditionPaiement, Contact, ContactTelephone, Devise, Pays, Tiers

OUI_NON = [("oui", "Oui"), ("non", "Non")]
MOTIF_TVA = r"[A-Z]{2}[0-9A-Z]{8,12}"


def trouver_tiers(valeur):
    """Tiers par code, à défaut par raison sociale exacte (sans casse)."""
    tiers = Tiers.objects.filter(pk=valeur).first() or Tiers.objects.filter(raison_sociale__iexact=valeur).first()
    if tiers is None:
        raise LigneIgnoree(f"tiers « {valeur} » introuvable")
    return tiers


def _pays(valeur):
    if not valeur:
        return None
    pays = Pays.objects.filter(Q(pk__iexact=valeur) | Q(nom__iexact=valeur)).first()
    if pays is None:
        raise LigneIgnoree(f"pays « {valeur} » inconnu")
    return pays


# ---------------------------------------------------------------- tiers

def creer_tiers(v, journal):
    if Tiers.objects.filter(pk=v["code"]).exists():
        raise LigneIgnoree("code déjà pris")
    conditions = None
    if v.get("conditions_paiement"):
        conditions = ConditionPaiement.objects.filter(libelle__iexact=v["conditions_paiement"]).first()
        if conditions is None:
            raise LigneIgnoree(f"conditions de paiement « {v['conditions_paiement']} » inconnues")
    devise = None
    if v.get("devise"):
        devise = Devise.objects.filter(pk=v["devise"].upper()).first()
        if devise is None:
            raise LigneIgnoree(f"devise « {v['devise']} » inconnue")
    tiers = Tiers(
        code=v["code"], raison_sociale=v["raison_sociale"],
        type_tiers=choix(v.get("type_tiers"), Tiers.TypeTiers.choices + [("c", "C"), ("f", "F")], Tiers.TypeTiers.CLIENT),
        regime_fiscal=choix(v.get("regime_fiscal"), Tiers.RegimeFiscal.choices, Tiers.RegimeFiscal.FRANCE),
        siret=v.get("siret", "").replace(" ", ""), numero_tva=v.get("numero_tva", "").replace(" ", ""),
        conditions_paiement=conditions, devise=devise, iban=v.get("iban", "").replace(" ", ""), bic=v.get("bic", ""),
    )
    tiers.type_tiers = {"c": Tiers.TypeTiers.CLIENT, "f": Tiers.TypeTiers.FOURNISSEUR}.get(tiers.type_tiers, tiers.type_tiers)
    return enregistrer(tiers, journal)


IMPORT_TIERS = ImportTableau(
    cle="tiers", titre="Importer des tiers", modele=Tiers, creer=creer_tiers,
    aide="Type : client, fournisseur ou les deux (client si vide). Un code déjà pris est ignoré.",
    exemple="code;raison_sociale;type;siret;numero_tva\nCLI-001;Dupont SAS;client;12345678900011;FR12345678901",
    champs=[
        ChampImport("code", "Code", obligatoire=True, synonymes=("code tiers", "code client", "code fournisseur", "n client",
                    "numero client", "num client", "reference", "ref", "compte", "compte tiers", "compte auxiliaire")),
        ChampImport("raison_sociale", "Raison sociale", obligatoire=True, synonymes=("nom", "societe", "entreprise", "denomination",
                    "libelle", "nom client", "nom fournisseur", "intitule", "company", "name")),
        ChampImport("type_tiers", "Type de tiers", synonymes=("type", "categorie", "nature", "client fournisseur"),
                    motif=r"client|fournisseur|les deux|c|f", choix=Tiers.TypeTiers.choices),
        ChampImport("siret", "SIRET", synonymes=("n siret", "numero siret", "siret siege"), motif=r"\d{14}"),
        ChampImport("numero_tva", "Numéro de TVA", synonymes=("tva", "n tva", "tva intracom", "tva intracommunautaire",
                    "numero tva intracommunautaire", "identifiant tva", "vat", "vat number"), motif=MOTIF_TVA),
        ChampImport("regime_fiscal", "Régime fiscal", synonymes=("regime", "regime tva", "fiscalite"), choix=Tiers.RegimeFiscal.choices),
        ChampImport("conditions_paiement", "Conditions de paiement", synonymes=("conditions", "mode de reglement", "reglement",
                    "echeance", "delai de paiement", "conditions de reglement")),
        ChampImport("devise", "Devise", synonymes=("monnaie", "currency"), motif=r"EUR|USD|GBP|CHF"),
        ChampImport("iban", "IBAN", synonymes=("rib", "compte bancaire"), motif=r"[A-Z]{2}\d{2}[0-9A-Z]{11,30}"),
        ChampImport("bic", "BIC / SWIFT", synonymes=("bic", "swift", "code swift")),
    ],
)


# ---------------------------------------------------------------- adresses

def creer_adresse(v, journal):
    tiers = trouver_tiers(v["tiers"])
    if Adresse.objects.filter(tiers=tiers, adresse__iexact=v["adresse"], code_postal=v["code_postal"]).exists():
        raise LigneIgnoree("adresse déjà présente pour ce tiers")
    adresse = Adresse(
        tiers=tiers, libelle=v.get("libelle", ""), adresse=v["adresse"], code_postal=v["code_postal"], ville=v["ville"], pays=_pays(v.get("pays")),
        est_livraison=oui_non(v.get("est_livraison"), False), est_facturation=oui_non(v.get("est_facturation"), False),
        est_principale=oui_non(v.get("est_principale"), False),
    )
    return enregistrer(adresse, journal)


IMPORT_ADRESSES = ImportTableau(
    cle="adresses", titre="Importer des adresses", modele=Adresse, creer=creer_adresse,
    aide="Le tiers se désigne par son code ou sa raison sociale. Livraison, facturation, principale : oui ou non.",
    exemple="tiers;adresse;code_postal;ville;pays;livraison;facturation\nCLI-001;12 rue des Forges;69007;Lyon;FR;oui;oui",
    champs=[
        ChampImport("tiers", "Tiers", obligatoire=True, synonymes=("code", "code tiers", "code client", "client", "fournisseur",
                    "raison sociale", "societe", "compte")),
        ChampImport("libelle", "Libellé", synonymes=("nom", "site", "nom adresse", "intitule")),
        ChampImport("adresse", "Adresse", obligatoire=True, synonymes=("rue", "adresse 1", "adresse ligne 1", "voie", "address", "street")),
        ChampImport("code_postal", "Code postal", obligatoire=True, synonymes=("cp", "postal", "zip", "code post"), motif=r"\d{5}"),
        ChampImport("ville", "Ville", obligatoire=True, synonymes=("commune", "localite", "city")),
        ChampImport("pays", "Pays", synonymes=("code pays", "country")),
        ChampImport("est_livraison", "Livraison", synonymes=("adresse de livraison", "livraison"), choix=OUI_NON),
        ChampImport("est_facturation", "Facturation", synonymes=("adresse de facturation", "facturation"), choix=OUI_NON),
        ChampImport("est_principale", "Adresse principale", synonymes=("principale", "par defaut"), choix=OUI_NON),
    ],
)


# ---------------------------------------------------------------- contacts

def creer_contact(v, journal):
    tiers = trouver_tiers(v["tiers"])
    nom, prenom = v["nom"], v.get("prenom", "")
    if Contact.objects.filter(tiers=tiers, nom__iexact=nom, prenom__iexact=prenom).exists():
        raise LigneIgnoree("contact déjà présent pour ce tiers")
    contact = enregistrer(Contact(tiers=tiers, nom=nom, prenom=prenom, email=v.get("email", ""), fonction=v.get("fonction", ""),
                                  est_principal=oui_non(v.get("est_principal"), False)), journal)
    for champ, genre in (("portable", ContactTelephone.TypeTelephone.PORTABLE), ("bureau", ContactTelephone.TypeTelephone.BUREAU)):
        if v.get(champ):
            enregistrer(ContactTelephone(contact=contact, type_telephone=genre, numero=v[champ]), journal)
    return contact


IMPORT_CONTACTS = ImportTableau(
    cle="contacts", titre="Importer des contacts", modele=Contact, creer=creer_contact,
    aide="Le tiers se désigne par son code ou sa raison sociale. Un contact de même nom et prénom chez ce tiers est ignoré.",
    exemple="tiers;nom;prenom;email;fonction;portable\nCLI-001;Martin;Julie;j.martin@dupont.fr;Acheteuse;06 12 34 56 78",
    champs=[
        ChampImport("tiers", "Tiers", obligatoire=True, synonymes=("code", "code tiers", "code client", "client", "fournisseur",
                    "raison sociale", "societe", "entreprise", "compte")),
        ChampImport("nom", "Nom", obligatoire=True, synonymes=("nom de famille", "nom contact", "last name", "surname")),
        ChampImport("prenom", "Prénom", synonymes=("first name",)),
        ChampImport("email", "Email", synonymes=("e mail", "mail", "courriel", "adresse mail", "adresse email"), motif=r"[^@\s]+@[^@\s]+\.[a-z]{2,}"),
        ChampImport("fonction", "Fonction", synonymes=("poste", "titre", "role", "service")),
        ChampImport("est_principal", "Contact principal", synonymes=("principal", "par defaut"), choix=OUI_NON),
        ChampImport("portable", "Téléphone portable", synonymes=("portable", "mobile", "gsm", "tel portable", "telephone mobile"),
                    motif=r"(\+33|0)[67]\d{8}"),
        ChampImport("bureau", "Téléphone bureau", synonymes=("telephone", "tel", "tel bureau", "telephone fixe", "fixe", "ligne directe"),
                    motif=r"(\+33|0)[1-59]\d{8}"),
    ],
)
