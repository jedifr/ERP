"""Écran « Importer un tableau » des listes de l'admin (voir comptes/imports.py) : données, correspondance des colonnes,
vérification, import. Un admin l'active en déclarant `import_tableau` (un `ImportTableau`) et en ajoutant
"action_importer_tableau" à `actions_list`."""

from django.core.exceptions import PermissionDenied
from django.template.response import TemplateResponse
from unfold.decorators import action as unfold_action

from . import imports
from .models import ProfilImportColonnes


def _texte(request):
    fichier = request.FILES.get("fichier")
    if fichier is not None:
        brut = fichier.read()
        try:
            return brut.decode("utf-8-sig")
        except UnicodeDecodeError:
            return brut.decode("cp1252", errors="replace")  # CSV enregistré par Excel sous Windows
    return request.POST.get("tableau", "")


def vue_import(modele_admin, request, spec):
    if not modele_admin.has_add_permission(request):
        raise PermissionDenied
    post = request.POST
    etape = post.get("etape", "donnees")
    texte = _texte(request) if request.method == "POST" else ""
    entetes, lignes = imports.lire(texte)
    contexte = {
        **modele_admin.admin_site.each_context(request), "title": spec.titre, "opts": modele_admin.model._meta, "spec": spec,
        "tableau": texte, "etape": "donnees", "niveaux": imports.NIVEAUX,
    }
    if request.method != "POST":
        return TemplateResponse(request, "admin/comptes/import_tableau.html", contexte)
    if not lignes:
        contexte["erreur"] = ("Aucune ligne sous les en-têtes : la première ligne doit porter les noms de colonnes." if entetes
                              else "Le tableau est vide : collez des lignes ou choisissez un fichier.")
        return TemplateResponse(request, "admin/comptes/import_tableau.html", contexte)
    if etape == "donnees":
        etape = "colonnes"

    sig = imports.signature(entetes)
    profil = ProfilImportColonnes.objects.filter(cible=spec.cle, signature=sig).first()
    proposes = imports.rapprocher(entetes, lignes, spec.champs, profil)
    if etape == "colonnes":
        correspondance = [p["champ"] for p in proposes]
        defauts = dict(profil.defauts) if profil else {}
    else:
        correspondance = [post.get(f"col_{i}", "") for i in range(len(entetes))]
        defauts = {c.nom: post.get(f"defaut_{c.nom}", "") for c in spec.champs if c.choix is not None}

    # Un champ ne peut venir que d'une colonne : la première l'emporte.
    vus = set()
    for i, nom in enumerate(correspondance):
        if nom in vus:
            correspondance[i] = ""
        elif nom:
            vus.add(nom)
    manquants = [c for c in spec.champs if c.obligatoire and c.nom not in vus and not defauts.get(c.nom)]

    colonnes = []
    for i, entete in enumerate(entetes):
        auto = proposes[i]
        nom = correspondance[i]
        niveau = auto["niveau"] if nom == auto["champ"] else "manuel" if nom else ""
        raison = auto["raison"] if nom == auto["champ"] else ""
        colonnes.append({
            "i": i, "entete": entete, "exemples": [l[i] for l in lignes[:3] if l[i]], "champ": nom, "niveau": niveau, "raison": raison,
        })
    contexte.update({
        "colonnes": colonnes, "manquants": manquants, "profil": profil, "nb_lignes": len(lignes),
        "a_verifier": sum(1 for c in colonnes if c["niveau"] in imports.NIVEAUX_A_VERIFIER),
        "sans_colonne": [{"champ": c, "choix": c.liste_choix(), "valeur": defauts.get(c.nom, "")} for c in spec.champs
                         if c.choix is not None and c.nom not in vus],
        "nom_profil": post.get("nom_profil") or (profil.nom if profil else ""), "memoriser": bool(post.get("memoriser")) if etape != "colonnes" else bool(profil),
    })
    if etape == "colonnes" or manquants or etape not in ("verifier", "importer"):
        contexte["etape"] = "colonnes"
        return TemplateResponse(request, "admin/comptes/import_tableau.html", contexte)

    defauts = {k: v for k, v in defauts.items() if k not in vus}
    rapport = imports.executer(spec, lignes, correspondance, defauts, request.user, essai=etape == "verifier")
    importes = [spec.champ(n) for n in correspondance if n] + [spec.champ(n) for n, v in defauts.items() if v]
    contexte.update({
        "etape": etape, "rapport": rapport, "champs_importes": importes,
        "apercu": [(n, [valeurs.get(c.nom, "") for c in importes], objet, motif) for n, valeurs, objet, motif in rapport["lignes"][: imports.LIGNES_APERCU]],
        "tronque": max(0, len(rapport["lignes"]) - imports.LIGNES_APERCU),
        "ignorees": [e for e, nom in zip(entetes, correspondance) if not nom],
    })
    if etape == "importer" and post.get("memoriser") and post.get("nom_profil", "").strip():
        ProfilImportColonnes.objects.update_or_create(
            cible=spec.cle, signature=sig,
            defaults={"nom": post["nom_profil"].strip()[:100], "correspondance": {imports.normaliser(e): n for e, n in zip(entetes, correspondance)},
                      "defauts": {k: v for k, v in defauts.items() if v}},
        )
        contexte["profil_enregistre"] = post["nom_profil"].strip()
    return TemplateResponse(request, "admin/comptes/import_tableau.html", contexte)


class ImportTableauMixin:
    import_tableau = None  # ImportTableau

    @unfold_action(description="Importer un tableau", url_path="importer", icon="upload_file")
    def action_importer_tableau(self, request):
        """Création en masse depuis un tableau collé ou un fichier CSV, avec rapprochement automatique des colonnes."""
        return vue_import(self, request, self.import_tableau)
