from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import RegleCodification


@admin.register(RegleCodification)
class RegleCodificationAdmin(ModelAdmin):
    list_display = [
        "entite",
        "prefixe",
        "nombre_chiffres",
        "reinitialisation",
        "format_annee",
        "compteur_actuel",
        "exemple",
    ]
    list_filter = ["reinitialisation"]
    readonly_fields = ["exemple"]
