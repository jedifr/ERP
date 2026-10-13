# Installer l'ERP sur le UGREEN NASync (UGOS Pro) et y transférer les données du Synology

Matériel prévu : DXP8800 Plus, 2 NVMe en RAID 1 (volume « rapide ») et 2 IronWolf en RAID 1
(volume « données »). Voir aussi [SAUVEGARDE.md](SAUVEGARDE.md), section « UGREEN NASync ».

L'ERP tourne exactement comme sur le Synology : deux conteneurs (`web` = Django/Gunicorn,
`db` = PostgreSQL 16) lancés par `docker compose` depuis le dossier du projet. Le
`docker-compose.yml` du dépôt est utilisé **tel quel** : aucune adaptation n'est nécessaire.

> Conventions de ce guide (à remplacer si votre NAS diffère) :
> - dossier du projet : `/volume1/docker/erp` (`/volume1` = le volume NVMe) ;
> - sauvegardes : `/volume2/erp_sauvegardes` (`/volume2` = le volume IronWolf) ;
> - utilisateur SSH : `marc`, IP du Ugreen : `192.168.1.60`, IP du Synology : `192.168.1.50`.

---

## Étape 0 — Préparer le Ugreen (interface UGOS Pro, navigateur)

1. **Stockage** : créer d'abord le volume sur les 2 NVMe (RAID 1), puis celui sur les 2 IronWolf
   (RAID 1). Le premier créé devient en général `/volume1`. Pour le volume NVMe, choisir **ext4**
   si UGOS le propose (recommandé pour PostgreSQL) ; Btrfs fonctionne aussi, ce n'est pas bloquant.
2. **Docker** : Centre d'applications → installer **Docker**, en choisissant le **volume NVMe**
   comme emplacement. C'est là que Docker range ses volumes (la base PostgreSQL et les fichiers
   déposés vivent dans des volumes Docker nommés, pas dans le dossier du projet).
3. **SSH** : Panneau de configuration → Terminal → activer **SSH** (port 22). Seuls les comptes
   administrateurs peuvent se connecter ; `root` est désactivé, on passe par `sudo`.
4. **Dossiers partagés** : vérifier qu'un dossier `docker` existe sur le volume NVMe (l'appli Docker
   le crée souvent ; sinon le créer), et créer le dossier partagé `erp_sauvegardes` sur le volume
   IronWolf (lecture/écriture pour votre compte). Sur UGOS, un `mkdir` à la racine d'un volume est
   refusé : les dossiers de premier niveau se créent toujours depuis l'interface.

## Étape 1 — Vérifier le Ugreen en SSH

Depuis votre PC (PowerShell ou Terminal) :

```bash
ssh marc@192.168.1.60
ls -d /volume*                 # repérer quel /volumeN est le NVMe et lequel est l'IronWolf
ls /volume1                    # le dossier partagé "docker" doit apparaître
sudo docker compose version    # doit afficher "Docker Compose version v2..."
which curl tar openssl rsync   # outils utilisés par les scripts
```

Si `docker compose version` échoue mais que `docker-compose version` fonctionne, signalez-le :
les scripts utilisent `docker compose` (version 2) et devront être adaptés.

## Étape 2 — Mettre le Synology à jour (même version des deux côtés)

La version installée sur le Ugreen doit être **égale ou plus récente** que celle du Synology
(les migrations tournent au démarrage). Le plus simple : mettre d'abord le Synology à jour.

```bash
ssh marc@192.168.1.50
cd /volume1/docker/erp
./update-nas.sh claude/project-construction-k7owwb
cat VERSION
```

## Étape 3 — Installer le projet sur le Ugreen

```bash
ssh marc@192.168.1.60
cd /volume1/docker
curl -fL -o erp.tar.gz https://codeload.github.com/jedifr/ERP/tar.gz/refs/heads/claude/project-construction-k7owwb
mkdir erp
tar -xzf erp.tar.gz -C erp --strip-components=1
rm erp.tar.gz
cd erp
cat VERSION        # doit être identique à celle du Synology (étape 2)
```

Le dossier doit s'appeler **`erp`** : Docker nomme les volumes d'après lui
(`erp_erp_postgres_data`, `erp_erp_media_data`), et tous les scripts s'appuient dessus.

## Étape 4 — Récupérer le `.env` du Synology

On garde le même `.env` : même clé secrète et même mot de passe de base. Depuis le Ugreen :

```bash
cd /volume1/docker/erp
scp marc@192.168.1.50:/volume1/docker/erp/.env .env
chmod 600 .env
```

(Si `scp` est refusé par le Synology, essayez `scp -O ...` ; sinon téléchargez `.env` avec File Station sur votre PC, puis
déposez-le dans `docker/erp` avec l'application Fichiers d'UGOS. Le fichier ne quitte pas votre
réseau : ne l'envoyez ni par e-mail ni dans un cloud.)

Puis l'adapter au Ugreen (`vi .env` ou `nano .env`) :

| Variable | Valeur sur le Ugreen |
|---|---|
| `DJANGO_ALLOWED_HOSTS` | ajouter l'IP du Ugreen, ex. `localhost,127.0.0.1,192.168.1.60` (garder `dh2o.synology.me` tant que l'accès extérieur passe par là) |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | l'origine HTTPS du reverse proxy si vous en remettez un ; vide en accès direct `http://IP:8000` |
| `GUNICORN_WORKERS` | `4` (ajouter la ligne si absente) |
| `ERP_PORT` | `8000` sauf si ce port est déjà pris sur le Ugreen |
| `DJANGO_COOKIES_SECURISES` | `false` tant que l'accès se fait en HTTP simple |

Ne rien changer à `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DJANGO_SECRET_KEY`.

## Étape 5 — Exporter les données du Synology

Choisissez un moment où personne ne saisit dans l'ERP : tout ce qui est saisi sur le Synology
après l'export ne sera pas sur le Ugreen.

```bash
ssh marc@192.168.1.50
cd /volume1/docker/erp
./exporter-transfert.sh
```

Le script fait un `pg_dump` frais de la base et une archive des fichiers déposés, et affiche le
chemin de l'archive, par ex. `/volume1/docker/erp_sauvegardes/transferts/erp_transfert_20261012_183000.tar.gz`.

Facultatif mais conseillé : arrêter l'ERP sur le Synology pour éviter toute saisie par erreur
(`sudo docker compose stop web`). Les données restent intactes, on peut le relancer à tout moment
avec `sudo docker compose start web`.

## Étape 6 — Importer sur le Ugreen

```bash
ssh marc@192.168.1.60
cd /volume1/docker/erp
scp marc@192.168.1.50:/volume1/docker/erp_sauvegardes/transferts/erp_transfert_20261012_183000.tar.gz .
./importer-transfert.sh erp_transfert_20261012_183000.tar.gz
```

Le script construit l'image et démarre les conteneurs (le premier build prend quelques minutes),
puis remplace la base vide par celle du Synology et restaure les fichiers déposés. Une fois
terminé :

```bash
sudo docker compose ps          # db "healthy", web "running"
sudo docker compose logs -f web # Ctrl+C pour quitter
rm erp_transfert_*.tar.gz       # l'archive contient toute la base : ne pas la laisser traîner
```

## Étape 7 — Vérifier

Ouvrir `http://192.168.1.60:8000/` : se connecter avec votre compte habituel, ouvrir un devis,
générer un PDF, ouvrir une pièce avec un fichier DXF. Comparer quelques chiffres avec le
Synology (nombre de devis, dernière facture).

**Gardez le Synology intact** (ne supprimez ni le projet ni ses volumes) tant que cette
vérification n'est pas faite et qu'une première sauvegarde du Ugreen n'a pas été restaurée avec
succès (étape 9).

## Étape 8 — Mises à jour sur le Ugreen

Identique au Synology :

```bash
cd /volume1/docker/erp
./update-nas.sh claude/project-construction-k7owwb
```

Le script fait une sauvegarde, télécharge la branche, conserve `.env` et `sauvegarde.conf`,
reconstruit et relance. Il demande le mot de passe `sudo` au moment du `docker compose`.

## Étape 9 — Sauvegardes sur le Ugreen

```bash
cd /volume1/docker/erp
cp sauvegarde.conf.exemple sauvegarde.conf
```

Les scripts rangent les sauvegardes dans `/volume1/docker/erp_sauvegardes` (à côté du projet,
donc sur le NVMe). Pour qu'elles aillent sur les IronWolf sans rien changer aux scripts (y compris
la sauvegarde automatique faite par `update-nas.sh`), on remplace ce dossier par un lien :

```bash
ls -d /volume*/erp_sauvegardes    # chemin réel du dossier partagé créé à l'étape 0
ln -s /volume2/erp_sauvegardes /volume1/docker/erp_sauvegardes
```

(à faire avant la première sauvegarde ; si le dossier existe déjà, déplacez son contenu dans
`/volume2/erp_sauvegardes` puis supprimez-le avant de créer le lien.) Premier essai à la main :

```bash
./sauvegarder-nas.sh
./verifier-sauvegarde.sh
```

Planification avec cron (`sudo crontab -e`), si UGOS n'a pas de planificateur de scripts :

```
30 2 * * * cd /volume1/docker/erp && ./sauvegarder-nas.sh >> /volume2/erp_sauvegardes/journal.txt 2>&1
0 6 * * 1 cd /volume1/docker/erp && ./verifier-sauvegarde.sh --age-max 2 >> /volume2/erp_sauvegardes/journal.txt 2>&1
```

Le détail (copie externe chiffrée, alerte en cas d'échec, vérification hebdomadaire) est dans
[SAUVEGARDE.md](SAUVEGARDE.md).

## Interface Docker d'UGOS (facultatif)

L'application Docker d'UGOS affiche les conteneurs `erp-web-1` et `erp-db-1` lancés en SSH :
vous pouvez y voir l'état, les journaux, et ouvrir un terminal. **Ne recréez pas** le projet
depuis l'onglet « Projet » sous un autre nom : Docker créerait de nouveaux volumes vides et l'ERP
démarrerait sur une base vide (les données ne seraient pas perdues, mais invisibles). Les mises à
jour se font avec `update-nas.sh`, en SSH.

## En cas de problème

- `sudo docker compose logs web` : erreurs de démarrage (migrations, `.env`).
- « Bad Request (400) » dans le navigateur : l'IP ou le nom utilisé n'est pas dans
  `DJANGO_ALLOWED_HOSTS`. Corriger `.env` puis `sudo docker compose up -d`.
- Erreur CSRF à la connexion derrière un reverse proxy : renseigner `DJANGO_CSRF_TRUSTED_ORIGINS`.
- Revenir en arrière : relancer l'ERP sur le Synology (`sudo docker compose start web`) ; rien
  n'y a été modifié.
