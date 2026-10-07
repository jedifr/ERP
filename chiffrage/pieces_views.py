"""Panneau « Pièces à découper » de la fiche devis : import de DXF/DWG, réglages par pièce, verdict immédiat.

Les vues sont appelées en AJAX par chiffrage/devis_pieces.js ; chaque carte de pièce est rendue côté serveur
(admin/chiffrage/devis/_carte_piece.html), la même vue servant à l'affichage initial et aux mises à jour."""

from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.urls import path, reverse
from django.utils.decorators import method_decorator
from django.views.decorators.http import require_POST

from decoupe.models import GazCoupe, ProcedeCoupe, ProfilImportDecoupe
from decoupe.services import devis_pieces
from decoupe.services.apercu_svg import generer_svg_piece
from technique.models import Matiere

from .models import Devis


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
