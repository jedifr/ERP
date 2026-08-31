# Tester l'ERP sur un Synology NAS (Container Manager / Docker)

Le projet est fourni avec un `Dockerfile` et un `docker-compose.yml` : deux
conteneurs (l'application Django/Gunicorn + une base PostgreSQL), sans
dépendance externe. C'est la manière recommandée de le tester sur le NAS,
que le planning atelier existant utilise probablement déjà.

## 1. Prérequis sur le NAS

1. **DSM 7.2 ou supérieur** recommandé (Container Manager avec support des
   projets `docker-compose`). Sur DSM plus ancien, le paquet s'appelle
   **Docker** et n'a pas l'onglet "Projet" — utiliser alors la méthode SSH
   ci-dessous.
2. Installer **Container Manager** (ou **Docker**) depuis le Centre de
   paquets Synology.
3. Activer **SSH** (Panneau de configuration → Terminal & SNMP → Activer le
   service SSH) — nécessaire pour cloner le dépôt et lancer les commandes.

## 2. Récupérer le projet sur le NAS

Connectez-vous en SSH au NAS (`ssh votre_utilisateur@ip-du-nas`). La plupart
des NAS Synology n'ont pas `git` par défaut : le plus simple est de
télécharger l'archive de la branche directement avec `curl`/`tar` (déjà
présents sur DSM) :

```bash
# Choisir un dossier, par exemple un dossier partagé "docker"
cd /volume1/docker
curl -fL -o erp.tar.gz https://codeload.github.com/jedifr/ERP/tar.gz/refs/heads/claude/project-construction-k7owwb
mkdir erp
tar -xzf erp.tar.gz -C erp --strip-components=1
rm erp.tar.gz
cd erp
```

(Utilisez bien `codeload.github.com/.../tar.gz/refs/heads/<branche>` et non
le lien `github.com/.../archive/...` : ce dernier ne fonctionne pas quand le
nom de la branche contient un `/`.)

Alternative sans SSH : télécharger le zip depuis github.com (bouton "Code" →
"Download ZIP") sur votre PC, puis le transférer et l'extraire via **File
Station**.

Si vous installez `git` (paquet **Git Server** depuis Package Center),
`git clone -b claude/project-construction-k7owwb https://github.com/jedifr/ERP.git erp`
fonctionne aussi.

Pour les mises à jour futures, une fois le projet en place, utilisez le
script `update-nas.sh` fourni (voir plus bas) plutôt que de refaire ces
étapes à la main.

## 3. Configurer l'environnement

```bash
cp .env.example .env
vi .env   # ou nano .env
```

Dans `.env`, à minima :

- `DJANGO_SECRET_KEY` : générer une valeur aléatoire (ex.
  `python3 -c "import secrets; print(secrets.token_urlsafe(50))"` sur
  n'importe quelle machine avec Python).
- `DJANGO_ALLOWED_HOSTS` : ajouter l'IP LAN du NAS, ex.
  `192.168.1.50,mon-nas.local`.
- `DB_PASSWORD` : changer le mot de passe par défaut.

## 4. Lancer les conteneurs

Toujours depuis le dossier du projet (`/volume1/docker/erp`) :

```bash
docker compose up -d --build
```

Cela construit l'image de l'application, démarre PostgreSQL, attend qu'il
soit prêt (`wait_for_db`), applique les migrations, collecte les fichiers
statiques, puis lance Gunicorn sur le port **8000**.

Vérifier que tout tourne :

```bash
docker compose ps
docker compose logs -f web
```

## 5. Créer un compte administrateur

```bash
docker compose exec web python manage.py createsuperuser
```

## 6. Accéder à l'ERP

Depuis un poste du réseau local, en accès direct au port du conteneur :

- Admin : `http://<ip-du-nas>:8000/admin/`
- API Phase 1 : `http://<ip-du-nas>:8000/api/v1/`

Si le port 8000 est déjà utilisé sur le NAS (par le planning atelier par
exemple), changez le mappage dans `docker-compose.yml` :

```yaml
services:
  web:
    ports:
      - "8100:8000"   # accessible alors sur http://<ip-du-nas>:8100
```

### Si le NAS utilise déjà le reverse proxy DSM (recommandé si un autre outil
### comme le planning atelier y est déjà exposé en HTTPS)

Dans **Panneau de configuration → Portail de connexion → Avancé → Reverse
Proxy**, créer une règle (par exemple `https://*:441` → `http://localhost:8000`,
sur le modèle de la règle déjà en place pour le planning atelier). L'ERP est
alors accessible en `https://<ip-du-nas>:441/admin/` (certificat auto-signé
par défaut : accepter l'avertissement du navigateur).

**Indispensable dans ce cas** : ajouter l'origine HTTPS du reverse proxy dans
`.env`, sinon la connexion à l'admin échouera avec une erreur CSRF (formulaire
de login refusé) :

```
DJANGO_CSRF_TRUSTED_ORIGINS=https://192.168.1.50:441
```

Puis relancer `docker compose up -d --build` pour prendre en compte le
changement.

## Alternative : via l'interface Container Manager

Sur DSM 7.2+, dans **Container Manager → Projet → Créer**, choisir "Créer
docker-compose.yml" et pointer vers le dossier `/volume1/docker/erp`
(le NAS y détecte automatiquement `docker-compose.yml`). L'interface graphique
permet ensuite de démarrer/arrêter le projet et de consulter les logs, sans
passer par SSH — sauf pour la commande `createsuperuser` (étape 5), qui reste
à faire via SSH ou le terminal intégré à Container Manager (bouton "Détails"
du conteneur → onglet "Terminal").

## Mettre à jour après un nouveau push

Le NAS n'ayant pas `git` (voir plus haut), le plus simple est le script
`update-nas.sh` fourni à la racine du projet : il télécharge la dernière
version de la branche, conserve votre `.env`, bascule, reconstruit et relance
les conteneurs — équivalent en une commande de tout ce qu'on a fait
manuellement à la main jusqu'ici.

```bash
cd /volume1/docker/erp
./update-nas.sh
```

Pour mettre à jour vers une autre branche : `./update-nas.sh nom-de-la-branche`.

Si `docker compose up --build` échoue après la bascule (ex. erreur de build),
le script s'arrête **avant** de supprimer l'ancienne version : un dossier
`erp_old` reste disponible juste à côté pour revenir en arrière au besoin
(`rm -rf erp && mv erp_old erp`).

Si vous avez installé `git` (paquet Git Server) : `git clone` puis `git pull`
fonctionnent aussi normalement, suivi de `docker compose up -d --build`.

Dans tous les cas, les migrations sont réappliquées automatiquement au
redémarrage du conteneur `web` (aucune donnée perdue — les données
PostgreSQL sont conservées dans un volume Docker nommé `erp_postgres_data`,
non touché par une mise à jour du code).

## Sauvegarde des données

Les données vivent dans le volume Docker `erp_postgres_data`. Pour une
sauvegarde simple :

```bash
docker compose exec db pg_dump -U erp_user erp_db > backup_$(date +%F).sql
```

## Notes

- Ce déploiement sert à **tester** l'ERP sur le réseau local. Pour une mise
  en production durable (accès distant, sauvegardes planifiées, HTTPS...),
  prévoir une étape ultérieure dédiée.
- Le port 8000 n'est exposé que sur le réseau local par défaut (pas
  d'exposition Internet automatique) : aucune configuration supplémentaire
  n'est nécessaire pour un simple test entre postes du réseau.
