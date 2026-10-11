from django.core.exceptions import PermissionDenied
from simple_history.admin import SimpleHistoryAdmin


class HistoriqueLectureSeule(SimpleHistoryAdmin):
    """Historique consultable, jamais rejouable : « revenir à une version
    précédente » réécrirait un enregistrement en contournant verrous et contrôles."""

    def revert_disabled(self, request, obj=None):
        return True

    def history_form_view(self, request, object_id, version_id, extra_context=None):
        raise PermissionDenied
