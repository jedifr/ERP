"""Paramètres de coupe en grille : une ligne par famille (ou nuance) de matière, une colonne par épaisseur, pour un procédé (et un gaz au laser).
Chaque case montre le poste, la vitesse ou la mise en place (au choix) avec une couleur d'état, et mène à la fiche du paramètre ; une case vide
propose de le créer. On peut affecter un poste à toute une ligne, une colonne ou toute la grille d'un coup."""

from collections import defaultdict

from django.urls import reverse

from technique.models import PosteTravail

from .models import GazCoupe, ParametreCoupe, ProcedeCoupe

VUES = [("poste", "Poste de travail"), ("vitesse", "Vitesse de production"), ("mise_en_place", "Mise en place d'une tôle"), ("origine", "Origine des vitesses")]


def _libelle_epaisseur(e):
    return f"{e:g}"


def _cle_ligne(p):
    return f"m{p.matiere_id}" if p.matiere_id else f"f{p.famille_id}"


def _nom_ligne(p):
    return f"{p.matiere.nom} (nuance)" if p.matiere_id else (p.famille.nom if p.famille_id else "—")


def _contenu(p, vue):
    if vue == "poste":
        return p.poste.nom if p.poste_id else "sans poste"
    if vue == "vitesse":
        if p.procede == ProcedeCoupe.LASER:
            return f"{p.vitesse_coupe_production_m_min:g} m/min" if p.vitesse_coupe_production_m_min else "—"
        v = next((x for x in p.vitesses.all() if abs(float(x.qualite) - 3.0) < 1e-9), None) or next(iter(p.vitesses.all()), None)
        return f"{v.vitesse_haute_mm_min / 1000:g} m/min" if v else "—"
    if vue == "mise_en_place":
        if p.temps_mise_en_place_min is not None:
            return f"{p.temps_mise_en_place_min:g} min"
        if p.poste_id and p.poste.temps_mise_en_place_min is not None:
            return f"{p.poste.temps_mise_en_place_min:g} min (poste)"
        return "non renseignée"
    return p.get_origine_display()


def _etat(p, vue):
    if not p.poste_id:
        return "ko"
    if vue == "mise_en_place" and p.temps_mise_en_place_min is None and (not p.poste_id or p.poste.temps_mise_en_place_min is None):
        return "attention"
    if p.origine == "calcule":
        return "estime"
    return "ok"


def contexte_grille(procede, gaz="", vue="poste"):
    """Données de la grille : {"colonnes": [épaisseur], "lignes": [{"cle", "nom", "cases": [case | None]}], "compte": …}."""
    procede = procede if procede in ProcedeCoupe.values else ProcedeCoupe.LASER
    vue = vue if vue in dict(VUES) else "poste"
    qs = ParametreCoupe.objects.filter(procede=procede).select_related("famille", "matiere", "poste").prefetch_related("vitesses")
    if procede == ProcedeCoupe.LASER and gaz:
        qs = qs.filter(gaz=gaz)
    parametres = list(qs)
    epaisseurs = sorted({p.epaisseur_mm for p in parametres})
    par_ligne = defaultdict(dict)
    noms = {}
    for p in parametres:
        par_ligne[_cle_ligne(p)][p.epaisseur_mm] = p  # au laser sans gaz choisi, le dernier gaz l'emporte : le sélecteur de gaz est proposé
        noms[_cle_ligne(p)] = _nom_ligne(p)
    lignes = []
    for cle in sorted(par_ligne, key=lambda c: noms[c].lower()):
        cases = []
        for e in epaisseurs:
            p = par_ligne[cle].get(e)
            cases.append({"vide": True, "url": url_creation(procede, gaz, cle, e), "cible": f"cell:{cle}:{e:g}"} if p is None else {
                "pk": p.pk, "texte": _contenu(p, vue), "etat": _etat(p, vue), "url": reverse("admin:decoupe_parametrecoupe_change", args=[p.pk]),
                "titre": f"{noms[cle]} · {_libelle_epaisseur(e)} mm · {p.get_origine_display()}",
            })
        lignes.append({"cle": cle, "nom": noms[cle], "cases": cases})
    return {
        "procede": procede, "gaz": gaz, "vue": vue, "colonnes": [{"valeur": e, "libelle": _libelle_epaisseur(e)} for e in epaisseurs], "lignes": lignes,
        "compte": {"total": len(parametres), "sans_poste": sum(1 for p in parametres if not p.poste_id), "estimes": sum(1 for p in parametres if p.origine == "calcule")},
    }


def affecter_poste(procede, gaz, cible, poste):
    """Affecte `poste` (ou None) aux paramètres de la sélection : « tout », « ligne:<clé> » ou « colonne:<épaisseur> ». Retourne le nombre modifié."""
    qs = ParametreCoupe.objects.filter(procede=procede)
    if procede == ProcedeCoupe.LASER and gaz:
        qs = qs.filter(gaz=gaz)
    if cible.startswith("ligne:"):
        cle = cible.split(":", 1)[1]
        qs = qs.filter(matiere_id=int(cle[1:])) if cle.startswith("m") else qs.filter(matiere__isnull=True, famille_id=int(cle[1:]))
    elif cible.startswith("colonne:"):
        qs = qs.filter(epaisseur_mm=float(cible.split(":", 1)[1]))
    elif cible != "tout":
        raise ValueError("sélection inconnue")
    return qs.update(poste=poste)


def url_creation(procede, gaz, cle, epaisseur):
    base = reverse("admin:decoupe_parametrecoupe_add")
    champ = "matiere" if cle.startswith("m") else "famille"
    return f"{base}?procede={procede}&gaz={gaz}&{champ}={cle[1:]}&epaisseur_mm={epaisseur:g}"


def postes():
    return PosteTravail.objects.order_by("nom")


def gaz_choices():
    return list(GazCoupe.choices)


def _cases_manquantes(cible):
    """[(clé de ligne, épaisseur)] des cases vides de la sélection (jet d'eau) : « cell:<clé>:<épaisseur> », « ligne:<clé> », « colonne:<épaisseur> » ou « tout »."""
    parametres = list(ParametreCoupe.objects.filter(procede=ProcedeCoupe.JET_EAU).select_related("famille", "matiere"))
    epaisseurs = sorted({p.epaisseur_mm for p in parametres})
    existants = {(_cle_ligne(p), p.epaisseur_mm) for p in parametres}
    lignes = sorted({_cle_ligne(p) for p in parametres})
    if cible.startswith("cell:"):
        _, cle, e = cible.split(":", 2)
        lignes, epaisseurs = [cle], [float(e)]
    elif cible.startswith("ligne:"):
        lignes = [cible.split(":", 1)[1]]
    elif cible.startswith("colonne:"):
        epaisseurs = [float(cible.split(":", 1)[1])]
    elif cible != "tout":
        raise ValueError("sélection inconnue")
    return parametres, [(cle, e) for cle in lignes for e in epaisseurs if (cle, e) not in existants]


def creer_manquants(cible, poste=None):
    """Crée les paramètres jet d'eau absents de la sélection, sans ouvrir leur fiche : copie du paramètre le plus proche en épaisseur de la même
    ligne (mêmes réglages, perçage proportionnel, vitesses estimées d'après l'usinabilité), avec `poste` s'il est donné, sinon celui du modèle.
    Retourne (créés [ParametreCoupe], ignorés [(libellé, motif)])."""
    from decoupe.services.parametres import ErreurParametre, dupliquer_vers_epaisseurs

    parametres, manquantes = _cases_manquantes(cible)
    par_ligne = defaultdict(list)
    for p in parametres:
        par_ligne[_cle_ligne(p)].append(p)
    crees, ignores = [], []
    for cle, e in manquantes:
        modeles = par_ligne.get(cle)
        nom = f"{_nom_ligne(modeles[0])} {_libelle_epaisseur(e)} mm" if modeles else f"{cle} {e:g} mm"
        if not modeles:
            ignores.append((nom, "aucun paramètre de cette matière pour servir de modèle"))
            continue
        modele = min(modeles, key=lambda p: abs(p.epaisseur_mm - e))
        try:
            nouveaux, _ = dupliquer_vers_epaisseurs(modele, [e])
        except ErreurParametre as exc:
            ignores.append((nom, str(exc)))
            continue
        for nouveau in nouveaux:
            if poste is not None:
                nouveau.poste = poste
                nouveau.save(update_fields=["poste"])
            crees.append(nouveau)
    return crees, ignores
