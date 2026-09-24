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
