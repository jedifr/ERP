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

from decoupe.models import GazCoupe, ProcedeCoupe, ProfilImportDecoupe
from decoupe.services import devis_pieces
from decoupe.services import imbrication_devis as imb
from decoupe.services.apercu_svg import generer_svg_feuille, generer_svg_piece
from decoupe.services.matiere import ErreurMatiere, prix_au_mm2
from technique.models import Matiere

from . import pieces_devis
from .moteur import ChiffrageError
from .models import Devis


def _nombre(valeur, defaut):
    try:
        return float(str(valeur).replace(",", "."))
    except (TypeError, ValueError):
        return float(defaut)


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
    marge = _nombre(choix.get("marge"), retenues.marge_bord_mm if retenues else 5)
    taux = max(0.0, min(100.0, _nombre(choix.get("chute"), retenues.taux_chute_recuperable if retenues else 0)))
    bloc = {"groupe": groupe, "cle": groupe.cle, "toles": toles, "tole": tole, "marge": marge, "chute": taux, "formats": formats, "erreur": "", "avertissement": ""}
    if not pieces:
        bloc["erreur"] = "Aucune pièce réalisable dans ce groupe."
        return bloc
    if tole is not None and formats:
        try:
            prix_au_mm2(tole, formats[0].largeur_mm, formats[0].longueur_mm)
        except ErreurMatiere as exc:
            bloc["avertissement"] = f"{exc} Le coût matière n'est pas calculé."
            tole = bloc["tole"] = None
    lignes, meilleur = imb.comparer_formats(groupe, formats, marge, taux, tole)
    if meilleur is None:
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
    couleurs = {p.pk: imb.COULEURS[i % len(imb.COULEURS)] for i, p in enumerate(pieces)}
    par_id = {p.pk: p for p in pieces}
    svg = generer_svg_feuille(
        format_choisi.largeur_mm, format_choisi.longueur_mm,
        [(par_id[pl.piece_id], pl.x_mm, pl.y_mm, pl.rotation_deg, pl.miroir) for pl in resultat.placements if pl.numero_feuille == 1],
        couleurs=couleurs,
    )
    bloc.update({
        "lignes": [{"format": f, "resultat": r, "erreur": e, "meilleur": f.pk == meilleur.pk, "choisi": f.pk == format_choisi.pk} for f, r, e in lignes],
        "resultat": resultat, "format": format_choisi, "svg": svg, "autres_feuilles": max(resultat.nb_feuilles - 1, 0),
        "legende": [(p, couleurs[p.pk]) for p in pieces],
        "retenu": bool(retenues and retenues.tole_id == (tole.pk if tole else None) and retenues.format_tole_id == format_choisi.pk
                       and retenues.marge_bord_mm == marge and float(retenues.taux_chute_recuperable) == taux),
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
            "url_enregistrer": reverse("admin:chiffrage_devis_piece_enregistrer", args=[piece.devis_id, piece.pk]),
            "url_supprimer": reverse("admin:chiffrage_devis_piece_supprimer", args=[piece.devis_id, piece.pk]),
        },
        request=request,
    )


class PiecesDevisMixin:
    """À mélanger à DevisAdmin : routes AJAX du panneau et contexte de la fiche."""

    def urls_pieces(self):
        vue = self.admin_site.admin_view
        return [
            path("<str:numero>/pieces/importer/", vue(self.piece_importer_view), name="chiffrage_devis_pieces_importer"),
            path("<str:numero>/pieces/<int:piece_id>/enregistrer/", vue(self.piece_enregistrer_view), name="chiffrage_devis_piece_enregistrer"),
            path("<str:numero>/pieces/<int:piece_id>/supprimer/", vue(self.piece_supprimer_view), name="chiffrage_devis_piece_supprimer"),
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
            "profils": ProfilImportDecoupe.objects.order_by("nom"),
        }}

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        extra_context = dict(extra_context or {})
        devis = Devis.objects.filter(pk=object_id).first() if object_id else None
        extra_context.update(self.contexte_panneau_pieces(request, devis))
        return super().changeform_view(request, object_id, form_url, extra_context)

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
        blocs = [_bloc_groupe(g, choix.get(g.cle) or {}, formats) for g in groupes]
        html = render_to_string(
            "admin/chiffrage/devis/_imbrication.html",
            {"blocs": blocs, "a_regler": a_regler, "editable": editable, "sans_format": not formats,
             "chiffrage": pieces_devis.apercu(devis), "peut_ajouter": editable and request.user.has_perm("chiffrage.add_devisligne")},
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
        tole = next((t for t in imb.toles_possibles(groupe) if str(t.pk) == str(donnees.get("tole"))), None) if donnees.get("tole") else None
        marge = max(0.0, _nombre(donnees.get("marge"), 5))
        taux = max(0.0, min(100.0, _nombre(donnees.get("chute"), 0)))
        for piece in groupe.pieces:
            piece.tole, piece.format_tole, piece.marge_bord_mm, piece.taux_chute_recuperable = tole, format_tole, marge, taux
            piece.save(update_fields=["tole", "format_tole", "marge_bord_mm", "taux_chute_recuperable"])
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
