# Tâches à faire plus tard

## Regrouper « Tarifs de poste » dans « Postes de travail »

**Demande** : ne plus avoir deux écrans séparés ; tout saisir depuis la fiche du poste de travail.

**Pourquoi c'est séparé aujourd'hui** : `technique.TarifPoste` est une table à part car un poste a *plusieurs* tarifs dans le temps (coût horaire avec date de début / date de fin, `DateRangeHistoriqueMixin`). Cela permet de recalculer un ancien devis avec les taux de l'époque.

**Faisable sans toucher aux données** : on garde le modèle et l'historique, on change seulement l'écran.
- Ajouter un inline tabulaire « Tarifs » (coût horaire, date de début, date de fin) dans la fiche `PosteTravailAdmin` (`technique/admin.py`), en lecture du tarif en cours bien visible en haut.
- Afficher dans la liste des postes le tarif en cours (colonne « Coût horaire actuel »).
- Retirer « Tarifs de poste » de la page Paramétrage (carte « Atelier ») et garder l'écran `TarifPosteAdmin` accessible seulement par l'URL, ou le supprimer de l'admin.
- Contrôle « Postes de travail sans tarif » du centre « À compléter » : faire pointer chaque élément sur la fiche du poste (onglet tarifs).
- Vérifier que la validation de non-chevauchement des périodes (mixin d'historique) fonctionne dans l'inline ; ajouter des tests.
