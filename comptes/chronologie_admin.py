"""À mélanger aux ModelAdmin (Devis, Commande, Facture, Livraison) : ajoute la chronologie d'affaire à la fiche."""

from django.contrib.admin.utils import unquote

from .chronologie import chronologie


class ChronologieMixin:
    """Les admins sans gabarit de fiche propre (Commande, Facture) posent `change_form_template = "admin/chronologie_change_form.html"` ;
    celui du devis inclut déjà `admin/_chronologie.html`."""

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        extra_context = dict(extra_context or {})
        if object_id:
            objet = self.get_object(request, unquote(object_id))
            try:
                extra_context["chronologie"] = chronologie(objet) if objet is not None else None
            except Exception:  # la chronologie est une aide : jamais bloquante pour la fiche
                extra_context["chronologie"] = None
        return super().changeform_view(request, object_id, form_url, extra_context)
