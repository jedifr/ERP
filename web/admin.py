from django.contrib import admin, messages
from django.shortcuts import render
from django.utils.html import format_html, format_html_join
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

    @admin.display(description="Pièces")
    def pieces(self, obj):
        return format_html("<table>{}</table>", format_html_join("", "<tr><td style='padding-right:1em'>{}</td><td style='padding-right:1em'>{} × {} mm</td><td style='padding-right:1em'>{} pièce(s)</td><td style='padding-right:1em'>{} € HT</td><td>{}</td></tr>", (
            (l["designation"], l["demande"].get("matiere"), l["demande"].get("epaisseur"), l["demande"].get("quantite"), f"{l['prix_total_ht']:.2f}",
             "À valider : " + " ".join(l["validations"]) if l["validations"] else "")
            for l in obj.lignes
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
