"""« Valider » une demande web : retrouve ou crée le client, crée le devis (une ligne par pièce, au prix de la demande) et
passe la demande en « Traitée ».

Chaque pièce découpée devient une pièce à découper du devis (fichier DXF de fabrication, article fabriqué, gamme de découpe
dès que le paramètre de coupe a un poste), comme une pièce importée à la main. Une pièce pliée est son flan à découper, puis
une étape de pliage sur le poste de la plieuse retenue. Le complément au minimum de commande devient une ligne du devis. Le prix de chaque ligne est celui annoncé au client (prix unitaire forcé), même
si les tarifs de l'ERP ont bougé depuis."""

import datetime
import re
from decimal import ROUND_HALF_UP

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils.text import slugify

from codification.models import RegleCodification
from codification.services import enregistrer_code_utilise, generer_code
from comptes.montants import D, arrondir

PRIX_UNITAIRE = D("0.0001")
REFERENCE_MINIMUM = "MINIMUM-COMMANDE"


class ErreurConversion(Exception):
    """Validation refusée (message affichable)."""


def _code_tiers(raison_sociale):
    from commercial.models import Tiers

    code = generer_code(RegleCodification.Entite.TIERS)
    if code and not Tiers.objects.filter(pk=code).exists():
        enregistrer_code_utilise(RegleCodification.Entite.TIERS, code)
        return code
    base = "WEB-" + (slugify(raison_sociale).upper()[:30] or "CLIENT")
    code, n = base, 2
    while Tiers.objects.filter(pk=code).exists():
        code, n = f"{base}-{n}", n + 1
    return code


def _separer_nom(nom):
    """« Marc WOLLENSCHNEIDER » → prénom « Marc », nom « WOLLENSCHNEIDER » ; un seul mot reste le nom."""
    mots = nom.split()
    if len(mots) < 2:
        return "", nom.strip()
    return mots[0], " ".join(mots[1:])


def _client(demande):
    """(tiers, contact, créé ?) : le contact dont l'e-mail est celui de la demande, sinon un nouveau client et son contact."""
    from commercial.models import Contact, ContactTelephone, Tiers

    contact = (
        Contact.objects.select_related("tiers").filter(email__iexact=demande.email)
        .order_by("-tiers__type_tiers").first()  # « les_deux » et « client » avant « fournisseur »
    )
    if contact is not None:
        tiers = contact.tiers
        if tiers.type_tiers == Tiers.TypeTiers.FOURNISSEUR:
            tiers.type_tiers = Tiers.TypeTiers.LES_DEUX
            tiers.save(update_fields=["type_tiers"])
        return tiers, contact, False
    raison = (demande.societe or demande.nom).strip()[:200]
    tiers = Tiers.objects.create(code=_code_tiers(raison), raison_sociale=raison, type_tiers=Tiers.TypeTiers.CLIENT)
    prenom, nom = _separer_nom(demande.nom)
    contact = Contact.objects.create(tiers=tiers, nom=nom[:100], prenom=prenom[:100], email=demande.email, est_principal=True)
    if demande.telephone:
        ContactTelephone.objects.create(contact=contact, numero=demande.telephone[:30])
    return tiers, contact, True


def _adresse(tiers, demande):
    """Adresse de livraison de la demande : reprise si le client l'a déjà, sinon ajoutée (et facturation s'il n'en a pas)."""
    from commercial.models import Adresse, Pays

    rue = " ".join(demande.adresse.split())[:255]
    if not rue:
        return None
    code_postal, ville = demande.code_postal.strip(), demande.ville.strip()
    if not code_postal:  # ancien formulaire : « 1 rue du Coteau 67000 Strasbourg » sur une ligne
        m = re.search(r"\b(\d{5})\s+(.+)$", rue)
        if m:
            code_postal, ville, rue = m.group(1), ville or m.group(2), rue[: m.start()].strip(" ,")
    existante = tiers.adresses.filter(adresse__iexact=rue, code_postal=code_postal).first()
    if existante is not None:
        if not existante.est_livraison:
            existante.est_livraison = True
            existante.save(update_fields=["est_livraison"])
        return existante
    adresses = tiers.adresses.all()
    return Adresse.objects.create(
        tiers=tiers, adresse=rue, code_postal=code_postal[:20], ville=ville[:100], pays=Pays.objects.filter(pk="FR").first(),
        est_livraison=True, est_facturation=not adresses.filter(est_facturation=True).exists(),
        est_principale=not adresses.filter(est_principale=True, est_livraison=True).exists(),
        libelle="Site web",
    )


def _numero_devis(demande):
    from chiffrage.models import Devis

    code = generer_code(RegleCodification.Entite.DEVIS)
    if code and not Devis.objects.filter(pk=code).exists():
        enregistrer_code_utilise(RegleCodification.Entite.DEVIS, code)
        return code
    numero, n = demande.numero, 2
    while Devis.objects.filter(pk=numero).exists():
        numero, n = f"{demande.numero}-{n}", n + 1
    return numero


def _fichier_fabrication(ligne):
    """DXF de fabrication d'une pièce découpée : le fichier du client (déjà converti en DXF s'il était en SVG ou STEP) tant
    qu'il n'a pas été mis à l'échelle, sinon le contour chiffré (le flan d'une pièce pliée). None sans contour (pièce pliée d'une
    demande antérieure au 17/10)."""
    from decoupe.services import formes

    from .dxf import chemin_fichier

    fichier = chemin_fichier(ligne["demande"].get("dxf"))
    if fichier and not ligne.get("pliage") and float(ligne["demande"].get("echelle") or 1) == 1:
        return fichier.read_bytes()
    contour = ligne.get("contour")
    if not contour:
        return None
    return formes.dxf_bytes(formes.Contour(contour["exterieur"], contour.get("trous") or []))


def _piece_decoupee(devis, ligne, index, contenu):
    """Pièce à découper du devis (et son article fabriqué), réglée comme la demande ; la gamme de découpe est alimentée si le
    paramètre de coupe a un poste. Retourne (article, remarque)."""
    from decoupe.models import ProcedeCoupe
    from decoupe.services import devis_pieces
    from technique.models import Article

    d = ligne["demande"]
    nom = (ligne.get("designation") or f"Pièce {index}")[:200]
    procede = ligne.get("procede") if ligne.get("procede") in ProcedeCoupe.values else ProcedeCoupe.LASER
    piece = devis_pieces.importer_pour_devis(devis, ContentFile(contenu, name=f"{slugify(nom)[:80] or 'piece'}.dxf"), procede=procede, nom=nom)
    devis_pieces.appliquer_reglages(piece, {"matiere": str(d.get("matiere") or ""), "epaisseur": str(d.get("epaisseur") or ""),
                                            "quantite": str(d.get("quantite") or 1)})
    tole = Article.objects.filter(pk=ligne.get("tole")).first() if ligne.get("tole") else None
    if tole is not None:
        piece.tole, piece.format_tole = tole, tole.format_tole
        piece.save(update_fields=["tole", "format_tole"])
    verdict = devis_pieces.verdict(piece, alimenter=True)
    return piece.article, verdict["gamme"] or verdict["message"]


def _etape_pliage(article, ligne):
    """Étape de pliage de la gamme d'un article, après sa découpe : pièce pliée (profil ou boîte) recalculée sur la
    plieuse retenue, ou, pour un fichier STEP, la plieuse et les temps chiffrés dans la demande. Retourne la remarque."""
    from decoupe.models import PliageArticle, Plieuse
    from decoupe.services.gamme import alimenter_gamme_pliage
    from decoupe.services.pliage import ErreurPliage
    from technique.models import Gamme

    d, chiffre = ligne["demande"], ligne.get("pliage") or {}
    p = d.get("pliage") or {}
    if p.get("forme") in PliageArticle.Forme.values:
        if article.matiere is None:
            return "gamme de pliage à préparer (matière inconnue)."
        try:
            nombres = lambda valeurs: [float(v or 0) for v in valeurs or []]  # noqa: E731
            commun = {"article": article, "matiere": article.matiere, "epaisseur_mm": float(d["epaisseur"]), "forme": p["forme"]}
            if p["forme"] == PliageArticle.Forme.BOITE:
                pliage = PliageArticle.objects.create(**commun, longueur_fond_mm=float(p["longueur"]), largeur_fond_mm=float(p["largeur"]),
                                                      bords_mm=nombres(p.get("bords")), angles_bords_deg=nombres(p.get("angles")))
            else:
                pliage = PliageArticle.objects.create(**commun, longueur_pli_mm=float(p["longueur"]), faces_mm=nombres(p.get("faces")),
                                                      angles_deg=nombres(p.get("angles")))
            etape, _ = alimenter_gamme_pliage(pliage)
        except (ErreurPliage, KeyError, TypeError, ValueError) as exc:
            return f"gamme de pliage à préparer ({exc})."
        return f"pliage sur « {etape.poste} »."
    plieuse = Plieuse.objects.select_related("poste").filter(pk=chiffre.get("plieuse_id")).first()
    if plieuse is None or plieuse.poste is None or chiffre.get("temps_reglage_min") is None:
        return "gamme de pliage à préparer (plieuse ou temps non repris de la demande)."
    ordre = max(article.gamme_etapes.values_list("ordre", flat=True), default=0) + 1
    Gamme.objects.create(article=article, poste=plieuse.poste, ordre=ordre, date_debut=datetime.date.today(), origine="pliage",
                         temps_fixe=chiffre["temps_reglage_min"], temps_variable=chiffre["temps_pli_min"])
    return f"pliage sur « {plieuse.poste} » ({chiffre.get('nb_plis')} pli(s) du fichier STEP)."


def _article_simple(devis, ligne, index):
    """Pièce pliée d'une demande antérieure au 17/10 (flan non enregistré) : article fabriqué avec la seule étape de
    pliage ; la découpe du flan reste à ajouter."""
    from decoupe.services.devis_pieces import _reference_article
    from technique.models import Article, Matiere

    nom = (ligne.get("designation") or f"Pièce {index}")[:200]
    reference, par_regle = _reference_article(devis, nom)
    d = ligne["demande"]
    article = Article.objects.create(
        reference=reference, libelle=nom, nature=Article.Nature.FABRIQUE,
        matiere=Matiere.objects.filter(pk=d.get("matiere")).first(), epaisseur=d.get("epaisseur"),
    )
    if par_regle:
        enregistrer_code_utilise(RegleCodification.Entite.ARTICLE, reference)
    return article, _etape_pliage(article, ligne) + " Découpe du flan à ajouter."


def _ligne_devis(devis, article, quantite, total, designation=""):
    """Ligne du devis au prix annoncé au client. Si l'ERP ne sait pas encore chiffrer l'article (tôle, gamme), la ligne
    garde ce prix en entier en matière ; un recalcul du devis la réalignera une fois l'article complété."""
    from chiffrage.builder import ajouter_ligne_devis
    from chiffrage.moteur import ChiffrageError, calculer_ligne

    l = ajouter_ligne_devis(devis, article, quantite, designation=designation[:255])
    l.prix_vente_unitaire_force = (total / quantite).quantize(PRIX_UNITAIRE, rounding=ROUND_HALF_UP)
    l.save(update_fields=["prix_vente_unitaire_force"])
    try:
        with transaction.atomic():
            calculer_ligne(devis, l)
        return l, ""
    except ChiffrageError as exc:
        l.refresh_from_db()
        l.prix_vente_matiere = l.prix_vente_avant_remise = arrondir(total)
        l.save(update_fields=["prix_vente_matiere", "prix_vente_avant_remise"])
        return l, f"prix repris de la demande, l'ERP ne sait pas encore le recalculer ({exc})"


def _article_minimum():
    """Article de la ligne de complément au minimum de commande (créé au premier besoin) : un service, sans gamme ni
    stock, donc sans ordre de fabrication."""
    from technique.models import Article

    return Article.objects.get_or_create(
        reference=REFERENCE_MINIMUM, defaults={"libelle": "Minimum de commande", "nature": Article.Nature.SERVICE_ACHETE},
    )[0]


@transaction.atomic
def creer_devis(demande):
    """Crée le client (s'il n'existe pas) et le devis de la demande ; la demande passe en « Traitée » et pointe vers le
    devis. Retourne (devis, client créé ?, [remarques par pièce]). Lève ErreurConversion si la demande a déjà son devis."""
    from chiffrage.models import Devis
    from commercial.models import Adresse

    from ..models import DemandeWeb

    demande = DemandeWeb.objects.select_for_update().get(pk=demande.pk)
    if demande.devis_id:
        raise ErreurConversion(f"La demande {demande.numero} a déjà son devis ({demande.devis_id}).")
    tiers, contact, cree = _client(demande)
    livraison = _adresse(tiers, demande)
    devis = Devis.objects.create(
        numero=_numero_devis(demande), client=tiers, contact=contact, date_creation=datetime.date.today(),
        reference=f"Demande web {demande.numero}"[:100],
        adresse_livraison=livraison or Adresse.par_defaut(tiers, "est_livraison"),
        adresse_facturation=Adresse.par_defaut(tiers, "est_facturation"),
    )
    remarques = []
    for i, ligne in enumerate(demande.lignes, start=1):
        contenu = _fichier_fabrication(ligne)
        if contenu:
            article, remarque = _piece_decoupee(devis, ligne, i, contenu)
            if ligne.get("pliage"):  # pièce pliée : le flan se découpe, puis se plie
                remarque = f"{remarque} ; {_etape_pliage(article, ligne)}" if remarque else _etape_pliage(article, ligne)
        else:
            article, remarque = _article_simple(devis, ligne, i)
        _, prix = _ligne_devis(devis, article, int(ligne["demande"].get("quantite") or 1), D(str(ligne["prix_total_ht"])),
                               ligne.get("designation") or "")
        remarques.append(f"Pièce {i} : " + " ; ".join(x for x in (remarque, prix) if x))
    complement = D(str(demande.total_ht)) - D(str(demande.total_pieces_ht))
    if complement > 0:
        _, prix = _ligne_devis(devis, _article_minimum(), 1, complement, "Complément au minimum de commande")
        remarques.append(f"Minimum de commande : ligne de {complement:.2f} € HT ajoutée".replace(".", ",") + (f" ; {prix}" if prix else "."))
    demande.devis, demande.statut = devis, DemandeWeb.Statut.TRAITEE
    demande.save(update_fields=["devis", "statut"])
    return devis, cree, remarques
