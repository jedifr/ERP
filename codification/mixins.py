"""Mixin pour brancher la codification automatique (voir services.py) sur le
formulaire d'ajout d'un ModelAdmin, sans dupliquer la même logique dans
chaque admin concerné.
"""

from .services import generer_code


class CodificationInitialeMixin:
    """Pré-remplit le champ clé primaire (numéro/code) du formulaire d'ajout
    avec le prochain code généré par la règle `codification_entite`, si une
    règle est configurée pour cette entité — sinon comportement inchangé
    (champ laissé vide, comme avant). Le champ reste un champ texte normal :
    l'utilisateur peut corriger la valeur proposée avant d'enregistrer."""

    codification_entite = None

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        if self.codification_entite:
            code = generer_code(self.codification_entite)
            if code:
                initial.setdefault(self.model._meta.pk.name, code)
        return initial
