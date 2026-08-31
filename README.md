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
- [x] **Phase 2 — Chiffrage et planning** (`chiffrage/`) : moteur de
      chiffrage, devis, commande, ordre de fabrication, synchronisation avec
      le planning atelier
- [x] **Phase 3 — Commercial et stock** (`commercial/`, `stock/`,
      `facturation/`) : contacts, emplacements, lots, mouvements de stock,
      alertes de seuil, pont de facturation vers Tiime
- [ ] **Phase 4 — Achats et pilotage** : achats fournisseurs, sous-traitance,
      indicateurs

## Tester sur le Synology NAS (Docker)

Un `Dockerfile` + `docker-compose.yml` sont fournis pour tester l'ERP
directement sur le NAS via Container Manager. Voir le guide détaillé :
[`docs/DEPLOIEMENT_SYNOLOGY.md`](docs/DEPLOIEMENT_SYNOLOGY.md).

Résumé express (en SSH sur le NAS, depuis le dossier du projet) :

```bash
cp .env.example .env   # ajuster DJANGO_SECRET_KEY, DJANGO_ALLOWED_HOSTS, DB_PASSWORD
docker compose up -d --build
docker compose exec web python manage.py createsuperuser
```

Puis ouvrir `http://<ip-du-nas>:8000/admin/`.

## Démarrage local (sans Docker)

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
- API : http://127.0.0.1:8000/api/v1/ (articles, matieres, postes-travail,
  tarifs-poste, nomenclatures, gammes, tiers, adresses, contacts, devis,
  devis-lignes, commandes, ordres-fabrication, emplacements, lots,
  mouvements-stock, alertes-stock, factures...)

## Tests

```bash
python manage.py test
```

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

## Phase 2 — Chiffrage et planning

App `chiffrage`, plus un socle minimal de l'app `commercial` (Tiers, Adresse
— nécessaire à `Devis.client` et `Commande.adresse_*`, complété en Phase 3).

- **Moteur de chiffrage** (`chiffrage/moteur.py`) : calcule le coût matière
  d'une ligne de devis (directement pour une matière première, via la
  nomenclature pour un article fabriqué — toutes les formules `unite_cout`
  du cahier des charges), le coût de chaque étape de gamme (tarif de poste
  valide à la date du devis), et applique la hiérarchie des marges (globale
  devis > défaut poste/article > éditée ligne à ligne). Déclenché par
  l'action admin/API **« Recalculer le chiffrage »**.
- **Lancer en production** (`chiffrage/production.py`) : transforme un devis
  validé en `Commande` + un `OrdreFabrication` par ligne d'article fabriqué
  (gamme et temps figés à cet instant), toujours créés localement même si le
  planning atelier est indisponible.
- **Synchronisation avec le planning atelier** (`chiffrage/planning_sync.py`) :
  aucune API n'étant encore définie côté planning, ce module est le point
  d'intégration unique à brancher plus tard (`PLANNING_API_URL`). En
  attendant, les OF restent `statut_synchro=en_attente` sans jamais bloquer
  leur création. Voir
  [`docs/SYNCHRONISATION_PLANNING.md`](docs/SYNCHRONISATION_PLANNING.md)
  pour la configuration et les tentatives automatiques.

## Phase 3 — Commercial et stock

- **commercial** (complété) : `Contact` s'ajoute à `Tiers`/`Adresse` posés en
  Phase 2.
- **stock** : `Emplacement`, `Lot`, `MouvementStock`, `AlerteStock`. Un
  `MouvementStock` (entrée/sortie) met à jour la quantité de son `Lot` à la
  création (jamais réappliqué sur une édition ultérieure — les mouvements
  sont des écritures de journal, pas des enregistrements modifiables), puis
  réévalue l'alerte de seuil de l'article (`Article.stock_mini`) : ouverture
  automatique si le stock total (tous lots confondus) passe sous le seuil,
  clôture automatique s'il repasse au-dessus — une seule alerte active à la
  fois par article (contrainte base de données). Seuls les articles
  `gere_en_stock=vrai` peuvent avoir des lots.
- **facturation** : `Facture`, simple trace côté ERP liée à une `Commande`
  (`chiffrage`) — la facture légale est créée manuellement dans Tiime, sa
  référence renseignée ensuite ici (`mode_creation=manuel`). Passage à une
  création automatique via API Tiime non implémenté (aucune API publique
  documentée à ce jour, cf. cahier des charges).
