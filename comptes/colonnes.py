"""Colonnes personnalisables : chaque utilisateur choisit celles qu'il veut voir dans les listes des documents et
dans les tableaux de lignes (« Mes colonnes », menu du compte).

Un admin ou un inline déclare `ColonnesPersonnalisablesMixin` :
- liste (ModelAdmin) : toutes les colonnes de `list_display` sauf la première (le lien vers la fiche) et les cases
  à cocher peuvent être masquées ; `colonnes_toujours_visibles` ajoute des exceptions ;
- tableau de lignes (inline) : seules les colonnes de `colonnes_optionnelles` peuvent être masquées (jamais un
  champ obligatoire ou utilisé par les calculs en direct de la fiche) ;
- `colonnes_masquees_par_defaut` : ce qui est masqué tant que l'utilisateur n'a rien choisi.

Les choix sont enregistrés par utilisateur (PreferenceColonnes) ; sans choix enregistré, tout le monde voit les
colonnes par défaut."""

from django.contrib import admin
from django.contrib.admin.utils import label_for_field
from django.contrib.admin.sites import site
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.contrib import messages
from unfold.admin import ModelAdmin as UnfoldModelAdmin  # noqa: F401  (documentation : ces mixins se placent avant ModelAdmin)

from .models import PreferenceColonnes


def _nom(colonne):
    return colonne if isinstance(colonne, str) else getattr(colonne, "__name__", str(colonne))


def cle_ecran_liste(model_admin):
    meta = model_admin.model._meta
    return f"{meta.app_label}.{meta.model_name}"


def cle_ecran_inline(parent_model, inline):
    meta = parent_model._meta
    return f"{meta.app_label}.{meta.model_name}:{inline.model._meta.model_name}"


def masquees_de(request, ecran, par_defaut):
    """Ensemble des colonnes masquées pour l'utilisateur de la requête (avec mise en cache sur la requête)."""
    utilisateur = getattr(request, "user", None)
    if utilisateur is None or not getattr(utilisateur, "is_authenticated", False):
        return set(par_defaut)
    cache = getattr(request, "_preferences_colonnes", None)
    if cache is None:
        cache = {p.ecran: set(p.masquees) for p in PreferenceColonnes.objects.filter(utilisateur=utilisateur)}
        request._preferences_colonnes = cache
    return cache[ecran] if ecran in cache else set(par_defaut)


class ColonnesPersonnalisablesMixin:
    """Pour un ModelAdmin (liste) ou un InlineModelAdmin (tableau de lignes) : voir le module."""

    colonnes_masquees_par_defaut = ()
    colonnes_optionnelles = ()  # inline : seules ces colonnes se masquent
    colonnes_toujours_visibles = ()  # liste : colonnes à ne jamais masquer (en plus de la première)

    def _ecran(self):
        if hasattr(self, "parent_model"):
            return cle_ecran_inline(self.parent_model, self)
        return cle_ecran_liste(self)

    def colonnes_masquables(self, colonnes=None):
        if hasattr(self, "parent_model"):  # inline
            return [c for c in self.colonnes_optionnelles]
        colonnes = colonnes if colonnes is not None else list(self.list_display)
        exclues = {"action_checkbox", "__str__", *self.colonnes_toujours_visibles}
        premiere = _nom(colonnes[0]) if colonnes else None
        return [_nom(c) for c in colonnes if _nom(c) not in exclues and _nom(c) != premiere]

    def _masquees(self, request, colonnes=None):
        choisies = masquees_de(request, self._ecran(), self.colonnes_masquees_par_defaut)
        return choisies & set(self.colonnes_masquables(colonnes))

    # --- liste -------------------------------------------------------------------------------------------------------
    def get_list_display(self, request):
        colonnes = super().get_list_display(request)
        masquees = self._masquees(request, colonnes)
        return [c for c in colonnes if _nom(c) not in masquees]

    # --- tableau de lignes ----------------------------------------------------------------------------------------------
    def get_fields(self, request, obj=None):
        champs = super().get_fields(request, obj)
        masquees = self._masquees(request)
        return [c for c in champs if c not in masquees]


def _ecrans_personnalisables():
    """[(clé, titre, [(colonne, libellé, masquée_par_défaut)], modèle admin)] pour tous les écrans déclarés."""
    ecrans = []
    for modele, model_admin in site._registry.items():
        meta = modele._meta
        if isinstance(model_admin, ColonnesPersonnalisablesMixin):
            toutes = super(ColonnesPersonnalisablesMixin, model_admin).get_list_display(_RequeteVide())
            colonnes = model_admin.colonnes_masquables(list(toutes))
            if colonnes:
                ecrans.append((
                    cle_ecran_liste(model_admin), f"Liste : {meta.verbose_name_plural}",
                    [(c, _libelle(c, model_admin), c in model_admin.colonnes_masquees_par_defaut) for c in colonnes],
                ))
        for classe in model_admin.inlines:
            if issubclass(classe, ColonnesPersonnalisablesMixin):
                inline = classe(modele, site)
                if inline.colonnes_optionnelles:
                    ecrans.append((
                        cle_ecran_inline(modele, inline),
                        f"Lignes de la fiche : {inline.model._meta.verbose_name_plural} ({meta.verbose_name})",
                        [(c, _libelle(c, inline), c in inline.colonnes_masquees_par_defaut) for c in inline.colonnes_optionnelles],
                    ))
    return sorted(ecrans, key=lambda e: e[1])


class _RequeteVide:
    """Requête minimale pour interroger get_list_display hors d'une vraie requête (aucun utilisateur : défauts)."""

    user = None


def _libelle(colonne, model_admin):
    try:
        libelle = label_for_field(colonne, model_admin.model, model_admin)
    except Exception:
        libelle = colonne
    libelle = str(libelle)
    return libelle[:1].upper() + libelle[1:]


def mes_colonnes(request):
    """Page « Mes colonnes » : une case par colonne, cochée = visible. Réglage personnel de l'utilisateur."""
    ecrans = _ecrans_personnalisables()
    if request.method == "POST":
        if "defaut" in request.POST:
            PreferenceColonnes.objects.filter(utilisateur=request.user).delete()
            messages.success(request, "Colonnes rétablies par défaut.")
        else:
            for cle, _titre, colonnes in ecrans:
                masquees = [c for c, _l, _d in colonnes if f"{cle}|{c}" not in request.POST]
                par_defaut = [c for c, _l, defaut in colonnes if defaut]
                if sorted(masquees) == sorted(par_defaut):
                    PreferenceColonnes.objects.filter(utilisateur=request.user, ecran=cle).delete()
                else:
                    PreferenceColonnes.objects.update_or_create(
                        utilisateur=request.user, ecran=cle, defaults={"masquees": masquees}
                    )
            messages.success(request, "Vos colonnes sont enregistrées.")
        return redirect(reverse("mes_colonnes"))
    enregistrees = {p.ecran: set(p.masquees) for p in PreferenceColonnes.objects.filter(utilisateur=request.user)}
    blocs = []
    for cle, titre, colonnes in ecrans:
        masquees = enregistrees[cle] if cle in enregistrees else {c for c, _l, defaut in colonnes if defaut}
        blocs.append({"titre": titre, "colonnes": [
            {"nom": f"{cle}|{c}", "libelle": libelle, "visible": c not in masquees} for c, libelle, _d in colonnes
        ]})
    return TemplateResponse(
        request, "admin/mes_colonnes.html",
        {**site.each_context(request), "title": "Mes colonnes", "blocs": blocs},
    )
