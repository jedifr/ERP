from django.contrib import admin, messages
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import path, reverse
from django.utils.html import format_html, format_html_join
from django.utils.text import slugify
from unfold.admin import ModelAdmin

from .models import DemandeWeb, ReglageWeb
from .services.prix import ErreurPrixWeb, catalogue, chiffrer


@admin.register(ReglageWeb)
class ReglageWebAdmin(ModelAdmin):
    """Marge web, minimum de commande et quantité maximale du configurateur."""

    list_display = ["__str__", "taux_marge_pct", "minimum_commande_ht", "quantite_max"]

    def get_queryset(self, request):
        ReglageWeb.charger()
        return super().get_queryset(request)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(DemandeWeb)
class DemandeWebAdmin(ModelAdmin):
    """Paniers envoyés depuis le site client, prix recalculés par l'ERP : à transformer en devis."""

    list_display = ["numero", "date", "statut", "societe", "nom", "email", "nb_pieces", "total_ht", "a_valider"]
    list_filter = ["statut", "a_valider"]
    search_fields = ["numero", "nom", "societe", "email"]
    readonly_fields = ["numero", "date", "nom", "societe", "email", "telephone", "adresse", "message", "pieces", "total_pieces_ht", "total_ht", "a_valider"]
    fields = ["numero", "date", "statut", "nom", "societe", "email", "telephone", "adresse", "message", "pieces", "total_pieces_ht", "total_ht", "a_valider", "note_interne"]

    def has_add_permission(self, request):
        return False

    @admin.display(description="Pièces")
    def nb_pieces(self, obj):
        return len(obj.lignes)

    def get_urls(self):
        return [path("<int:pk>/dxf/<int:index>/", self.admin_site.admin_view(self.dxf_view), name="web_demandeweb_dxf")] + super().get_urls()

    def dxf_view(self, request, pk, index):
        """DXF de fabrication d'une ligne : le fichier envoyé par le client (converti en DXF s'il était en SVG ou DWG ;
        `?original=1` rend le fichier tel qu'envoyé), sinon le contour recalculé (pièce libre, forme)."""
        from decoupe.services import formes

        from .services.dxf import chemin_fichier

        demande = get_object_or_404(DemandeWeb, pk=pk)
        if not self.has_view_permission(request, demande) or not 0 <= index < len(demande.lignes):
            raise Http404
        ligne = demande.lignes[index]
        nom = f"{demande.numero}-{index + 1}-{slugify(ligne['designation'])[:60] or 'piece'}.dxf"
        original = bool(request.GET.get("original"))
        fichier = chemin_fichier(ligne["demande"].get("dxf"), original=original)
        # Fichier mis à l'échelle par le client : le DXF de fabrication est recalculé depuis le contour chiffré.
        if fichier and (original or float(ligne["demande"].get("echelle") or 1) == 1):
            return FileResponse(open(fichier, "rb"), as_attachment=True, filename=nom[:-4] + fichier.suffix)
        contour = ligne.get("contour")
        if not contour:
            raise Http404("Pas de contour pour cette ligne (pièce pliée : flan calculé au devis).")
        reponse = HttpResponse(formes.dxf_bytes(formes.Contour(contour["exterieur"], contour.get("trous") or [])), content_type="application/dxf")
        reponse["Content-Disposition"] = f'attachment; filename="{nom}"'
        return reponse

    @admin.display(description="Pièces")
    def pieces(self, obj):
        from .services.dxf import chemin_fichier

        def lien(i, l):
            if not (l.get("contour") or l["demande"].get("dxf")):
                return ""
            url = reverse("admin:web_demandeweb_dxf", args=[obj.pk, i])
            original = chemin_fichier(l["demande"].get("dxf"), original=True)
            if original and original.suffix != ".dxf":
                return format_html("<a href='{}'>DXF</a> · <a href='{}?original=1'>{}</a>", url, url, original.suffix[1:].upper())
            return format_html("<a href='{}'>DXF</a>", url)

        return format_html("<table>{}</table>", format_html_join("", "<tr><td style='padding-right:1em'>{}</td><td style='padding-right:1em'>{} × {} mm</td><td style='padding-right:1em'>{} pièce(s)</td><td style='padding-right:1em'>{} € HT</td><td style='padding-right:1em'>{}</td><td>{}</td></tr>", (
            (l["designation"], l["demande"].get("matiere"), l["demande"].get("epaisseur"), l["demande"].get("quantite"), f"{l['prix_total_ht']:.2f}",
             lien(i, l), "À valider : " + " ".join(l["validations"]) if l["validations"] else "")
            for i, l in enumerate(obj.lignes)
        )))


def _nombres(texte):
    return [x.replace(",", ".") for x in (texte or "").replace(";", " ").split()]


def apercu_configurateur_view(request):
    """Essai du prix en ligne depuis l'ERP : même calcul que l'API du site client."""
    cat = catalogue()
    saisie = request.POST if request.method == "POST" else {}
    resultat = None
    if request.method == "POST":
        type_piece = saisie.get("type_piece", "forme")
        cotes, pliage = {}, None
        if type_piece == "forme":
            famille = next((f for f in cat["formes"] if f["cle"] == saisie.get("forme")), None)
            for p in famille["parametres"] if famille else []:
                if saisie.get(f"cote_{p['cle']}", "") != "":
                    cotes[p["cle"]] = saisie[f"cote_{p['cle']}"]
        elif type_piece == "profil":
            pliage = {"forme": "profil", "faces": _nombres(saisie.get("faces")), "angles": _nombres(saisie.get("angles")), "longueur": saisie.get("longueur_pli")}
        else:
            pliage = {"forme": "boite", "longueur": saisie.get("longueur_fond"), "largeur": saisie.get("largeur_fond"),
                      "bords": _nombres(saisie.get("bords")), "angles": _nombres(saisie.get("angles_bords"))}
        matiere, _, epaisseur = (saisie.get("tole") or "").rpartition("|")
        try:
            resultat = chiffrer(matiere, epaisseur, saisie.get("quantite") or 1, forme=saisie.get("forme") if type_piece == "forme" else None,
                                cotes=cotes, pliage=pliage).en_dict()
        except ErreurPrixWeb as exc:
            messages.error(request, str(exc))
    contexte = {
        **admin.site.each_context(request), "title": "Configurateur web : essai de prix", "catalogue": cat, "saisie": saisie,
        "resultat": resultat, "saisie_cotes": {k: v for k, v in saisie.items() if k.startswith("cote_")},
        "toles": [(f"{m['matiere']}|{e:g}", f"{m['matiere']} {e:g} mm") for m in cat["matieres"] for e in m["epaisseurs"]],
    }
    return render(request, "web/apercu.html", contexte)
