"""Panneau « Pièces à découper » de la fiche devis : import de DXF/DWG, réglages par pièce, verdict immédiat.

Les vues sont appelées en AJAX par chiffrage/devis_pieces.js ; chaque carte de pièce est rendue côté serveur
(admin/chiffrage/devis/_carte_piece.html), la même vue servant à l'affichage initial et aux mises à jour."""

import json

from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.urls import path, reverse
from django.utils.decorators import method_decorator
from django.views.decorators.http import require_POST

from decoupe.models import GazCoupe, PieceDecoupe, PieceProfile, ProcedeCoupe, ProfileSection, ProfilImportDecoupe
from decoupe.services import devis_pieces, devis_profiles, formes, nomenclature, profiles
from decoupe.services import imbrication_devis as imb
from decoupe.services.apercu_svg import generer_svg_feuille_a_plat, generer_svg_piece
from decoupe.services.matiere import ErreurMatiere, bord_tole_piece_mm, prix_au_mm2
from decoupe.services.parametres import reglage
from technique.models import Matiere

from . import pieces_devis
from .moteur import ChiffrageError
from .models import Devis


MAX_FEUILLES_AFFICHEES = 40


def _nombre(valeur, defaut):
    try:
        return float(str(valeur).replace(",", "."))
    except (TypeError, ValueError):
        return float(defaut)


def _booleen(valeur, defaut):
    if valeur is None or valeur == "":
        return bool(defaut)
    return str(valeur).lower() in ("1", "true", "on", "oui")


def _bloc_groupe(groupe, choix, formats):
    """Contexte d'affichage de l'imbrication d'un groupe (choix : valeurs des commandes du panneau, sinon celles déjà retenues)."""
    pieces = groupe.pieces
    retenues = pieces[0] if pieces else None
    toles = imb.toles_possibles(groupe)
    tole = next((t for t in toles if str(t.pk) == str(choix.get("tole"))), None) if choix.get("tole") else None
    if tole is None and retenues is not None and retenues.tole_id and not choix:
        tole = next((t for t in toles if t.pk == retenues.tole_id), None)
    if tole is None and len(toles) == 1:
        tole = toles[0]
    # Bord de tôle : automatique (matière, épaisseur, procédé : voir matiere.bord_tole_mm) tant qu'il n'est pas imposé ou retenu.
    bord_auto = max((bord_tole_piece_mm(p) for p in pieces), default=5.0)
    retenue = retenues is not None and retenues.tole_id is not None
    if "marge" in choix and str(choix["marge"]).strip() != "":
        marge, marge_auto = _nombre(choix["marge"], bord_auto), False
    elif "marge" not in choix and retenue and not choix:
        marge, marge_auto = float(retenues.marge_bord_mm), False
    else:
        marge, marge_auto = bord_auto, True
    taux = max(0.0, min(100.0, _nombre(choix.get("chute"), retenues.taux_chute_recuperable if retenues else 0)))
    forme = _booleen(choix.get("forme"), retenues.imbrication_forme if retenues else True)
    sens = choix.get("sens") or (retenues.sens_imbrication if retenues else "longueur")
    if sens not in PieceDecoupe.SensImbrication.values:
        sens = "longueur"
    coin = choix.get("coin") or (retenues.coin_depart if retenues else "bas_gauche")
    if coin not in PieceDecoupe.CoinDepart.values:
        coin = "bas_gauche"
    bloc = {
        "groupe": groupe, "cle": groupe.cle, "toles": toles, "tole": tole, "marge": marge, "marge_auto": marge_auto, "chute": taux, "forme": forme, "formats": formats,
        "erreur": "", "avertissement": "", "epaisseurs_toles": [] if toles else imb.epaisseurs_toles_existantes(groupe),
        "lien_creer_tole": "" if toles else reverse("admin:technique_article_add") + f"?nature=matiere_premiere&matiere={groupe.matiere.pk}&epaisseur={groupe.epaisseur:g}&unite_cout=surface", "sens": sens, "coin": coin, "sens_choix": PieceDecoupe.SensImbrication.choices,
        "coin_choix": PieceDecoupe.CoinDepart.choices,
    }
    if not pieces:
        bloc["erreur"] = "Aucune pièce réalisable dans ce groupe."
        return bloc
    if tole is not None and formats:
        try:
            prix_au_mm2(tole, formats[0].largeur_mm, formats[0].longueur_mm)
        except ErreurMatiere as exc:
            bloc["avertissement"] = f"{exc} Le coût matière n'est pas calculé."
            bloc["avertissement_lien"] = (reverse("admin:technique_article_change", args=[tole.pk]), "Renseigner le coût de la tôle")
            tole = bloc["tole"] = None
    tout = _booleen(choix.get("tout"), False)
    obligatoires = {f.pk for f in formats if (retenues is not None and f.pk == retenues.format_tole_id) or str(f.pk) == str(choix.get("format"))}
    lignes, meilleur, non_calcules, message_paliers = imb.comparer_par_paliers(
        groupe, formats, marge, taux, tole, forme=forme, sens=sens, coin=coin, tout=tout, obligatoires=obligatoires,
    )
    bloc.update({"non_calcules": non_calcules, "message_paliers": message_paliers, "tout": tout})
    if meilleur is None:
        if formats and not imb.formats_pour_groupe(groupe, formats):
            bloc["erreur"] = (
                f"Aucun format de tôle n'est prévu pour {groupe.matiere.nom} : renseignez les familles ou nuances de vos formats "
                "(menu Formats de tôle) ou laissez-les vides pour toutes les matières."
            )
        elif formats and not imb.formats_compatibles(groupe.procede, formats):
            nom = "le laser" if groupe.procede == "laser" else "le jet d'eau"
            bloc["erreur"] = (
                f"Aucun format de tôle actif ne tient dans {nom} ({reglage(groupe.procede).libelle_capacite}). "
                "Ajoutez un format plus petit dans le menu « Formats de tôle »."
            )
        else:
            bloc["erreur"] = lignes[0][2] if lignes else "Aucun format de tôle actif (menu Formats de tôle)."
        return bloc
    voulu = next((f for f in formats if str(f.pk) == str(choix.get("format"))), None) if choix.get("format") else None
    if voulu is None and retenues is not None and retenues.format_tole_id and not choix:
        voulu = next((f for f in formats if f.pk == retenues.format_tole_id), None)
    format_choisi = voulu or meilleur
    resultat = next((r for f, r, _ in lignes if f.pk == format_choisi.pk and r is not None), None)
    if resultat is None:
        format_choisi = meilleur
        resultat = next(r for f, r, _ in lignes if f.pk == meilleur.pk)
    from .reglage import resume_groupe

    try:
        bloc["reglage"] = resume_groupe(groupe, resultat.nb_feuilles)
    except Exception:  # information : jamais bloquante pour l'imbrication
        bloc["reglage"] = None
    couleurs = {p.pk: imb.COULEURS[i % len(imb.COULEURS)] for i, p in enumerate(pieces)}
    par_id = {p.pk: p for p in pieces}
    feuilles = []
    for numero in range(1, min(resultat.nb_feuilles, MAX_FEUILLES_AFFICHEES) + 1):
        placees = [pl for pl in resultat.placements_affichage if pl.numero_feuille == numero]
        feuilles.append({
            "numero": numero, "nb_pieces": len(placees), "partielle": numero == resultat.nb_feuilles and resultat.nb_feuilles > 0,
            "svg": generer_svg_feuille_a_plat(
                resultat.largeur_x_mm, resultat.hauteur_y_mm,
                [(par_id[pl.piece_id], pl.x_mm, pl.y_mm, pl.rotation_deg, pl.miroir) for pl in placees], couleurs=couleurs,
                chute_bout=resultat.chute_bout if numero == resultat.nb_feuilles else None,
            ),
        })
    if resultat.avertissement_coin:
        bloc["avertissement"] = (bloc["avertissement"] + " " + resultat.avertissement_coin).strip()
    bloc.update({
        "lignes": [{"format": f, "resultat": r, "erreur": e, "meilleur": f.pk == meilleur.pk, "choisi": f.pk == format_choisi.pk} for f, r, e in lignes],
        "resultat": resultat, "format": format_choisi, "feuilles": feuilles,
        "feuilles_masquees": max(resultat.nb_feuilles - MAX_FEUILLES_AFFICHEES, 0),
        "legende": [(p, couleurs[p.pk]) for p in pieces],
        "retenu": bool(retenues and retenues.tole_id == (tole.pk if tole else None) and retenues.format_tole_id == format_choisi.pk
                       and retenues.marge_bord_mm == marge and float(retenues.taux_chute_recuperable) == taux
                       and retenues.imbrication_forme == forme and retenues.sens_imbrication == sens and retenues.coin_depart == resultat.coin_depart),
    })
    return bloc


def _carte(request, piece, editable=True, alimenter=False):
    apercu = ""
    if piece.contour_json:
        apercu = generer_svg_piece(
            piece.largeur_mm, piece.hauteur_mm, piece.contour_json.get("exterieur") or [], piece.contour_json.get("trous") or [],
            (piece.gravure_json or {}).get("traits"), (piece.pliage_json or {}).get("traits"),
        )
    return render_to_string(
        "admin/chiffrage/devis/_carte_piece.html",
        {
            "piece": piece, "nom_fichier": (piece.fichier_source.name or "").rsplit("/", 1)[-1], "verdict": devis_pieces.verdict(piece, alimenter=alimenter), "apercu": apercu, "editable": editable,
            "matieres": Matiere.objects.order_by("nom"), "procedes": ProcedeCoupe.choices, "gaz": GazCoupe.choices,
            "rotations": [("", "Aucune (sens imposé)"), *[(str(v), l) for v, l in PieceDecoupe.PasRotation.choices]],
            "forme_json": json.dumps(piece.parametres_forme) if piece.parametres_forme else "",
            "url_enregistrer": reverse("admin:chiffrage_devis_piece_enregistrer", args=[piece.devis_id, piece.pk]),
            "url_supprimer": reverse("admin:chiffrage_devis_piece_supprimer", args=[piece.devis_id, piece.pk]),
            "operations": _operations(request, piece),
        },
        request=request,
    )


def _operations(request, piece):
    """Éditeur des opérations de fabrication (gamme de l'article) : None si la pièce n'a pas d'article ou si l'utilisateur ne voit pas les gammes."""
    if not piece.article_id or not request.user.has_perm("technique.view_gamme"):
        return None
    autres = [{"ref": p.article_id, "nom": p.nom} for p in PieceDecoupe.objects.filter(devis_id=piece.devis_id, article__isnull=False).exclude(pk=piece.pk).order_by("nom")]
    return {
        "url": reverse("gamme_editeur", args=[piece.article_id]),
        "editable": request.user.has_perm("technique.add_gamme") and request.user.has_perm("technique.change_gamme"),
        "autres": json.dumps(autres),
    }


def _catalogue_profils():
    """Sections de profilés par famille pour la bibliothèque de formes (menus et masses)."""
    familles = []
    for valeur, libelle in ProfileSection.Famille.choices:
        sections = list(ProfileSection.objects.filter(famille=valeur).order_by("ordre", "designation"))
        familles.append({
            "cle": valeur, "libelle": libelle,
            "sections": [{"id": s.pk, "designation": s.designation, "masse": s.masse_lineique, "barre": s.longueur_barre_mm, "prix": s.article_id is not None} for s in sections],
            "non_verifie": any(not s.verifie for s in sections),
        })
    return familles


def _carte_profil(request, piece, editable=True):
    return render_to_string(
        "admin/chiffrage/devis/_carte_profil.html",
        {
            "piece": piece, "editable": editable, "apercu": profiles.svg_debit(piece.longueur_mm, piece.section.hauteur_mm, piece.coupe_a_deg, piece.coupe_b_deg),
            "section_svg": profiles.svg_section(piece.section, 64),
            "sections": ProfileSection.objects.filter(famille=piece.section.famille).order_by("ordre", "designation"),
            "coupes": COUPES, "url_enregistrer": reverse("admin:chiffrage_devis_profil_enregistrer", args=[piece.devis_id, piece.pk]),
            "url_supprimer": reverse("admin:chiffrage_devis_profil_supprimer", args=[piece.devis_id, piece.pk]),
        },
        request=request,
    )


COUPES = [(90, "Droite (90°)"), (45, "Biais 45°"), (60, "Biais 60°"), (30, "Biais 30°"), (22.5, "Biais 22,5°")]


def profiles_lien(section):
    return pieces_devis.lien_prix_profil(section)


def _bloc_profil(section, pieces, choix):
    """Contexte d'affichage de l'imbrication des débits d'une section dans ses barres."""
    premiere = pieces[0]
    trait = max(0.0, _nombre(choix.get("trait"), premiere.trait_scie_mm))
    marge = max(0.0, _nombre(choix.get("marge"), premiere.marge_bout_mm))
    taux = max(0.0, min(100.0, _nombre(choix.get("chute"), premiere.taux_chute_recuperable)))
    bloc = {"section": section, "cle": f"profil|{section.pk}", "pieces": pieces, "trait": trait, "marge": marge, "chute": taux, "erreur": "", "resultat": None}
    try:
        r = profiles.imbriquer_barres(pieces, section, None, trait, marge, taux)
    except profiles.ErreurProfile as exc:
        bloc["erreur"] = str(exc)
        return bloc
    couleurs = {p.pk: profiles.COULEURS[i % len(profiles.COULEURS)] for i, p in enumerate(pieces)}
    bloc.update({
        "resultat": r, "barres": profiles.svg_barres(r, section, {p.pk: p for p in pieces}, couleurs), "legende": [(p, couleurs[p.pk]) for p in pieces],
        "retenu": all(p.trait_scie_mm == trait and p.marge_bout_mm == marge and float(p.taux_chute_recuperable) == taux for p in pieces),
        "avertissement": r.erreur_prix,
        "avertissement_lien": (lambda l: (l[0], l[1]) if l else None)(profiles_lien(section)) if r.erreur_prix else None,
    })
    return bloc


class PiecesDevisMixin:
    """À mélanger à DevisAdmin : routes AJAX du panneau et contexte de la fiche."""

    def urls_pieces(self):
        vue = self.admin_site.admin_view
        return [
            path("<str:numero>/pieces/importer/", vue(self.piece_importer_view), name="chiffrage_devis_pieces_importer"),
            path("<str:numero>/pieces/<int:piece_id>/enregistrer/", vue(self.piece_enregistrer_view), name="chiffrage_devis_piece_enregistrer"),
            path("<str:numero>/pieces/<int:piece_id>/supprimer/", vue(self.piece_supprimer_view), name="chiffrage_devis_piece_supprimer"),
            path("<str:numero>/pieces/formes/apercu/", vue(self.forme_apercu_view), name="chiffrage_devis_formes_apercu"),
            path("<str:numero>/pieces/formes/ajouter/", vue(self.forme_ajouter_view), name="chiffrage_devis_formes_ajouter"),
            path("<str:numero>/profils/apercu/", vue(self.profil_apercu_view), name="chiffrage_devis_profils_apercu"),
            path("<str:numero>/profils/ajouter/", vue(self.profil_ajouter_view), name="chiffrage_devis_profils_ajouter"),
            path("<str:numero>/profils/retenir/", vue(self.profil_retenir_view), name="chiffrage_devis_profils_retenir"),
            path("<str:numero>/profils/<int:piece_id>/enregistrer/", vue(self.profil_enregistrer_view), name="chiffrage_devis_profil_enregistrer"),
            path("<str:numero>/profils/<int:piece_id>/supprimer/", vue(self.profil_supprimer_view), name="chiffrage_devis_profil_supprimer"),
            path("<str:numero>/pieces/imbrication/", vue(self.piece_imbrication_view), name="chiffrage_devis_pieces_imbrication"),
            path("<str:numero>/pieces/imbrication/retenir/", vue(self.piece_retenir_view), name="chiffrage_devis_pieces_retenir"),
            path("<str:numero>/pieces/ajouter-au-devis/", vue(self.piece_ajouter_view), name="chiffrage_devis_pieces_ajouter"),
        ]

    # --- contexte de la fiche -------------------------------------------------------------------------------------
    def contexte_panneau_pieces(self, request, devis):
        from .admin import devis_verrouille

        if devis is None or devis.pk is None:
            return {"panneau_pieces": {"actif": False, "nouveau": True}}
        verrouille = devis_verrouille(devis)
        autorise = request.user.has_perm("decoupe.view_piecedecoupe")
        if not autorise:
            return {"panneau_pieces": {"actif": False}}
        editable = (
            not verrouille and self.has_change_permission(request, devis) and request.user.has_perm("decoupe.add_piecedecoupe")
            and request.user.has_perm("technique.add_article")
        )
        return {"panneau_pieces": {
            "actif": True, "editable": editable, "verrouille": verrouille,
            "cartes": [_carte(request, p, editable) for p in devis_pieces.pieces_du_devis(devis)],
            "url_importer": reverse("admin:chiffrage_devis_pieces_importer", args=[devis.pk]),
            "url_imbrication": reverse("admin:chiffrage_devis_pieces_imbrication", args=[devis.pk]),
            "url_retenir": reverse("admin:chiffrage_devis_pieces_retenir", args=[devis.pk]),
            "url_ajouter": reverse("admin:chiffrage_devis_pieces_ajouter", args=[devis.pk]),
            "url_forme_apercu": reverse("admin:chiffrage_devis_formes_apercu", args=[devis.pk]),
            "url_forme_ajouter": reverse("admin:chiffrage_devis_formes_ajouter", args=[devis.pk]),
            "catalogue_formes": ({**formes.catalogue(), "profils": _catalogue_profils()}) if editable else None,
            "cartes_profils": [_carte_profil(request, p, editable) for p in devis_profiles.pieces_du_devis(devis)],
            "url_profil_apercu": reverse("admin:chiffrage_devis_profils_apercu", args=[devis.pk]),
            "url_profil_ajouter": reverse("admin:chiffrage_devis_profils_ajouter", args=[devis.pk]),
            "url_profil_retenir": reverse("admin:chiffrage_devis_profils_retenir", args=[devis.pk]),
            "profils": ProfilImportDecoupe.objects.order_by("nom"),
        }}

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        extra_context = dict(extra_context or {})
        devis = Devis.objects.filter(pk=object_id).first() if object_id else None
        extra_context.update(self.contexte_panneau_pieces(request, devis))
        extra_context.update(self.contexte_lignes_detail(request, devis))
        return super().changeform_view(request, object_id, form_url, extra_context)

    def contexte_lignes_detail(self, request, devis):
        """Lignes fabriquées dépliables (nomenclature et opérations de l'article), modifiables tant que le devis n'est pas validé."""
        from .admin import devis_verrouille
        from technique.models import Article

        if devis is None or devis.pk is None:
            return {}
        u = request.user
        if not (u.has_perm("technique.view_gamme") and u.has_perm("technique.view_nomenclature")):
            return {}
        lignes = list(devis.lignes.filter(article__nature=Article.Nature.FABRIQUE).select_related("article").order_by("pk"))
        editable = (not devis_verrouille(devis) and all(u.has_perm(f"technique.{a}_{m}") for m in ("gamme", "nomenclature") for a in ("add", "change"))
                    and u.has_perm("technique.delete_nomenclature"))
        return {"lignes_detail": lignes, "editable_detail": editable}

    # --- vues AJAX ------------------------------------------------------------------------------------------------
    def _devis_modifiable(self, request, numero):
        from .admin import devis_verrouille

        devis = get_object_or_404(Devis, pk=numero)
        if not (self.has_change_permission(request, devis) and request.user.has_perm("decoupe.add_piecedecoupe")
                and request.user.has_perm("technique.add_article")):
            raise PermissionDenied
        if devis_verrouille(devis):
            return devis, JsonResponse({"detail": "Devis validé, donc verrouillé : repassez-le en brouillon pour modifier ses pièces."}, status=409)
        return devis, None

    @method_decorator(require_POST)
    def piece_importer_view(self, request, numero):
        devis, refus = self._devis_modifiable(request, numero)
        if refus:
            return refus
        fichier = request.FILES.get("fichier")
        if fichier is None:
            return JsonResponse({"detail": "Aucun fichier reçu."}, status=400)
        try:
            piece = devis_pieces.importer_pour_devis(
                devis, fichier, profil_import_id=request.POST.get("profil_import") or None,
                procede=request.POST.get("procede") or ProcedeCoupe.LASER,
            )
        except devis_pieces.ErreurPieceDevis as exc:
            return JsonResponse({"detail": str(exc)}, status=400)
        return JsonResponse({"html": _carte(request, piece), "piece_id": piece.pk})

    @method_decorator(require_POST)
    def profil_apercu_view(self, request, numero):
        if not request.user.has_perm("decoupe.view_piecedecoupe"):
            raise PermissionDenied
        donnees, erreur = self._saisie_profil(request)
        if erreur:
            return erreur
        section = ProfileSection.objects.filter(pk=donnees.get("section")).first()
        if section is None:
            return JsonResponse({"detail": "Choisissez une section de profilé."}, status=400)
        try:
            longueur, a, b = (float(str(donnees.get(k) or d).replace(",", ".")) for k, d in (("longueur", 0), ("coupe_a", 90), ("coupe_b", 90)))
            piece = PieceProfile(section=section, longueur_mm=longueur, coupe_a_deg=a, coupe_b_deg=b)
            profiles.verifier_piece(piece)
        except (ValueError, profiles.ErreurProfile) as exc:
            return JsonResponse({"detail": str(exc) if not isinstance(exc, ValueError) else "Longueur et angles sont des nombres."}, status=400)
        prix = ""
        try:
            prix = f"{section.prix_au_metre():.2f} €/m"
        except profiles.ErreurPrixProfile as exc:
            prix = ""
        return JsonResponse({
            "svg": profiles.svg_debit(longueur, section.hauteur_mm, a, b), "section_svg": profiles.svg_section(section),
            "nom": devis_profiles.nom_suggere(section, longueur), "masse": round(piece.masse_kg, 2), "masse_lineique": section.masse_lineique, "prix": prix,
        })

    @staticmethod
    def _saisie_profil(request):
        try:
            donnees = json.loads(request.body or b"{}")
        except ValueError:
            return None, JsonResponse({"detail": "Requête illisible."}, status=400)
        if not isinstance(donnees, dict):
            return None, JsonResponse({"detail": "Requête illisible."}, status=400)
        return donnees, None

    @method_decorator(require_POST)
    def profil_ajouter_view(self, request, numero):
        devis, refus = self._devis_modifiable(request, numero)
        if refus:
            return refus
        donnees, erreur = self._saisie_profil(request)
        if erreur:
            return erreur
        try:
            piece = devis_profiles.creer(
                devis, donnees.get("section"), donnees.get("longueur"), donnees.get("coupe_a"), donnees.get("coupe_b"), donnees.get("quantite"),
            )
        except devis_pieces.ErreurPieceDevis as exc:
            return JsonResponse({"detail": str(exc)}, status=400)
        return JsonResponse({"html": _carte_profil(request, piece), "piece_id": piece.pk})

    @method_decorator(require_POST)
    def profil_enregistrer_view(self, request, numero, piece_id):
        devis, refus = self._devis_modifiable(request, numero)
        if refus:
            return refus
        piece = get_object_or_404(devis.pieces_profile, pk=piece_id)
        try:
            devis_profiles.modifier(piece, request.POST.dict())
        except devis_pieces.ErreurPieceDevis as exc:
            return JsonResponse({"detail": str(exc)}, status=400)
        piece.refresh_from_db()
        return JsonResponse({"html": _carte_profil(request, piece), "piece_id": piece.pk})

    @method_decorator(require_POST)
    def profil_supprimer_view(self, request, numero, piece_id):
        devis, refus = self._devis_modifiable(request, numero)
        if refus:
            return refus
        conserve = devis_profiles.supprimer(get_object_or_404(devis.pieces_profile, pk=piece_id))
        return JsonResponse({"ok": True, "article_conserve": conserve})

    @method_decorator(require_POST)
    def profil_retenir_view(self, request, numero):
        """Retient trait de scie, chute de tête et chute récupérable d'une section : reportés sur ses débits."""
        devis, refus = self._devis_modifiable(request, numero)
        if refus:
            return refus
        donnees, erreur = self._saisie_profil(request)
        if erreur:
            return erreur
        pieces = list(devis.pieces_profile.filter(section_id=str(donnees.get("section") or "").replace("profil|", "") or 0))
        if not pieces:
            return JsonResponse({"detail": "Section introuvable : rafraîchissez le panneau."}, status=404)
        trait, marge = max(0.0, _nombre(donnees.get("trait"), 3)), max(0.0, _nombre(donnees.get("marge"), 0))
        taux = max(0.0, min(100.0, _nombre(donnees.get("chute"), 0)))
        for p in pieces:
            p.trait_scie_mm, p.marge_bout_mm, p.taux_chute_recuperable = trait, marge, taux
            p.save(update_fields=["trait_scie_mm", "marge_bout_mm", "taux_chute_recuperable"])
        return JsonResponse({"ok": True})

    @staticmethod
    def _saisie_forme(request):
        try:
            donnees = json.loads(request.body or b"{}")
        except ValueError:
            return None, JsonResponse({"detail": "Requête illisible."}, status=400)
        cotes = donnees.get("cotes")
        if not isinstance(donnees.get("famille"), str) or not isinstance(cotes, dict):
            return None, JsonResponse({"detail": "Choisissez une forme."}, status=400)
        return donnees, None

    @method_decorator(require_POST)
    def forme_apercu_view(self, request, numero):
        """Aperçu d'une forme paramétrique (SVG, dimensions, nom) sans rien enregistrer."""
        if not request.user.has_perm("decoupe.view_piecedecoupe"):
            raise PermissionDenied
        donnees, erreur = self._saisie_forme(request)
        if erreur:
            return erreur
        try:
            contour = formes.construire(donnees["famille"], donnees["cotes"])
            nom = formes.nom_suggere(donnees["famille"], donnees["cotes"])
        except formes.ErreurForme as exc:
            return JsonResponse({"detail": str(exc)}, status=400)
        largeur, hauteur = contour.dimensions
        svg = generer_svg_piece(largeur, hauteur, contour.exterieur, contour.trous, None, None)
        return JsonResponse({"svg": svg, "nom": nom, "largeur": round(largeur, 2), "hauteur": round(hauteur, 2), "trous": len(contour.trous)})

    @method_decorator(require_POST)
    def forme_ajouter_view(self, request, numero):
        """Crée une pièce depuis la bibliothèque de formes ou, avec `piece_id`, modifie les cotes d'une pièce paramétrique."""
        devis, refus = self._devis_modifiable(request, numero)
        if refus:
            return refus
        donnees, erreur = self._saisie_forme(request)
        if erreur:
            return erreur
        try:
            if donnees.get("piece_id"):
                piece = get_object_or_404(devis.pieces_decoupe, pk=donnees["piece_id"])
                devis_pieces.modifier_forme(piece, donnees["famille"], donnees["cotes"])
            else:
                piece = devis_pieces.creer_depuis_forme(
                    devis, donnees["famille"], donnees["cotes"], quantite=donnees.get("quantite") or 1,
                    procede=donnees.get("procede") or ProcedeCoupe.LASER,
                )
        except (devis_pieces.ErreurPieceDevis, ValueError) as exc:
            return JsonResponse({"detail": str(exc)}, status=400)
        piece.refresh_from_db()
        return JsonResponse({"html": _carte(request, piece), "piece_id": piece.pk})

    @method_decorator(require_POST)
    def piece_enregistrer_view(self, request, numero, piece_id):
        devis, refus = self._devis_modifiable(request, numero)
        if refus:
            return refus
        piece = get_object_or_404(devis.pieces_decoupe, pk=piece_id)
        try:
            devis_pieces.appliquer_reglages(piece, request.POST.dict())
        except devis_pieces.ErreurPieceDevis as exc:
            return JsonResponse({"detail": str(exc)}, status=400)
        piece.refresh_from_db()
        peut_gamme = request.user.has_perm("technique.add_gamme") and request.user.has_perm("technique.change_gamme")
        return JsonResponse({"html": _carte(request, piece, alimenter=peut_gamme), "piece_id": piece.pk})

    @method_decorator(require_POST)
    def piece_supprimer_view(self, request, numero, piece_id):
        devis, refus = self._devis_modifiable(request, numero)
        if refus:
            return refus
        piece = get_object_or_404(devis.pieces_decoupe, pk=piece_id)
        conserve = devis_pieces.supprimer(piece)
        return JsonResponse({"ok": True, "article_conserve": conserve})

    @method_decorator(require_POST)
    def piece_imbrication_view(self, request, numero):
        """Imbrication de chaque groupe (matière, épaisseur, procédé) du devis : calcul seul, rien n'est enregistré."""
        from .admin import devis_verrouille

        devis = get_object_or_404(Devis, pk=numero)
        if not (self.has_view_permission(request, devis) and request.user.has_perm("decoupe.view_piecedecoupe")):
            raise PermissionDenied
        try:
            choix = json.loads(request.body or "{}").get("choix") or {}
        except (json.JSONDecodeError, AttributeError):
            return JsonResponse({"detail": "Requête invalide."}, status=400)
        groupes, a_regler = imb.grouper(devis_pieces.pieces_du_devis(devis))
        formats = imb.formats_actifs()
        editable = not devis_verrouille(devis) and self.has_change_permission(request, devis)
        peut_gamme = editable and request.user.has_perm("technique.add_gamme") and request.user.has_perm("technique.change_gamme")
        blocs = [_bloc_groupe(g, choix.get(g.cle) or {}, formats) for g in groupes]
        blocs_profils = [_bloc_profil(sec, pcs, choix.get(f"profil|{sec.pk}") or {}) for sec, pcs in profiles.groupes(devis_profiles.pieces_du_devis(devis))]
        html = render_to_string(
            "admin/chiffrage/devis/_imbrication.html",
            {"blocs": blocs, "blocs_profils": blocs_profils, "a_regler": a_regler, "editable": editable, "sans_format": not formats,
             "chiffrage": pieces_devis.apercu(devis, avec_gamme=peut_gamme), "peut_ajouter": editable and request.user.has_perm("chiffrage.add_devisligne")},
            request=request,
        )
        return JsonResponse({"html": html})

    @method_decorator(require_POST)
    def piece_retenir_view(self, request, numero):
        """Retient tôle, format, marge et chute récupérable d'un groupe : reportés sur ses pièces (étape suivante : chiffrage)."""
        devis, refus = self._devis_modifiable(request, numero)
        if refus:
            return refus
        try:
            donnees = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return JsonResponse({"detail": "Requête invalide."}, status=400)
        groupes, _ = imb.grouper(devis_pieces.pieces_du_devis(devis))
        groupe = next((g for g in groupes if g.cle == donnees.get("cle")), None)
        if groupe is None:
            return JsonResponse({"detail": "Groupe introuvable : rafraîchissez le panneau."}, status=404)
        from decoupe.models import FormatTole

        format_tole = FormatTole.objects.filter(pk=donnees.get("format"), actif=True).first()
        if format_tole is None:
            return JsonResponse({"detail": "Choisissez un format de tôle."}, status=400)
        compatible, motif = imb.format_compatible(format_tole, groupe.procede)
        if not compatible:
            return JsonResponse({"detail": f"{format_tole} : {motif}"}, status=400)
        tole = next((t for t in imb.toles_possibles(groupe) if str(t.pk) == str(donnees.get("tole"))), None) if donnees.get("tole") else None
        marge = max(0.0, _nombre(donnees.get("marge"), 5))
        taux = max(0.0, min(100.0, _nombre(donnees.get("chute"), 0)))
        forme = _booleen(donnees.get("forme"), True)
        sens = donnees.get("sens") if donnees.get("sens") in PieceDecoupe.SensImbrication.values else "longueur"
        coin = donnees.get("coin") if donnees.get("coin") in PieceDecoupe.CoinDepart.values else "bas_gauche"
        for piece in groupe.pieces:
            piece.tole, piece.format_tole, piece.marge_bord_mm, piece.taux_chute_recuperable = tole, format_tole, marge, taux
            piece.imbrication_forme, piece.sens_imbrication, piece.coin_depart = forme, sens, coin
            piece.save(update_fields=["tole", "format_tole", "marge_bord_mm", "taux_chute_recuperable", "imbrication_forme", "sens_imbrication", "coin_depart"])
            if request.user.has_perm("technique.add_nomenclature") and request.user.has_perm("technique.change_nomenclature"):
                nomenclature.alimenter_piece(piece)
            article = piece.article
            if tole is not None and article is not None and article.taux_marge_defaut is None and tole.taux_marge_defaut is not None:
                article.taux_marge_defaut = tole.taux_marge_defaut  # l'article fabriqué reprend la marge de sa tôle
                article.save(update_fields=["taux_marge_defaut"])
        return JsonResponse({"ok": True})

    @method_decorator(require_POST)
    def piece_ajouter_view(self, request, numero):
        """Ajoute (ou met à jour) aux lignes du devis les pièces prêtes, avec leur quantité, et les chiffre."""
        devis, refus = self._devis_modifiable(request, numero)
        if refus:
            return refus
        if not request.user.has_perm("chiffrage.add_devisligne"):
            raise PermissionDenied
        try:
            resultats = pieces_devis.ajouter_au_devis(devis)
        except ChiffrageError as exc:
            return JsonResponse({"detail": str(exc)}, status=409)
        return JsonResponse({
            "resultats": [{"nom": i.piece.nom, "etat": etat, "raison": i.raison} for i, etat in resultats],
            "ajoutees": sum(1 for _, e in resultats if e == "ajoutée"),
            "mises_a_jour": sum(1 for _, e in resultats if e == "mise à jour"),
        })
