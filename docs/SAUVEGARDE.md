# Sauvegarde, vérification et transfert vers un autre NAS

> Matériel cible : **UGREEN NASync DXP8800 Plus** (système **UGOS Pro**, basé sur Debian). Les scripts sont du shell standard (Docker, openssl, sha256sum,
> rsync) et ne dépendent pas de Synology ; seuls les chemins et les écrans de planification changent. Voir la section « UGREEN NASync » plus bas.

> Statut : scripts écrits et testés avec un faux Docker (logique, fichiers, chiffrement, alertes). **Pas encore essayés sur le vrai NAS** :
> faites un premier passage à la main (étape 1 à 4) et une restauration d'essai avant de compter dessus.

## Ce qui est sauvegardé
- la **base PostgreSQL** (tous les documents, articles, paramètres, utilisateurs) ;
- les **fichiers déposés** (plans DXF/DWG des pièces à découper) ;
- à garder **à part** : le fichier `.env` (clé secrète Django, mot de passe de la base) et la **phrase secrète** de chiffrement. Sans la clé
  secrète, les sessions sont fermées (sans gravité) ; sans la phrase secrète, une copie chiffrée est **définitivement illisible**.

## Les scripts (dans le dossier du projet sur le NAS)
| Script | Rôle |
|---|---|
| `sauvegarder-nas.sh` | Sauvegarde locale (14 conservées) + somme de contrôle + copie externe chiffrée facultative + alerte en cas d'échec |
| `verifier-sauvegarde.sh` | Contrôle une sauvegarde : fichier intact, **restauration d'essai** dans une base temporaire, âge maximum |
| `restaurer-nas.sh` | Restaure (sauvegarde de sécurité avant remplacement) ; `--nouveau-nas` pour une installation neuve ; lit les fichiers `.enc` |
| `exporter-transfert.sh` | Prépare UNE archive pour déménager vers un autre NAS |
| `importer-transfert.sh` | Installe cette archive sur le nouveau NAS |

## 1. Régler la sauvegarde (une fois)
1. Copier `sauvegarde.conf.exemple` en `sauvegarde.conf` (même dossier) et le remplir (il est conservé par `update-nas.sh`).
2. **Créer la phrase secrète** : `openssl rand -base64 32 > /volume1/docker/erp.cle && chmod 600 /volume1/docker/erp.cle`, puis la noter dans un
   gestionnaire de mots de passe (copie **hors du NAS**). Indiquer `CLE_CHIFFREMENT="/volume1/docker/erp.cle"`.
3. Choisir où part la copie externe (`COPIE_EXTERNE`) : disque USB branché sur le NAS, dossier réseau monté, ou autre NAS (`utilisateur@machine:/dossier`,
   rsync sur ssh avec clé sans mot de passe). Les copies externes sont chiffrées (`.enc`) ; le dossier local reste en clair sur le NAS.
4. Premier essai à la main : `cd /volume1/docker/erp && ./sauvegarder-nas.sh`, puis `./verifier-sauvegarde.sh`.

## 2. Planifier (Synology : DSM > Planificateur de tâches > Script défini par l'utilisateur ; UGREEN : voir « UGREEN NASync » plus bas)
- **Chaque nuit** : `cd /volume1/docker/erp && ./sauvegarder-nas.sh` — cocher « Envoyer les détails de l'exécution par e-mail » et « uniquement si le script se termine de façon anormale » : DSM vous écrit quand la sauvegarde échoue. (`ALERTE_URL` / `ALERTE_COMMANDE` ajoutent une alerte directe, par ex. ntfy.)
- **Chaque semaine** : `cd /volume1/docker/erp && ./verifier-sauvegarde.sh --age-max 2` — restaure la dernière sauvegarde dans une base temporaire, et **échoue si la dernière sauvegarde a plus de 2 jours** (planification arrêtée, NAS éteint…). C'est ce test qui dit qu'on peut vraiment restaurer.
- **Cloud** : pour un cloud (Synology C2, Backblaze B2, Google Drive…), utiliser **Hyper Backup** sur le dossier `erp_sauvegardes` : il chiffre, versionne et alerte. À privilégier si vous voulez une copie hors site sans autre machine. L'idéal : 3 copies (NAS, copie externe, hors site), sur 2 supports au moins.

## 3. Restaurer
- Sur la même machine : `./restaurer-nas.sh erp_base_AAAAMMJJ_HHMMSS.sql.gz erp_fichiers_AAAAMMJJ_HHMMSS.tar.gz` (taper `RESTAURER` pour confirmer ; une sauvegarde de sécurité de l'état actuel est faite avant).
- Depuis une copie externe chiffrée : même commande avec les fichiers `.enc` (la phrase secrète est lue dans `sauvegarde.conf`).
- Sur un NAS neuf : voir ci-dessous.

## 4. Changer de NAS
**Sur l'ancien NAS**
1. `./exporter-transfert.sh --avec-env` → archive `…/erp_sauvegardes/transferts/erp_transfert_….tar.gz.enc` : base, fichiers, version, mode d'emploi **et** `.env`, le tout chiffré (le script refuse d'inclure le `.env` en clair). Sans `--avec-env`, recopiez le `.env` à part.
2. Copier l'archive et le fichier de la phrase secrète vers le nouveau NAS (clé USB, scp…).

**Sur le nouveau NAS**
1. Installer Docker (Container Manager) et le projet : `docs/DEPLOIEMENT_SYNOLOGY.md` (la version du projet doit être **égale ou plus récente**).
2. Créer `sauvegarde.conf` avec `CLE_CHIFFREMENT` pointant vers le fichier de la phrase secrète.
3. `./importer-transfert.sh erp_transfert_….tar.gz.enc` : installe le `.env` (s'il n'existe pas), démarre la base, restaure base et fichiers.
4. Ouvrir l'ERP : connexion, un devis, un PDF. **Garder l'ancien NAS intact** jusqu'à ce contrôle ; refaire ensuite l'étape 1 de ce document (planification, copie externe) sur le nouveau NAS.

## Limites connues
- Pas de rotation automatique sur une destination rsync distante (à purger côté serveur).
- Les sauvegardes locales restent en clair sur le NAS (le chiffrement protège la copie externe) : protégez l'accès au dossier `erp_sauvegardes`.
- Une sauvegarde nocturne peut perdre au plus la journée de saisie ; pour une exigence plus fine, planifier plusieurs sauvegardes par jour.

## UGREEN NASync DXP8800 Plus (UGOS Pro)
Ce que je sais et ce que je n'ai **pas pu vérifier** (je n'ai pas accès à ce NAS) :
- **Docker** : UGOS Pro propose Docker ; l'ERP se lance comme sur Synology avec `docker compose` (en SSH, ou via l'application Docker). Les chemins `/volume1/docker/erp` des exemples sont à adapter : repérez le chemin réel de votre dossier partagé (`ls /volume1` en SSH). Si `sudo docker` demande un mot de passe dans une tâche planifiée, lancez la tâche en `root`.
- **Planification** : si UGOS n'offre pas de planificateur de scripts, utilisez **cron** en SSH (`sudo crontab -e`) :
  `30 2 * * * cd /volume1/docker/erp && ./sauvegarder-nas.sh >> /volume1/docker/erp_sauvegardes/journal.txt 2>&1`
  `0 6 * * 1 cd /volume1/docker/erp && ./verifier-sauvegarde.sh --age-max 2 >> /volume1/docker/erp_sauvegardes/journal.txt 2>&1`
  Dans ce cas, c'est `ALERTE_URL` / `ALERTE_COMMANDE` (voir `sauvegarde.conf.exemple`) qui vous prévient d'un échec — configurez-en une, par exemple une notification ntfy sur votre téléphone.
- **Copie hors site / cloud** : pas de Hyper Backup sur UGOS ; utilisez `COPIE_EXTERNE` (disque USB, dossier réseau, rsync vers un autre serveur), ou l'outil de sauvegarde cloud d'UGOS s'il existe, sur le dossier `erp_sauvegardes`.
- **Installation** : `update-nas.sh` utilise `curl`, `tar` et `docker compose`, présents sur une base Debian. Vérifiez `rsync` (`which rsync`) si vous choisissez une copie vers une autre machine.

### Où mettre quoi sur ce matériel (recommandations)
- **Système sur le SSD 128 Go, données sur disque rapide** : créez un volume sur les **SSD NVMe M.2** (miroir si possible) pour les données Docker (base PostgreSQL) : c'est ce qui rend les listes et les recalculs de devis réactifs. Gardez les **disques durs** (RAID 1, 5 ou 6 selon le nombre de baies utilisées) pour `erp_sauvegardes` et les archives.
- **Mémoire** : 8 Go suffisent pour l'ERP (base + application), mais le NAS fait aussi d'autres choses : passer à 16 Go (DDR5 SO-DIMM, extensible) est un confort peu coûteux. Sur ce processeur (10 cœurs), `GUNICORN_WORKERS=4` dans `.env` est un bon réglage.
- **Alimentation** : un **onduleur** évite la corruption de la base lors d'une coupure.
- **Instantanés** : si le volume est en Btrfs, activez les instantanés quotidiens du dossier des sauvegardes (protège d'une suppression ou d'un ransomware), en plus de la copie externe.
- **Performances** : ce processeur est nettement plus rapide que l'Atom d'un ancien NAS ; l'imbrication à plat des tôles, aujourd'hui le calcul le plus lent, sera sensiblement plus rapide.
