"""À mélanger aux ModelAdmin : à la création d'un document, les champs listés dans `champs_date_du_jour` sont pré-remplis avec la
date du jour (modifiable). Une valeur passée dans l'adresse (?date_creation=…) reste prioritaire."""

from django.utils import timezone


class DateDuJourMixin:
    champs_date_du_jour = ()

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        for champ in self.champs_date_du_jour:
            initial.setdefault(champ, timezone.localdate())
        return initial
