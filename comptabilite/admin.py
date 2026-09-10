from django.contrib import admin, messages
from django.shortcuts import redirect
from unfold.admin import ModelAdmin
from unfold.decorators import action

from .models import CompteComptable, JournalComptable
from .pcg import PCG_MILLESIME, importer_pcg


@admin.register(CompteComptable)
class CompteComptableAdmin(ModelAdmin):
    list_display = ["code", "libelle", "classe", "systeme", "actif"]
    list_filter = ["classe", "systeme", "actif"]
    search_fields = ["code", "libelle"]
    autocomplete_fields = ["compte_parent"]
    actions_list = ["action_importer_pcg"]

    @action(
        description=f"Importer le plan comptable officiel (PCG {PCG_MILLESIME})",
        icon="cloud_download",
    )
    def action_importer_pcg(self, request):
        crees, maj = importer_pcg()
        self.message_user(
            request,
            f"Plan comptable {PCG_MILLESIME} importé : {crees} compte(s) créé(s), {maj} déjà à jour.",
            level=messages.SUCCESS,
        )
        return redirect("admin:comptabilite_comptecomptable_changelist")


@admin.register(JournalComptable)
class JournalComptableAdmin(ModelAdmin):
    list_display = ["code", "libelle", "nature", "actif"]
    list_filter = ["nature", "actif"]
    search_fields = ["code", "libelle"]
