import json

from django.contrib import admin
from django.http import Http404, HttpResponse, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import path, reverse
from unfold.admin import ModelAdmin

from comptes.historique import HistoriqueLectureSeule

from . import contextes, moteur
from .defaults import CSS_CANVAS, blocs_editeur, modele_par_defaut
from .models import ModeleDocument
from .rendu import rendre

T = ModeleDocument.Type
TAILLE_MAX = 1_500_000


def _objets_apercu(type_document):
    """Derniers documents réels, pour prévisualiser le modèle avec de vraies données."""
    from chiffrage.models import Commande, Devis, Livraison, OrdreFabrication

    if type_document == T.DEVIS:
        return [(d.pk, f"{d.numero} — {d.client.raison_sociale}") for d in Devis.objects.select_related("client").order_by("-date_creation")[:30]]
    if type_document == T.BON_LIVRAISON:
        return [(l.pk, f"{l.numero} — {l.commande_id}") for l in Livraison.objects.order_by("-date_livraison")[:30]]
    if type_document == T.FICHE_FABRICATION:
        return [(o.pk, f"{o.numero} — {o.article_id}") for o in OrdreFabrication.objects.order_by("-date_lancement")[:30]]
    return [(c.pk, f"{c.numero} — {c.client.raison_sociale}") for c in Commande.objects.select_related("client").order_by("-date_commande")[:30]]


def _objet(type_document, pk):
    from chiffrage.models import Commande, Devis, Livraison, OrdreFabrication

    modele = {T.DEVIS: Devis, T.BON_LIVRAISON: Livraison, T.FICHE_FABRICATION: OrdreFabrication}.get(type_document, Commande)
    return get_object_or_404(modele, pk=pk)


@admin.register(ModeleDocument)
class ModeleDocumentAdmin(HistoriqueLectureSeule, ModelAdmin):
    """Éditeur visuel (GrapesJS) des documents de vente. Réservé aux superutilisateurs : un modèle est
    du HTML qui sera rendu côté serveur, c'est donc un droit d'administration."""

    list_display = ["type_document", "actif", "version", "modifie_le", "modifie_par"]
    list_display_links = ["type_document"]
    ordering = ["type_document"]

    # --- droits : superutilisateur seulement, pas d'ajout ni de suppression (un modèle par document) -----------
    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        if request.user.is_superuser:
            for type_document in T.values:  # les cinq modèles existent toujours (mise en page par défaut, inactifs)
                ModeleDocument.pour(type_document)
        return super().changelist_view(request, extra_context)

    # --- éditeur --------------------------------------------------------------------------------------------
    def get_urls(self):
        urls = [
            path("<int:pk>/enregistrer/", self.admin_site.admin_view(self.enregistrer_view), name="documents_modele_enregistrer"),
            path("<int:pk>/apercu/", self.admin_site.admin_view(self.apercu_view), name="documents_modele_apercu"),
            path("<int:pk>/defaut/", self.admin_site.admin_view(self.defaut_view), name="documents_modele_defaut"),
            path("<int:pk>/objets/", self.admin_site.admin_view(self.objets_view), name="documents_modele_objets"),
        ]
        return urls + super().get_urls()

    def change_view(self, request, object_id, form_url="", extra_context=None):
        if not request.user.is_superuser:
            return self._refuser(request)
        modele = get_object_or_404(ModeleDocument, pk=object_id)
        variables = [
            {"groupe": groupe, "variables": [{"chemin": c, "libelle": l} for c, l in liste]}
            for groupe, liste in contextes.VARIABLES[modele.type_document]
        ]
        return TemplateResponse(
            request,
            "documents/editeur.html",
            {
                **self.admin_site.each_context(request),
                "title": f"Modèle : {modele}",
                "modele": modele,
                "historique_url": reverse("admin:documents_modeledocument_history", args=[modele.pk]),
                "liste_url": reverse("admin:documents_modeledocument_changelist"),
                "configuration": {
                    "projet": modele.projet, "html": modele.html, "css": modele.css, "actif": modele.actif,
                    "variables": variables, "blocs": blocs_editeur(modele.type_document), "cssCanvas": CSS_CANVAS,
                    "urls": {
                        "enregistrer": reverse("admin:documents_modele_enregistrer", args=[modele.pk]),
                        "apercu": reverse("admin:documents_modele_apercu", args=[modele.pk]),
                        "defaut": reverse("admin:documents_modele_defaut", args=[modele.pk]),
                        "objets": reverse("admin:documents_modele_objets", args=[modele.pk]),
                    },
                },
            },
        )

    def _refuser(self, request):
        from django.core.exceptions import PermissionDenied

        raise PermissionDenied

    def _modele(self, request, pk):
        if not request.user.is_superuser:
            self._refuser(request)
        return get_object_or_404(ModeleDocument, pk=pk)

    @staticmethod
    def _corps(request):
        try:
            donnees = json.loads(request.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
        return donnees if isinstance(donnees, dict) else None

    def enregistrer_view(self, request, pk):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        modele = self._modele(request, pk)
        donnees = self._corps(request)
        if donnees is None:
            return JsonResponse({"detail": "Requête illisible."}, status=400)
        html, css, projet = (str(donnees.get(c, "")) for c in ("html", "css", "projet"))
        if max(len(html), len(css), len(projet)) > TAILLE_MAX:
            return JsonResponse({"detail": "Le modèle est trop volumineux (images trop lourdes ?)."}, status=400)
        # Le modèle doit pouvoir être rendu : on le vérifie avec des données d'exemple avant de l'enregistrer.
        try:
            _, inconnues = rendre(html, css, contextes.contexte_exemple(modele.type_document))
        except Exception as exc:
            return JsonResponse({"detail": f"Ce modèle ne peut pas être rendu en PDF : {exc}"}, status=400)
        modele.html, modele.css, modele.projet = html, css, projet
        modele.actif = bool(donnees.get("actif", modele.actif))
        modele.version += 1
        modele.modifie_par = request.user
        modele._change_reason = ("Modèle activé" if modele.actif else "Modèle enregistré")[:100]
        modele.save()
        return JsonResponse({"ok": True, "version": modele.version, "actif": modele.actif, "inconnues": inconnues})

    def apercu_view(self, request, pk):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        from chiffrage.documents import DocumentError

        modele = self._modele(request, pk)
        donnees = self._corps(request)
        if donnees is None:
            return JsonResponse({"detail": "Requête illisible."}, status=400)
        html, css = str(donnees.get("html", "")), str(donnees.get("css", ""))
        try:
            if donnees.get("objet"):
                contexte = contextes.CONTEXTES[modele.type_document](_objet(modele.type_document, donnees["objet"]))
            else:
                contexte = contextes.contexte_exemple(modele.type_document)
            document, _ = rendre(html, css, contexte)
            pdf = moteur.ecrire([document])
        except Http404:
            return JsonResponse({"detail": "Document introuvable."}, status=404)
        except DocumentError as exc:
            return JsonResponse({"detail": str(exc)}, status=400)
        except Exception as exc:
            return JsonResponse({"detail": f"Rendu impossible : {exc}"}, status=400)
        reponse = HttpResponse(pdf, content_type="application/pdf")
        reponse["Content-Disposition"] = 'inline; filename="apercu.pdf"'
        return reponse

    def defaut_view(self, request, pk):
        if request.method != "GET":
            return HttpResponseNotAllowed(["GET"])
        modele = self._modele(request, pk)
        html, css = modele_par_defaut(modele.type_document)
        return JsonResponse({"html": html, "css": css})

    def objets_view(self, request, pk):
        if request.method != "GET":
            return HttpResponseNotAllowed(["GET"])
        modele = self._modele(request, pk)
        return JsonResponse({"objets": [{"id": i, "label": l} for i, l in _objets_apercu(modele.type_document)]})
