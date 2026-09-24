# ERP maison

ERP interne pour l'atelier, développé en remplacement progressif d'Herakles.
Cahier des charges complet : [`docs/ERP_Specification_Complete_4_Phases.md`](docs/ERP_Specification_Complete_4_Phases.md).

## Stack technique

- **Backend** : Django 5.2 + Django REST Framework
- **Base de données** : PostgreSQL
- **Admin** : interface d'administration Django (CRUD des référentiels)
- **API** : REST (DRF), destinée notamment à la synchronisation avec l'outil de
  planification d'atelier (fraisage/tournage) hébergé sur le NAS Synology

## Avancement — 4 phases

- [x] **Phase 1 — Socle technique** (`technique/`) : Matière, Article, Poste de
      travail, Tarif de poste, Nomenclature, Gamme
- [ ] **Phase 2 — Chiffrage et planning** : devis, ordre de fabrication,
      synchronisation avec le planning atelier
  - [x] **Chiffrage découpe laser / jet d'eau** (`decoupe/`) : import DXF/DWG
        d'une pièce et imbrication dans une surface de tôle donnée
- [ ] **Phase 3 — Commercial et stock** : tiers, adresses, stock, pont de
      facturation vers Tiime
- [ ] **Phase 4 — Achats et pilotage** : achats fournisseurs, sous-traitance,
      indicateurs

## Démarrage local

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env   # ajuster les identifiants PostgreSQL si besoin

# Créer la base et l'utilisateur PostgreSQL (exemple) :
#   createuser erp_user --pwprompt
#   createdb erp_db -O erp_user

python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

- Admin : http://127.0.0.1:8000/admin/
- API Phase 1 : http://127.0.0.1:8000/api/v1/ (articles, matieres,
  postes-travail, tarifs-poste, nomenclatures, gammes)
- API découpe : http://127.0.0.1:8000/api/v1/ (pieces-decoupe, imbrications)

## Tests

```bash
python manage.py test
```

## Déploiement sur un NAS Synology (Docker / Container Manager)

Le dépôt fournit un `Dockerfile` (multi-étapes, robuste aux NAS ARM qui n'ont pas toujours de
roue Python précompilée pour Shapely) et un `docker-compose.yml` (app + PostgreSQL, volumes
persistants pour la base et les fichiers déposés).

1. **Récupérer le projet sur le NAS**, par exemple via `git clone` en SSH (App Git Server
   ou `git` installé via Entware/SynoCommunity), ou en copiant le dossier via File Station.
2. **Créer le fichier d'environnement** : `cp .env.example .env`, puis éditer :
   - `DJANGO_SECRET_KEY` : une vraie valeur aléatoire (ne jamais garder la valeur par défaut)
   - `DJANGO_DEBUG=False`
   - `DJANGO_ALLOWED_HOSTS` : l'IP LAN du NAS (et/ou son nom DSM), ex.
     `192.168.1.50,mon-nas.local`
   - `DJANGO_CSRF_TRUSTED_ORIGINS` : `http://192.168.1.50:8000` (même host que ci-dessus,
     avec le schéma) — nécessaire pour que l'admin accepte les formulaires hors `localhost`
   - `DB_PASSWORD` : un mot de passe PostgreSQL choisi (pas besoin de créer la base à la main,
     le conteneur `db` s'en charge au premier démarrage)
3. **Lancer via Container Manager** : DSM 7.2+ → *Container Manager* → *Projet* → *Créer* →
   pointer sur le dossier du projet (qui contient `docker-compose.yml`) → DSM détecte le
   compose et propose de builder + démarrer les deux services. En ligne de commande (SSH) :
   ```bash
   docker compose up -d --build
   ```
   Le premier démarrage construit l'image (peut prendre plusieurs minutes sur un NAS ARM s'il
   doit compiler Shapely depuis les sources), applique les migrations automatiquement
   (`docker-entrypoint.sh`), puis démarre Gunicorn sur le port `8000` (configurable via
   `ERP_PORT` dans `.env`).
4. **Créer un compte admin** :
   ```bash
   docker compose exec web python manage.py createsuperuser
   ```

**Tester depuis un PC :** une fois les conteneurs démarrés, l'ERP est joignable depuis
n'importe quel appareil du **même réseau local** (ou via VPN si le NAS y est accessible à
distance) à l'adresse `http://<ip-du-nas>:8000/admin/` — pas besoin d'être sur le NAS
lui-même. Vérifier que :
- le pare-feu DSM autorise le port choisi (Panneau de configuration → Sécurité → Pare-feu) ;
- l'IP/nom d'hôte utilisé dans le navigateur figure bien dans `DJANGO_ALLOWED_HOSTS`.

Cet ERP est pensé comme un outil interne au réseau de l'atelier (pas d'exposition Internet
directe) : par simplicité, les fichiers déposés (DXF/DWG) sont servis directement par Django
même hors `DEBUG` (voir `SERVE_MEDIA` dans `config/settings.py`), sans reverse proxy
obligatoire. Le trafic reste donc en HTTP simple sur le LAN, sauf à ajouter soi-même un
reverse proxy HTTPS (ex. via le proxy inversé intégré à DSM) devant le port `8000`.

**Sauvegardes :** les données persistantes vivent dans deux volumes Docker nommés (`db_data`
pour PostgreSQL, `media_data` pour les fichiers DXF/DWG) — à inclure dans la stratégie de
sauvegarde du NAS (Hyper Backup peut sauvegarder les dossers de volumes Docker sous
`/volume1/@docker/volumes/`), ou remplacer les volumes nommés par des montages liés vers un
dossier partagé DSM si vous préférez les parcourir directement dans File Station.

## Phase 1 — Socle technique

App `technique`. Modélise :

- **Matiere** : référentiel des matières (densité, pour le calcul au poids)
- **Article** : table unique matière première / fabriqué (`nature`). Une
  matière première n'a jamais de gamme ni de nomenclature ; un article
  fabriqué n'a pas de coût unitaire stocké, il est recalculé à chaque devis
- **PosteTravail** : centre de charge (mode horaire ou forfaitaire)
- **TarifPoste** : historique des coûts horaires par poste (aucun
  chevauchement de périodes autorisé)
- **Nomenclature** : composants consommés par un article fabriqué
- **Gamme** : suite d'opérations (postes) d'un article fabriqué, historisée
  (aucun chevauchement de révision autorisé pour un même article/poste/ordre)

Les règles métier du cahier des charges sont appliquées via `Model.clean()`
et rejouées côté API (voir `technique/serializers.py`,
`FullCleanModelSerializer`) pour éviter toute duplication de logique entre
l'admin et l'API.

## Chiffrage découpe laser / jet d'eau

App `decoupe`. Permet de chiffrer une pièce découpée (laser ou jet d'eau) à
partir de son fichier de CAO, en calculant combien de feuilles de tôle sont
nécessaires pour une quantité donnée.

- **PieceDecoupe** : import d'un fichier `.dxf` (ou `.dwg`, voir plus bas) —
  la géométrie est extraite automatiquement (surface, longueur totale de
  découpe, rectangle englobant, nombre de trous) et stockée sur la pièce.
  Les entités reconnues sont LINE, LWPOLYLINE/POLYLINE (avec bulges), ARC,
  CIRCLE, ELLIPSE et SPLINE ; les contours peuvent être fermés par une seule
  entité ou par une chaîne de segments disjoints (assemblage par connexité),
  et les imbrications de contours (trous, îlots) sont résolues par une règle
  pair/impair. `rotation_autorisee` (vrai par défaut) indique si la pièce
  peut être pivotée de 90° lors de l'imbrication (à décocher si le sens de
  la matière l'impose).
- **ImbricationJob** : un calcul d'imbrication — dimensions de feuille, marge
  de bord, espacement entre pièces, et une ou plusieurs `ImbricationLigne`
  (pièce + quantité). Le calcul détermine le nombre de feuilles nécessaires,
  place chaque occurrence de pièce (`ImbricationPlacement`), et donne un
  taux d'utilisation matière ainsi qu'une estimation du coût (si l'article
  matière lié a une unité de coût "surface").

**Algorithme d'imbrication.** Il s'agit d'un compactage par étagères
("shelf packing", Best-Fit Decreasing Height) sur le rectangle englobant de
chaque pièce, avec rotation possible par pas de 90° — pas d'une imbrication
polygonale exacte type No-Fit-Polygon. Le taux d'utilisation retourné se base
cependant sur la surface réelle des pièces (issue de leur contour), pas de
leur rectangle englobant, ce qui donne une estimation matière réaliste.
Un aperçu SVG de chaque feuille (silhouettes réelles, pas juste les
rectangles) est disponible via
`GET /api/v1/imbrications/{id}/apercu/{numero_feuille}/`.

**DWG.** Le format DWG est propriétaire ; sa lecture s'appuie sur
l'utilitaire externe gratuit [ODA File
Converter](https://www.opendesignalliance.org/), non installé par défaut —
sans lui, l'import d'un `.dwg` échoue avec un message explicite invitant à
l'installer ou à exporter le fichier en `.dxf` depuis le logiciel de CAO.

Points ouverts, comme pour les chutes de tôle en phase 2 : l'imbrication par
rectangle englobant est une approximation (pas de No-Fit-Polygon), et un
fichier contenant plusieurs contours fermés disjoints ne retient que le plus
grand comme silhouette de la pièce (avertissement sur les autres).
