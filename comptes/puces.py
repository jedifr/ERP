"""Puces de filtre rapide au-dessus d'une liste (Devis, Factures, Commandes) : un clic = un filtre usuel, avec le nombre
de résultats. `puces = [(libellé, "?paramètres")]` sur le ModelAdmin ; une puce est active quand ses paramètres sont
exactement ceux de l'URL (hors page, tri et recherche)."""

from urllib.parse import parse_qsl

IGNORES = {"p", "o", "q", "_changelist_filters"}


class PucesMixin:
    puces = []
    list_before_template = "admin/puces_liste.html"

    def _puces(self, request):
        courants = {k: v for k, v in request.GET.items() if k not in IGNORES}
        resultat = []
        base = self.get_queryset(request)
        for libelle, parametres in self.puces:
            voulus = dict(parse_qsl(parametres.lstrip("?")))
            nombre = None
            if voulus and not any(k == "retard" or k == "validite" for k in voulus):
                try:
                    nombre = base.filter(**{k: v for k, v in voulus.items()}).count()
                except Exception:
                    nombre = None
            resultat.append({"libelle": libelle, "url": parametres or "?", "actif": courants == voulus, "nombre": nombre})
        return resultat

    def changelist_view(self, request, extra_context=None):
        extra_context = dict(extra_context or {})
        extra_context["puces"] = self._puces(request)
        return super().changelist_view(request, extra_context)
