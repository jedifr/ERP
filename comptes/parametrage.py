"""Page « Paramétrage » : tout ce qui se règle une fois (découpe, formes, atelier, articles, commercial, comptabilité, société,
utilisateurs), en cartes, avec un état par carte (« à jour », « 5 sans coût »…). Les liens respectent les droits de l'utilisateur.

Les contrôles d'état (`ETATS`) sont de petites fonctions indépendantes : ce sont aussi les premiers éléments du futur centre
« À compléter ». Tous les liens de la page sont aussi proposés par la recherche globale (Ctrl+K)."""

from django.conf import settings
from django.contrib import admin
from django.shortcuts import render
from django.urls import reverse

# (clé, titre, icône, description, [(libellé, app, modèle ou None, droit, URL nommée facultative)], clé du contrôle d'état)
CARTES = [
    ("decoupe", "Découpe", "content_cut", "Paramètres de coupe, réglages laser et jet d'eau, formats de tôle, bord de tôle", [
        ("Paramètres de coupe", "decoupe", "parametrecoupe"), ("Paramètres de coupe en grille", "decoupe", "parametrecoupe", "admin:decoupe_parametrecoupe_action_grille"), ("Réglages de coupe", "decoupe", "reglageprocede"),
        ("Formats de tôle", "decoupe", "formattole"), ("Imbrication : seuil de chutes", "decoupe", "reglageimbrication"), ("Imbrications (historique)", "decoupe", "imbricationjob"),
    ], "decoupe"),
    ("formes", "Formes et profilés", "straighten", "Cotes normalisées, sections de profilés, profils d'import DXF", [
        ("Cotes normalisées", "decoupe", "normecote"), ("Sections de profilés", "decoupe", "profilesection"),
        ("Profils d'import DXF", "decoupe", "profilimportdecoupe"),
    ], "formes"),
    ("atelier", "Atelier", "precision_manufacturing", "Postes de travail, tarifs de poste, gammes et gammes types", [
        ("Postes de travail et tarifs", "technique", "postetravail"), ("Gammes", "technique", "gamme"), ("Gammes types", "technique", "gammetype"),
    ], "atelier"),
    ("articles", "Articles et matières", "category", "Matières, familles, nomenclatures, fournisseurs et tarifs d'achat", [
        ("Matières", "technique", "matiere"), ("Familles de matière", "technique", "famillematiere"), ("Nomenclatures", "technique", "nomenclature"),
        ("Fournisseurs d'article", "achats", "articlefournisseur"), ("Tarifs d'achat", "achats", "tarifachatarticle"),
    ], "articles"),
    ("stock", "Stock", "warehouse", "Emplacements, mouvements, transferts", [
        ("Emplacements", "stock", "emplacement"), ("Mouvements de stock", "stock", "mouvementstock"), ("Transferts", "stock", "transfert"),
    ], None),
    ("commercial", "Commercial", "handshake", "TVA, conditions de paiement, délais proposés, devises, pays, adresses, contacts", [
        ("Taux de TVA", "commercial", "tauxtva"), ("Conditions de paiement", "commercial", "conditionpaiement"), ("Délais proposés", "commercial", "delaipropose"),
        ("Devises", "commercial", "devise"), ("Pays", "commercial", "pays"), ("Adresses", "commercial", "adresse"), ("Contacts", "commercial", "contact"),
    ], None),
    ("comptabilite", "Comptabilité", "receipt_long", "Plan comptable, journaux, postes de gestion, comptes d'articles et de tiers, export", [
        ("Journaux comptables", "comptabilite", "journalcomptable"), ("Plan comptable", "comptabilite", "comptecomptable"),
        ("Postes de gestion", "comptabilite", "postegestion"), ("Codes analytiques", "comptabilite", "codeanalytique"),
        ("Comptes de vente d'article", "comptabilite", "articlecomptevente"), ("Comptes d'achat d'article", "comptabilite", "articlecompteachat"),
        ("Comptes comptables de tiers", "comptabilite", "tierscomptecomptable"), ("Paramètres comptables", "comptabilite", "parametrescomptables"),
        ("Export comptable", "comptabilite", "parametresexportcomptable"),
    ], None),
    ("societe", "Société et documents", "business", "En-tête, SIRET, IBAN, mentions, modèles PDF, codification", [
        ("Société (en-tête des documents)", "comptes", "societe"), ("Modèles de documents (PDF)", "documents", "modeledocument"),
        ("Règles de codification", "codification", "reglecodification"),
    ], "societe"),
    ("utilisateurs", "Utilisateurs et droits", "admin_panel_settings", "Comptes, groupes, audit des droits, journal des connexions", [
        ("Utilisateurs", "auth", "user"), ("Groupes", "auth", "group"), ("Audit des droits", "auth", "user", "admin:auth_user_audit_droits"),
        ("Journal des connexions", "comptes", "evenementconnexion"),
    ], None),
]
MODELES_SUPERUTILISATEUR = {"modeledocument", "user", "group"}


def _a_voir(request, app, modele):
    if modele in MODELES_SUPERUTILISATEUR and not request.user.is_superuser:
        return False
    if app == "stock" and not settings.STOCK_ACTIF:
        return False
    return request.user.has_perm(f"{app}.view_{modele}")


def _url(app, modele, nom=None):
    return reverse(nom or f"admin:{app}_{modele}_changelist")


# ----------------------------------------------------------------------------------------------- contrôles d'état
# Chaque contrôle retourne (texte, ton) avec ton « ko » (bloquant), « attention » ou « ok », ou None si non concerné.
def _etat_societe():
    from .models import Societe

    s = Societe.charger()
    manques = [nom for nom, valeur in (("SIRET", s.siret), ("TVA intracommunautaire", s.tva_intracommunautaire), ("IBAN", s.iban), ("adresse", s.adresse)) if not valeur]
    return (f"{manques[0]} manquant" if len(manques) == 1 else f"{len(manques)} informations manquantes", "ko") if manques else ("à jour", "ok")


def _etat_decoupe():
    from decoupe.models import ParametreCoupe

    sans_poste = ParametreCoupe.objects.filter(poste__isnull=True).count()
    return (f"{sans_poste} sans poste de travail", "ko") if sans_poste else ("à jour", "ok")


def _etat_formes():
    from decoupe.models import NormeCote, ProfileSection

    a_verifier = NormeCote.objects.filter(verifie=False).count() + ProfileSection.objects.filter(verifie=False).count()
    return (f"{a_verifier} à vérifier", "attention") if a_verifier else ("à jour", "ok")


def _etat_atelier():
    from technique.models import PosteTravail

    sans_tarif = PosteTravail.objects.filter(mode_calcul=PosteTravail.ModeCalcul.HORAIRE, tarifs__isnull=True).count()
    return (f"{sans_tarif} poste{'s' if sans_tarif > 1 else ''} sans tarif", "ko") if sans_tarif else ("à jour", "ok")


def _etat_articles():
    from technique.models import Article

    sans_cout = Article.objects.filter(nature=Article.Nature.MATIERE_PREMIERE, cout_unitaire__isnull=True).count()
    return (f"{sans_cout} sans coût", "attention") if sans_cout else ("à jour", "ok")


ETATS = {"societe": _etat_societe, "decoupe": _etat_decoupe, "formes": _etat_formes, "atelier": _etat_atelier, "articles": _etat_articles}


def cartes(request):
    """Cartes visibles par l'utilisateur : [{cle, titre, icone, description, liens: [{libelle, url}], etat: (texte, ton) | None}]."""
    resultat = []
    for cle, titre, icone, description, liens, cle_etat in CARTES:
        visibles = [{"libelle": l[0], "url": _url(l[1], l[2], l[3] if len(l) > 3 else None)} for l in liens if _a_voir(request, l[1], l[2])]
        if not visibles:
            continue
        etat = None
        if cle_etat:
            try:
                etat = ETATS[cle_etat]()
            except Exception:  # un contrôle en échec ne doit pas empêcher d'ouvrir la page
                etat = None
        resultat.append({"cle": cle, "titre": titre, "icone": icone, "description": description, "liens": visibles, "etat": etat})
    return resultat


def nombre_a_completer(request):
    """Nombre de cartes de paramétrage qui demandent une action (pastille du menu)."""
    return sum(1 for c in cartes(request) if c["etat"] and c["etat"][1] in ("ko", "attention"))


def ecrans_pour_recherche(request):
    """[(titre, description, url, icône, mots-clés)] : tous les écrans de réglage, pour la recherche globale (Ctrl+K)."""
    resultat = []
    for carte in cartes(request):
        for lien in carte["liens"]:
            resultat.append((lien["libelle"], f"Paramétrage — {carte['titre']}", lien["url"], carte["icone"], f"{lien['libelle']} {carte['titre']} {carte['description']} paramétrage réglage"))
    return resultat


def parametrage_view(request):
    contexte = {**admin.site.each_context(request), "title": "Paramétrage", "cartes": cartes(request)}
    return render(request, "admin/parametrage.html", contexte)
