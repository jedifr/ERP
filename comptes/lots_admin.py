"""Écrans « Modifier par lots… » des listes de l'admin (voir comptes/lots.py) : saisie des champs, aperçu avant → après, application."""

import datetime

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.utils.safestring import mark_safe

from . import lots

PREFIXES = ("c_", "v_", "m_", "u_")


def _choix_depuis_post(post, champs):
    """[(ChampLot, valeur, mode, unité)] des champs cochés dans le formulaire."""
    choix = []
    for spec in champs:
        if not post.get(f"c_{spec.nom}"):
            continue
        valeur = post.getlist(f"v_{spec.nom}") if spec.genre == "m2m" else post.get(f"v_{spec.nom}", "")
        choix.append((spec, valeur, post.get(f"m_{spec.nom}") or "fixe", post.get(f"u_{spec.nom}") or None))
    return choix


def _champs_formulaire(champs, post):
    """Champs prêts pour l'affichage : choix, valeur saisie, case cochée, mode et unité retenus."""
    sortie = []
    for spec in champs:
        valeur = post.getlist(f"v_{spec.nom}") if spec.genre == "m2m" else post.get(f"v_{spec.nom}", "")
        sortie.append({
            "spec": spec, "coche": bool(post.get(f"c_{spec.nom}")), "valeur": valeur, "mode": post.get(f"m_{spec.nom}") or "fixe",
            "unite": post.get(f"u_{spec.nom}") or "", "choix": spec.liste_choix(), "objets": spec.objets(),
            "modes": [(code, libelle) for code, libelle in lots.MODES if code in spec.modes],
        })
    return sortie


def vue_lot(modele_admin, request, queryset, champs, nom_action, titre, periode=False, description_lot=None, modele=None):
    """Réponse de l'action d'admin : saisie, aperçu ou application selon la phase du formulaire."""
    if not modele_admin.has_change_permission(request):
        raise PermissionDenied
    modele = modele or modele_admin.model
    ids = request.POST.getlist("_selected_action")
    phase = request.POST.get("lot_phase", "saisie")
    choix = _choix_depuis_post(request.POST, champs)
    date_effet = None
    if periode:
        try:
            date_effet = datetime.date.fromisoformat(request.POST.get("date_effet", ""))
        except ValueError:
            date_effet = None
    contexte = {
        **modele_admin.admin_site.each_context(request), "title": titre, "opts": modele._meta, "action": nom_action, "ids": ids, "queryset": queryset,
        "nombre": queryset.count(), "champs": _champs_formulaire(champs, request.POST), "periode": periode,
        "date_effet": request.POST.get("date_effet") or datetime.date.today().isoformat(), "phase": "saisie",
        "reprise": [(k, v) for k in request.POST for v in request.POST.getlist(k) if k.startswith(PREFIXES) or k == "date_effet"],
    }
    if phase == "saisie" or not choix or (periode and date_effet is None):
        if phase != "saisie":
            messages.error(request, "Cochez au moins un champ" + (" et indiquez la date d'effet." if periode else "."))
        return TemplateResponse(request, "admin/comptes/lot.html", contexte)
    description = (description_lot or "Modification par lots") + " : " + ", ".join(spec.libelle for spec, *_ in choix)
    if phase == "apercu":
        lignes = lots.preparer(modele, queryset, choix, periode=date_effet)
        a_appliquer = [l for l in lignes if not l["ignore"] and not l["erreur"]]
        return TemplateResponse(request, "admin/comptes/lot.html", {
            **contexte, "phase": "apercu", "lignes": lignes, "nb_ok": len(a_appliquer), "nb_ignores": sum(1 for l in lignes if l["ignore"]),
            "nb_erreurs": sum(1 for l in lignes if l["erreur"]),
        })
    if phase == "appliquer":
        if periode:
            lot, n, ignores = lots.appliquer_periode(modele, queryset, choix, date_effet, request.user, description + f" (à partir du {date_effet:%d/%m/%Y})")
        else:
            lignes = lots.preparer(modele, queryset, choix)
            lot, n = lots.appliquer(modele, lignes, request.user, description)
        if n:
            lien = mark_safe(f' <a href="/admin/comptes/lotmodification/{lot.pk}/change/">Voir le lot (annulable)</a>')
            messages.success(request, mark_safe(f"{n} objet(s) mis à jour." + lien))
        else:
            messages.warning(request, "Rien n'a été modifié.")
        return redirect(request.path)
    return TemplateResponse(request, "admin/comptes/lot.html", contexte)


class ModificationParLotsMixin:
    """À mélanger à un ModelAdmin : ajoute l'action « Modifier par lots… » (à déclarer dans `actions`) pour les champs `champs_lot`,
    et « Modifier avec effet à une date… » (`champs_periode`) pour les modèles à périodes."""

    champs_lot = []
    champs_periode = []

    @admin.action(description="Modifier par lots…", permissions=["change"])
    def action_modifier_par_lots(self, request, queryset):
        return vue_lot(self, request, queryset, self.champs_lot, "action_modifier_par_lots", f"Modifier par lots — {self.model._meta.verbose_name_plural}")

    @admin.action(description="Modifier avec effet à une date (nouvelle période)…", permissions=["change"])
    def action_modifier_periode(self, request, queryset):
        return vue_lot(
            self, request, queryset, self.champs_periode, "action_modifier_periode", f"Modifier avec effet à une date — {self.model._meta.verbose_name_plural}",
            periode=True, description_lot="Nouvelle période",
        )
