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
        "compteur_actuel",
    ]
    list_filter = ["reinitialisation"]
