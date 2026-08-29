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
- [ ] **Phase 3 — Commercial et stock** : tiers, adresses, stock, pont de
      facturation vers Tiime
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
- API Phase 1 : http://127.0.0.1:8000/api/v1/ (articles, matieres,
  postes-travail, tarifs-poste, nomenclatures, gammes)

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
