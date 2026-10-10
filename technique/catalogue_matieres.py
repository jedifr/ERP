"""Base matières rapide : on coche des nuances (catalogue des matières courantes de la chaudronnerie) et des épaisseurs, on valide, et tout
se crée à la suite : matière, tôle (article matière première, référence et libellé d'après un modèle, prix d'achat), paramètre de coupe
jet d'eau estimé d'après l'usinabilité de la famille, règle de création pour les prochaines tôles. Ce qui existe déjà n'est jamais
écrasé ; le tout est tracé dans un lot annulable (comptes/lots.py)."""

from decimal import Decimal, InvalidOperation

from django.db import transaction

# (nuance, famille, densité kg/dm³) — valeurs courantes, modifiables sur la page (la densité de la matière en base prévaut si elle existe).
CATALOGUE = [
    ("S235", "Acier", 7.85), ("S355", "Acier", 7.85), ("S355J2W (Corten)", "Acier", 7.85), ("DC01", "Acier", 7.85), ("Hardox 450", "Acier", 7.85),
    ("1.4301", "Inox", 7.9), ("1.4307", "Inox", 7.9), ("1.4404", "Inox", 8.0), ("1.4016", "Inox", 7.7),
    ("5754", "Aluminium", 2.66), ("5083", "Aluminium", 2.66), ("1050", "Aluminium", 2.7), ("6082", "Aluminium", 2.7),
    ("Cuivre Cu-DHP", "Cuivre", 8.9), ("Laiton CuZn37", "Laiton", 8.4),
]
EPAISSEURS = [0.8, 1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10, 12, 15, 20, 25, 30]
PRESETS = {"courantes": [1.5, 2, 3, 4, 5, 6, 8, 10], "fines": [1, 1.5, 2, 3], "fortes": [10, 12, 15, 20, 25, 30]}


def lire_tableau(texte):
    """Lignes « nuance ; densité ; prix » (séparateur ; tabulation ou |) : [(nuance, densité, prix ou None)]. Les lignes invalides sont ignorées."""
    lignes = []
    for brute in (texte or "").splitlines():
        morceaux = [m.strip() for m in brute.replace("\t", ";").replace("|", ";").split(";")]
        if len(morceaux) < 2 or not morceaux[0]:
            continue
        try:
            densite = float(morceaux[1].replace(",", "."))
            prix = Decimal(morceaux[2].replace(",", ".")) if len(morceaux) > 2 and morceaux[2] else None
        except (ValueError, InvalidOperation):
            continue
        lignes.append((morceaux[0], densite, prix))
    return lignes


def _epaisseurs(valeurs):
    sortie = set()
    for v in valeurs:
        try:
            e = float(str(v).replace(",", "."))
        except ValueError:
            continue
        if e > 0:
            sortie.add(e)
    return sorted(sortie)


@transaction.atomic
def creer_base(selection, epaisseurs, utilisateur, unite_cout="poids", modele_reference="{matiere} - {largeur} x {longueur} x {epaisseur}",
               modele_libelle="Tôle {matiere} - {largeur} x {longueur} x {epaisseur} mm", creer_regle=True, jet_eau=True, formats=None):
    """Crée matières, tôles, paramètres jet d'eau et règles. `selection` : [{nom, densite, famille (nom, facultatif), prix (Decimal ou None)}].
    `formats` : FormatTole dont on crée une tôle par nuance et par épaisseur (« S235 - 1500 x 3000 x 3 ») ; à défaut, le format usuel de chaque matière.
    Retourne un rapport {crees: [(type, libellé)], ignores: [(libellé, motif)], lot}."""
    from comptes import lots
    from decoupe.models import ParametreCoupe, ProcedeCoupe
    from decoupe.services.parametres import ErreurParametre, dupliquer_vers_epaisseurs

    from .models import Article, FamilleMatiere, Matiere, RegleCreationTole

    epaisseurs = _epaisseurs(epaisseurs)
    rapport = {"crees": [], "ignores": [], "lot": None}
    crees = []  # (étiquette du modèle, clé) pour l'annulation
    for ligne in selection:
        nom = ligne["nom"].strip()
        matiere = Matiere.objects.filter(pk=nom).first()
        if matiere is None:
            famille = FamilleMatiere.objects.filter(nom__iexact=ligne.get("famille") or "").first() if ligne.get("famille") else None
            matiere = Matiere(nom=nom, densite=float(ligne["densite"]), famille=famille)
            matiere.save()  # famille déduite du nom si elle n'est pas donnée
            crees.append(("technique.Matiere", matiere.pk))
            rapport["crees"].append(("Matière", nom))
        regle = RegleCreationTole(
            nom=f"Tôle {nom}", matiere=matiere, modele_reference=modele_reference, modele_libelle=modele_libelle, unite_cout=unite_cout,
            cout_unitaire=ligne.get("prix"),
        )
        formats_matiere = [f for f in (formats or []) if f.convient_a(matiere)] or [regle.format_par_defaut(matiere)]
        for e in epaisseurs:
            for format_tole in formats_matiere:
                if Article.objects.filter(pk=regle.reference_de_base(matiere, e, format_tole)).exists():
                    rapport["ignores"].append((f"Tôle {regle.reference_de_base(matiere, e, format_tole)}", "existe déjà"))
                    continue
                tole = regle.creer_tole(matiere, e, format_tole)
                crees.append(("technique.Article", tole.pk))
                rapport["crees"].append(("Tôle", f"{tole.pk}" + ("" if tole.cout_unitaire is not None else " (sans prix)")))
            if not jet_eau:
                continue
            famille = matiere.famille
            modeles = list(ParametreCoupe.objects.filter(procede=ProcedeCoupe.JET_EAU, famille=famille, matiere__isnull=True)) if famille else []
            if not modeles:
                rapport["ignores"].append((f"Jet d'eau {nom} {e:g} mm", "aucun paramètre jet d'eau pour cette famille : à créer d'abord (Paramètres de coupe)"))
                continue
            if ParametreCoupe.objects.filter(procede=ProcedeCoupe.JET_EAU, famille=famille, matiere__isnull=True, epaisseur_mm=e).exists():
                rapport["ignores"].append((f"Jet d'eau {famille} {e:g} mm", "existe déjà"))
                continue
            modele = min(modeles, key=lambda p: abs(p.epaisseur_mm - e))
            try:
                nouveaux, _ = dupliquer_vers_epaisseurs(modele, [e])
            except ErreurParametre as exc:
                rapport["ignores"].append((f"Jet d'eau {famille} {e:g} mm", str(exc)))
                continue
            for p in nouveaux:
                crees.append(("decoupe.ParametreCoupe", p.pk))
                rapport["crees"].append(("Paramètre jet d'eau (estimé)", f"{famille} {e:g} mm"))
        if creer_regle and not RegleCreationTole.objects.filter(matiere=matiere).exists():
            regle.save()
            crees.append(("technique.RegleCreationTole", regle.pk))
            rapport["crees"].append(("Règle de création", regle.nom))
    if crees:
        rapport["lot"] = lots._enregistrer_lot(utilisateur, Matiere, f"Base matières : {len(rapport['crees'])} élément(s) créé(s)", [], crees)
    return rapport
