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

Connectez-vous en SSH au NAS (`ssh votre_utilisateur@ip-du-nas`), puis :

```bash
# Choisir un dossier, par exemple un dossier partagé "docker"
cd /volume1/docker
git clone -b claude/project-construction-k7owwb https://github.com/jedifr/ERP.git erp
cd erp
```

Si `git` n'est pas disponible sur le NAS, téléchargez le zip du dépôt sur
votre PC et transférez-le sur le NAS via **File Station**, puis décompressez-le
dans `/volume1/docker/erp`.

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

Depuis un poste du réseau local :

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

## Alternative : via l'interface Container Manager

Sur DSM 7.2+, dans **Container Manager → Projet → Créer**, choisir "Créer
docker-compose.yml" et pointer vers le dossier `/volume1/docker/erp`
(le NAS y détecte automatiquement `docker-compose.yml`). L'interface graphique
permet ensuite de démarrer/arrêter le projet et de consulter les logs, sans
passer par SSH — sauf pour la commande `createsuperuser` (étape 5), qui reste
à faire via SSH ou le terminal intégré à Container Manager (bouton "Détails"
du conteneur → onglet "Terminal").

## Mettre à jour après un nouveau push

```bash
cd /volume1/docker/erp
git pull
docker compose up -d --build
```

Les migrations sont réappliquées automatiquement au redémarrage du conteneur
`web` (aucune donnée perdue — les données PostgreSQL sont conservées dans un
volume Docker nommé `erp_postgres_data`).

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
