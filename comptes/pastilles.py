"""Pastilles de statut colorées dans les listes de l'admin.

Une seule définition de couleurs, partagée par tous les écrans : un admin déclare
`pastilles = {"statut": COULEURS_DEVIS_STATUT}` et la colonne s'affiche en pastille (fond clair, texte
coloré) au lieu d'un texte gris. Les variantes sont celles d'Unfold : info (bleu), success (vert),
warning (orange), danger (rouge), primary (ambre), sinon gris. L'export CSV, le tri et les filtres ne
changent pas (la colonne reste le champ du modèle, seul l'affichage est enrobé)."""

from django.utils.text import capfirst

# Vocabulaire de couleurs commun : on choisit la couleur par sens, pas écran par écran.
EN_COURS = "info"        # brouillon, en cours, en attente d'import
A_FAIRE = "warning"      # en attente, à payer, réponse attendue
TERMINE = "success"      # validé, soldé, payé, synchronisé
PROBLEME = "danger"      # refusé, annulé, en échec
NEUTRE = "primary"       # informatif (avoir, révisé…)


class PastillesMixin:
    """`pastilles = {champ: {valeur_enregistrée: variante}}` ; à placer avant ModelAdmin."""

    pastilles = {}

    def get_list_display(self, request):
        colonnes = super().get_list_display(request)
        return [self._colonne_pastille(c) if isinstance(c, str) and c in self.pastilles else c for c in colonnes]

    def _colonne_pastille(self, champ):
        modele_champ = self.model._meta.get_field(champ)
        couleurs = self.pastilles[champ]
        libelles = {str(libelle): couleurs[valeur] for valeur, libelle in modele_champ.flatchoices if valeur in couleurs}

        def colonne(obj):
            return getattr(obj, f"get_{champ}_display")()

        colonne.__name__ = champ  # Unfold en tire la classe CSS de la cellule (field-<champ>)
        colonne.short_description = capfirst(str(modele_champ.verbose_name))
        colonne.admin_order_field = champ
        colonne.label = libelles
        return colonne
