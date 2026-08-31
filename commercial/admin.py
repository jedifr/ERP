from django.contrib import admin

from .models import Adresse, Tiers


class AdresseInline(admin.TabularInline):
    model = Adresse
    extra = 1


@admin.register(Tiers)
class TiersAdmin(admin.ModelAdmin):
    list_display = ["code", "raison_sociale", "type_tiers", "siret"]
    list_filter = ["type_tiers"]
    search_fields = ["code", "raison_sociale", "siret"]
    inlines = [AdresseInline]


@admin.register(Adresse)
class AdresseAdmin(admin.ModelAdmin):
    list_display = ["tiers", "type_adresse", "libelle", "ville", "est_principale"]
    list_filter = ["type_adresse", "est_principale"]
    search_fields = ["tiers__code", "tiers__raison_sociale", "ville"]
    autocomplete_fields = ["tiers"]
