# Tâches à faire plus tard

Aucune tâche en attente.

## Fait
- Tarifs de poste regroupés dans la fiche du poste de travail (tableau « Tarifs » + colonne « Coût horaire actuel ») ; plus d'entrée séparée dans le Paramétrage.
- Éditeur d'opérations de fabrication (gamme de l'article, gammes types) utilisé dans la fiche devis **et** dans le constructeur de devis (mode brouillon : étapes gardées en mémoire jusqu'à la création de l'article).

## Informations du projet (à garder en tête)
- L'ERP **n'est pas en production** : développement en cours, le client le dira quand il voudra démarrer. Aucune contrainte de compatibilité avec des données de production ; **l'intégralité des documents commerciaux (devis, commandes, livraisons, factures, achats…) pourra être supprimée** au passage en production.
- Changement de NAS prévu.

## À prévoir : phase de migration vers la production
Reprendre les données de référence (clients et fournisseurs, articles, matières, postes et tarifs, gammes, paramètres de coupe, formats de tôle, plan comptable, société…), vider les documents commerciaux de test et leurs compteurs de codification, remettre à zéro l'historique et les journaux. Idée : commande de gestion `preparer_production` (liste ce qui sera supprimé, demande confirmation, sauvegarde avant). Revoir aussi la sécurité signalée sur l'accueil (clé secrète, mot de passe de base, cookies sécurisés).

## À prévoir : sauvegarde et transfert vers le nouveau NAS
Existant : `sauvegarder-nas.sh` (base PostgreSQL compressée + fichiers déposés, 14 sauvegardes conservées) et `restaurer-nas.sh` (avec sauvegarde de sécurité avant remplacement). À ajouter : sauvegarde **hors du NAS** (copie chiffrée vers un second disque / un autre NAS / un cloud, règle 3-2-1), planification et alerte en cas d'échec, test de restauration périodique, procédure de **transfert vers le nouveau NAS** (sauvegarde, installation Docker, restauration, vérification de version via `VERSION`) et copie du `.env` (clé secrète) à part, en lieu sûr.
