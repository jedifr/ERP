from django.contrib import admin, messages
from django.http import FileResponse, Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import path, reverse
from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe
from django.utils.text import slugify
from unfold.admin import ModelAdmin
from unfold.decorators import action as unfold_action
from unfold.enums import ActionVariant

from .models import DemandeWeb, ReglageWeb
from .services.prix import ErreurPrixWeb, catalogue, chiffrer


@admin.register(ReglageWeb)
class ReglageWebAdmin(ModelAdmin):
    """Marge web, minimum de commande et quantité maximale du configurateur."""

    list_display = ["__str__", "taux_marge_pct", "minimum_commande_ht", "quantite_max", "adresse_retrait"]

    def get_queryset(self, request):
        ReglageWeb.charger()
        return super().get_queryset(request)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


# Lien bien visible dans un champ en lecture seule (l'admin ne colore pas les liens qu'on y place).
LIEN = "color:#2563eb;text-decoration:underline;font-weight:600"


@admin.register(DemandeWeb)
class DemandeWebAdmin(ModelAdmin):
    """Paniers envoyés depuis le site client, prix recalculés par l'ERP : à transformer en devis."""

    list_display = ["numero", "date", "statut", "societe", "nom", "email", "nb_pieces", "total_ht", "retrait_atelier", "a_valider"]
    list_filter = ["statut", "retrait_atelier", "a_valider"]
    search_fields = ["numero", "nom", "societe", "email"]
    readonly_fields = ["numero", "date", "nom", "societe", "email", "telephone", "retrait_atelier", "adresse", "code_postal", "ville", "message", "pieces",
                       "total_pieces_ht", "total_ht", "a_valider", "devis_lien"]
    fields = ["numero", "date", "statut", "devis_lien", "nom", "societe", "email", "telephone", "retrait_atelier", "adresse", "code_postal", "ville", "message",
              "pieces", "total_pieces_ht", "total_ht", "a_valider", "note_interne"]
    actions_detail = ["action_valider"]

    @admin.display(description="Devis")
    def devis_lien(self, obj):
        if not obj.devis_id:
            return "Pas encore : « Valider » crée le client et le devis."
        return format_html("<a href='{}' style='{}'>{}</a>", reverse("admin:chiffrage_devis_change", args=[obj.devis_id]), LIEN, obj.devis_id)

    def get_actions_detail(self, request, object_id):
        actions = super().get_actions_detail(request, object_id)
        if DemandeWeb.objects.filter(pk=object_id, devis__isnull=False).exists():  # déjà validée : plus de bouton
            actions = [a for a in actions if not a.action_name.endswith("action_valider")]
        return actions

    def has_valider_permission(self, request, object_id=None):
        return request.user.has_perm("chiffrage.add_devis") and request.user.has_perm("web.change_demandeweb")

    @unfold_action(description="Valider : créer le client et le devis", icon="task_alt", variant=ActionVariant.PRIMARY,
                   permissions=["valider"], url_path="valider")
    def action_valider(self, request, object_id):
        """Retrouve (par l'e-mail) ou crée le client, crée le devis de la demande et passe la demande en « Traitée »."""
        from .services.conversion import ErreurConversion, creer_devis

        demande = get_object_or_404(DemandeWeb, pk=object_id)
        retour = HttpResponseRedirect(reverse("admin:web_demandeweb_change", args=[demande.pk]))
        try:
            devis, cree, remarques = creer_devis(demande)
        except ErreurConversion as exc:
            self.message_user(request, str(exc), level=messages.WARNING)
            return retour
        client = f"Client {devis.client} {'créé' if cree else 'retrouvé par son e-mail'}"
        self.message_user(request, f"Devis {devis.numero} créé. {client}.", level=messages.SUCCESS)
        for remarque in remarques:
            self.message_user(request, remarque, level=messages.INFO)
        return HttpResponseRedirect(reverse("admin:chiffrage_devis_change", args=[devis.pk]))

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
        if fichier and (original or (not ligne.get("pliage") and float(ligne["demande"].get("echelle") or 1) == 1)):
            return FileResponse(open(fichier, "rb"), as_attachment=True, filename=nom[:-4] + fichier.suffix)
        contour = ligne.get("contour")
        if not contour:
            raise Http404("Pas de contour pour cette ligne (pièce pliée : flan calculé au devis).")
        contenu = formes.dxf_bytes(formes.Contour(contour["exterieur"], contour.get("trous") or []), plis=ligne.get("lignes_pli"))
        reponse = HttpResponse(contenu, content_type="application/dxf")
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
            dxf = format_html("<a href='{}' style='{}'>Télécharger le DXF</a>", url, LIEN)
            if original and original.suffix != ".dxf":
                return format_html("{} · <a href='{}?original=1' style='{}'>Fichier d'origine ({})</a>", dxf, url, LIEN, original.suffix[1:].upper())
            return dxf

        entete = "".join(f"<th style='padding-right:1em;text-align:left'>{t}</th>" for t in ("Pièce", "Matière", "Quantité", "Prix", "Fichier", ""))
        return format_html("<table><tr>{}</tr>{}</table>", mark_safe(entete), format_html_join("", "<tr><td style='padding-right:1em'>{}</td><td style='padding-right:1em'>{} {} mm</td><td style='padding-right:1em'>{} pièce(s)</td><td style='padding-right:1em'>{} € HT</td><td style='padding-right:1em'>{}</td><td>{}</td></tr>", (
            (l["designation"], l["demande"].get("matiere"), l["demande"].get("epaisseur"), l["demande"].get("quantite"), f"{l['prix_total_ht']:.2f}",
             lien(i, l), " ".join(filter(None, ["À valider : " + " ".join(l["validations"]) if l["validations"] else "",
                                                  " ".join(l.get("modifications") or [])])))
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
                                cotes=cotes, pliage=pliage, procede=saisie.get("procede") or None).en_dict()
        except ErreurPrixWeb as exc:
            messages.error(request, str(exc))
    contexte = {
        **admin.site.each_context(request), "title": "Configurateur web : essai de prix", "catalogue": cat, "saisie": saisie,
        "resultat": resultat, "saisie_cotes": {k: v for k, v in saisie.items() if k.startswith("cote_")},
        "toles": [(f"{m['matiere']}|{e:g}", f"{m['matiere']} {e:g} mm") for m in cat["matieres"] for e in m["epaisseurs"]],
    }
    return render(request, "web/apercu.html", contexte)
