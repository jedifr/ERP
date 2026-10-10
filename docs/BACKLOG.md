# Tâches à faire plus tard

Aucune tâche en attente.

## Fait
- Tarifs de poste regroupés dans la fiche du poste de travail (tableau « Tarifs » + colonne « Coût horaire actuel ») ; plus d'entrée séparée dans le Paramétrage.
- Éditeur d'opérations de fabrication (gamme de l'article, gammes types) utilisé dans la fiche devis **et** dans le constructeur de devis (mode brouillon : étapes gardées en mémoire jusqu'à la création de l'article).

## Informations du projet (à garder en tête)
- L'ERP **n'est pas en production** : développement en cours, le client le dira quand il voudra démarrer. Aucune contrainte de compatibilité avec des données de production ; **l'intégralité des documents commerciaux (devis, commandes, livraisons, factures, achats…) pourra être supprimée** au passage en production.
- Changement de NAS prévu.

## À prévoir : phase de migration vers la production
Reprendre les données de référence (clients et fournisseurs, articles, matières, postes et tarifs, gammes, paramètres de coupe, formats de tôle, plan comptable, société…), vider les documents commerciaux de test et leurs compteurs de codification, remettre à zéro l'historique et les journaux. Fait : commande de gestion `preparer_production` (liste ce qui sera supprimé, sauvegarde attestée et confirmation exigées) ; voir `docs/RELECTURE_PRODUCTION.md`, section 5. Revoir aussi la sécurité signalée sur l'accueil (clé secrète, mot de passe de base, cookies sécurisés).

## Sauvegarde et transfert vers le nouveau NAS — fait, à valider sur le vrai NAS
Scripts et mode d'emploi : `docs/SAUVEGARDE.md` (copie externe chiffrée, vérification par restauration d'essai, alerte, export/import de transfert). Reste à faire par le client : un premier passage sur le NAS, le choix de la destination externe et de la phrase secrète, la planification dans DSM, un essai de restauration.
