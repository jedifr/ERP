# Synchronisation avec le planning atelier

## Architecture

Conformément au cahier des charges (Phase 2) : **deux bases distinctes,
synchronisées via API**. L'ERP reste la source de vérité unique pour les
postes de travail et leurs tarifs. Flux :

- **ERP → Planning** : envoi des ordres de fabrication (`OrdreFabrication` +
  `OperationOF`) au lancement en production.
- **Planning → ERP** : retour de l'avancement (`temps_reel`,
  `quantite_bonne`, `quantite_rebut` sur `OperationOF`) — **pas encore
  implémenté**, à construire une fois le contrat d'API du planning atelier
  connu.

Toute la logique d'envoi ERP → Planning est centralisée dans
`chiffrage/planning_sync.py` (`PlanningSyncClient`), pour n'avoir qu'un seul
endroit à adapter le jour où l'API du planning atelier sera définie.

## État actuel

**Aucune API n'est encore définie côté planning atelier** (confirmé au
démarrage du projet). Tant que `PLANNING_API_URL` n'est pas configuré :

- La création d'une commande et de ses ordres de fabrication (bouton/action
  "Lancer en production") fonctionne normalement — elle ne dépend jamais de
  la disponibilité du planning.
- Chaque `OrdreFabrication` créé reste avec `statut_synchro = en_attente`.

## Configurer la connexion une fois l'API du planning atelier connue

Dans `.env` :

```
PLANNING_API_URL=http://<ip-du-nas-ou-hostname>:<port>/api
PLANNING_API_KEY=            # si une authentification par jeton est utilisée
PLANNING_SYNC_MAX_TENTATIVES=5
```

`PlanningSyncClient.envoyer_ordre_fabrication()` (dans
`chiffrage/planning_sync.py`) poste alors un JSON `{numero, article,
quantite, date_lancement, operations: [...]}` sur
`POST {PLANNING_API_URL}/ordres-fabrication`, avec un en-tête
`Idempotency-Key` (le numéro de l'OF) — **prérequis technique impératif du
cahier des charges** : l'API du planning atelier devra elle-même être
idempotente sur cette clé pour éviter tout doublon en cas de rejeu. Adapter
le format du payload et l'URL à l'API réelle le moment venu.

## Gestion des échecs et tentatives automatiques

- Échec de synchronisation → `statut_synchro = en_attente`, `nombre_tentatives`
  incrémenté, `date_derniere_tentative` mise à jour.
- Au-delà de `PLANNING_SYNC_MAX_TENTATIVES` (5 par défaut) → `statut_synchro
  = echec_persistant`.
- Action manuelle **"Resynchroniser"** (admin, ou `POST
  /api/v1/ordres-fabrication/{numero}/resynchroniser/`) : remet le compteur
  à zéro et retente immédiatement.

### Tentatives automatiques périodiques

Le cahier des charges prévoit une tentative automatique toutes les 15
minutes. Cette ERP tournant déjà sur le même NAS que le planning atelier, le
plus simple est d'utiliser le **Planificateur de tâches** de DSM plutôt qu'un
service applicatif supplémentaire (pas de Celery/Redis à opérer pour un outil
interne de cette taille) :

1. **Panneau de configuration → Planificateur de tâches → Créer → Tâche
   planifiée → Script défini par l'utilisateur**
2. Fréquence : toutes les 15 minutes
3. Commande (adapter le chemin du projet) :
   ```bash
   docker compose -f /volume1/docker/erp/docker-compose.yml exec -T web \
     python manage.py retry_sync_ordres_fabrication
   ```

Cette commande (`chiffrage/management/commands/retry_sync_ordres_fabrication.py`)
retente tous les OF `en_attente` et affiche un résumé
(`X/Y ordre(s) de fabrication synchronisé(s)`), visible dans le journal de
la tâche planifiée DSM.
