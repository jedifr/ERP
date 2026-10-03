"""Export CSV des listes de l'admin (action « Exporter en CSV »).

Principes :
- on n'exporte que les colonnes affichées dans la liste (`get_list_display`) : un export
  ne révèle jamais plus que ce que l'utilisateur voit déjà à l'écran (les colonnes
  réservées à certains droits, comme les marges, le restent) ;
- droit requis : « voir » le modèle ; chaque export est journalisé (utilisateur, liste,
  nombre de lignes) ;
- format prévu pour Excel/LibreOffice en français : UTF-8 avec BOM, séparateur « ; »,
  virgule décimale, dates JJ/MM/AAAA ;
- les textes commençant par = + - @ sont neutralisés (injection de formule dans un
  tableur) ; les nombres, y compris négatifs (avoirs), restent des nombres.
"""

import csv
import datetime
import decimal
import html
import logging

from django.contrib import admin, messages
from django.contrib.admin.utils import label_for_field, lookup_field
from django.db import models
from django.http import HttpResponse
from django.utils import timezone
from django.utils.html import strip_tags

logger = logging.getLogger(__name__)

LIMITE_LIGNES = 20000
_PREFIXES_FORMULE = ("=", "+", "-", "@", "\t", "\r")


def _neutraliser(texte):
    return "'" + texte if texte.startswith(_PREFIXES_FORMULE) else texte


def _entete(modeladmin, colonne):
    libelle = str(
        modeladmin.model._meta.verbose_name if colonne == "__str__" else label_for_field(colonne, modeladmin.model, modeladmin)
    )
    return _neutraliser(libelle[:1].upper() + libelle[1:])


def formater_valeur(valeur):
    """Valeur Python -> cellule CSV."""
    if valeur is None:
        return ""
    if isinstance(valeur, bool):
        return "Oui" if valeur else "Non"
    if isinstance(valeur, (int, float, decimal.Decimal)):
        return str(valeur).replace(".", ",")
    if isinstance(valeur, datetime.datetime):
        if timezone.is_aware(valeur):
            valeur = timezone.localtime(valeur)
        return valeur.strftime("%d/%m/%Y %H:%M")
    if isinstance(valeur, datetime.date):
        return valeur.strftime("%d/%m/%Y")
    texte = html.unescape(strip_tags(str(valeur))).replace("\r", " ").replace("\n", " ").strip()
    if texte in ("-", "—"):
        return ""
    return _neutraliser(texte)


class ExportCsvMixin:
    """À placer avant ModelAdmin : ajoute l'action « Exporter la sélection en CSV »."""

    def has_export_permission(self, request):
        return self.has_view_permission(request)

    def get_actions(self, request):
        actions = super().get_actions(request)
        if self.has_export_permission(request):
            actions["exporter_csv"] = (
                ExportCsvMixin.exporter_csv,
                "exporter_csv",
                "Exporter la sélection en CSV (colonnes affichées)",
            )
        return actions

    def _valeur_cellule(self, nom, obj):
        try:
            champ, _attr, valeur = lookup_field(nom, obj, self)
        except Exception:  # colonne calculée dont la lecture échoue : cellule vide plutôt qu'export cassé
            return ""
        if champ is not None and getattr(champ, "choices", None) and valeur not in (None, ""):
            valeur = dict(champ.flatchoices).get(valeur, valeur)
        elif isinstance(champ, models.ForeignKey) and valeur is not None:
            valeur = str(valeur)
        return formater_valeur(valeur)

    def exporter_csv(self, request, queryset):
        if not self.has_export_permission(request):
            self.message_user(request, "Vous n'avez pas le droit d'exporter cette liste.", level=messages.ERROR)
            return None
        total = queryset.count()
        if total > LIMITE_LIGNES:
            self.message_user(
                request,
                f"Export refusé : {total} lignes (maximum {LIMITE_LIGNES}). Filtrez la liste puis recommencez.",
                level=messages.ERROR,
            )
            return None

        colonnes = [c for c in self.get_list_display(request) if c != "action_checkbox"]
        meta = self.model._meta
        reponse = HttpResponse(content_type="text/csv; charset=utf-8")
        horodatage = timezone.localtime().strftime("%Y%m%d-%H%M")
        reponse["Content-Disposition"] = f'attachment; filename="{meta.model_name}-{horodatage}.csv"'
        reponse.write("﻿")
        ecrivain = csv.writer(reponse, delimiter=";", lineterminator="\r\n")
        ecrivain.writerow([_entete(self, c) for c in colonnes])
        for obj in queryset:
            ecrivain.writerow([self._valeur_cellule(c, obj) for c in colonnes])

        logger.info(
            "Export CSV : %s a exporté %d ligne(s) de %s", request.user.get_username(), total, meta.label
        )
        return reponse


# Pour `admin.action` : la description est portée par get_actions ci-dessus.
ExportCsvMixin.exporter_csv = admin.action(description="Exporter la sélection en CSV")(ExportCsvMixin.exporter_csv)
