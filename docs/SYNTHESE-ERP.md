# Synthèse de l'ERP DÉCOUPE H2O

> État au 11/10/2026. Sources : code de la branche `claude/project-construction-k7owwb` (commit `41b382e`, fichier `VERSION` = 2026.10.14.9), branches `main` et `claude/exciting-goldberg-igedwe`, `README.md` (journal de chaque évolution), `docs/`, 185 commits, dépôt `jedifr/Planning` et sessions Claude Code des 29/08 au 11/10. Les sessions longues n'ont été relues que par échantillons : leur contenu est retracé par les commits et le README. Tout point non vérifiable est marqué **À vérifier**.

## 1. Objectif et périmètre

**Objectif.** ERP interne qui remplace progressivement Herakles (`README.md` l. 3) pour l'atelier DÉCOUPE H2O : chiffrage, devis, commandes, fabrication, livraisons, factures, achats, stock, sous-traitance, comptabilité, pilotage. Il doit rester compatible avec le planning atelier (`jedifr/Planning`), qui garde l'ordonnancement et le pointage (`docs/ERP_Specification_Complete_4_Phases.md`, « Contexte »).

**Pour qui.** Les profils prévus sont commercial, atelier, comptabilité et direction (`comptes/groupes.py`, `docs/RELECTURE_PRODUCTION.md` §6). L'ERP s'utilise sur le réseau local du NAS.

**Ce qu'il ne fait pas :**
- l'ordonnancement et le suivi d'atelier, qui restent dans Planning ;
- l'émission légale des factures (plateforme agréée, Tiime) : il produit factures, avoirs et Factur-X, mais la facture légale « reste faite à côté » (`docs/RELECTURE_PRODUCTION.md` §4) ;
- la paie et les congés ;
- l'imbrication exacte par No-Fit-Polygon : le calcul est heuristique (`decoupe/services/imbrication*.py`).

**État.** Pas encore en production. Les documents commerciaux de test seront purgés au démarrage (`docs/BACKLOG.md`).

## 2. Stack et architecture

- **Langage et frameworks :** Python 3.12 (`Dockerfile`), Django 5.2.17, Django REST Framework 3.18. L'interface est l'admin Django habillée par django-unfold 0.91, avec de nombreux écrans et scripts JS spécifiques (`requirements.txt`).
- **Bibliothèques métier :** ezdxf (DXF), shapely (géométrie), WeasyPrint et ReportLab (PDF), factur-x, django-simple-history (historique), GrapesJS embarqué (`documents/static/documents/vendor/`).
- **Base de données :** PostgreSQL 16 (`docker-compose.yml`, `config/settings.py` l. 140). Les clés primaires sont souvent des chaînes naturelles : `Article.reference`, `PosteTravail.nom`, `Matiere.nom` et `numero` pour les documents (`*/models.py`).
- **Volume :** environ 59 600 lignes Python, 175 migrations et 1 315 fonctions de test.
- **Déploiement :** Docker Compose (services `db` et `web` ; gunicorn, 3 workers, délai de 300 s) sur NAS. Le NAS actuel est un Synology ; la cible est un UGREEN DXP8800 Plus sous UGOS Pro, qui n'a pas encore été essayé (`docs/SAUVEGARDE.md`, `docs/RELECTURE_PRODUCTION.md` l. 3). Le code arrive sur le NAS en archive via `update-nas.sh [branche]`, sans `.git`. La version affichée provient du fichier `VERSION` (`comptes/version.py`).

```
config/         réglages (settings.py : variables d'environnement, UNFOLD, REST, PLANNING_*), urls.py
technique/      socle : Matiere, FamilleMatiere, Article, PosteTravail, TarifPoste, Gamme, GammeType, Nomenclature,
                ProfileSection, RegleCreationTole ; éditeurs de gamme et de nomenclature
decoupe/        découpe laser/jet d'eau : PieceDecoupe, FormatTole, ParametreCoupe, VitesseCoupe, imbrications ;
                services/ (geometrie, temps, vitesses, matiere, imbrication*, profiles, formes, lua_materiaux) ; data/ (bases de coupe)
chiffrage/      Devis, DevisLigne(+Operation), Commande, Livraison, OrdreFabrication, OperationOF ; moteur.py, reglage.py,
                builder*.py (ajout de ligne), production.py, planning_sync.py, documents.py (PDF)
commercial/     Tiers, Adresse, Contact, TauxTVA, Devise, Pays, ConditionPaiement ; import de tiers
stock/          Emplacement, Lot, MouvementStock (immuable), Transfert, Inventaire, AlerteStock, traçabilité
achats/         ArticleFournisseur, TarifAchatArticle, CommandeFournisseur, Reception, FactureFournisseur
soustraitance/  EnvoiSousTraitance, RetourSousTraitance
facturation/    Facture/avoir, FactureLigne, RelanceFacture, facturx.py
comptabilite/   plan comptable, journaux, écritures, postes de gestion, export ISACOMPTA
pilotage/       services sans table : marge réelle, taux de charge
documents/      modèles PDF modifiables (GrapesJS → WeasyPrint)
codification/   numérotation paramétrable (préfixe + compteur)
comptes/        société, droits, accueil, paramétrage, « À compléter », lots de modification, montants.py, sécurité
docs/           cahier des charges, déploiement, sauvegarde, synchro planning, relecture production, backlog
*.sh            update / sauvegarde / vérification / restauration / transfert NAS
```

## 3. Modèle de données

- **Socle** (`technique/models.py`). `Article` est une table unique, distinguée par `nature` : matière première, fabriqué, service acheté, consommable ou composant. Ses champs clés sont `unite_cout` (m², m, kg, pièce), `cout_unitaire`, `matiere`, `epaisseur`, `taux_marge_defaut` et `gere_en_stock`. Un `Matiere` (nuance, densité, usinabilité) se rattache à une `FamilleMatiere`. `PosteTravail` porte `mode_calcul` (horaire ou forfaitaire), `nombre_machines`, la marge et la mise en place d'une tôle ; `TarifPoste` contient son coût horaire, historisé par dates. `Nomenclature` relie un parent à un composant, avec longueur, largeur et quantité. `Gamme` contient les étapes (poste, ordre, `temps_fixe` et `temps_variable` en minutes, `cout_forfaitaire`, `origine` « decoupe » ou saisie), historisées par dates.
- **Découpe** (`decoupe/models.py`). `PieceDecoupe` reprend le DXF et sa géométrie, avec la matière, l'épaisseur, le procédé, le gaz, la tôle, le format et le devis. `ParametreCoupe` est défini par famille ou nuance, épaisseur, procédé et gaz, avec ses vitesses, son perçage, son poste et son origine (relevé ou calculé). `FormatTole` porte une priorité et des restrictions par matière. Les autres modèles sont `ImbricationJob` (avec ses lignes et placements), `PieceProfile` (débit de profilé), `ReglageProcede` et `NormeCote`.
- **Vente et production** (`chiffrage/models.py`). Un `Devis` (statut, issue, révision `devis_origine`, `taux_marge_globale`, client, adresses, contact) contient des `DevisLigne`. Chaque ligne porte l'article, la quantité, le coût matière calculé, les taux de marge appliqués, un prix forcé et la TVA, ainsi que ses `DevisLigneOperation` (coût calculé, marge, prix). Une `Commande` (statut en cours, soldée ou annulée) contient des `CommandeLigne`, dont les modifications sont tracées. Une `Livraison` contient des `LivraisonLigne` avec leur lot. Un `OrdreFabrication` (`statut_synchro`, nombre de tentatives, empreinte envoyée) regroupe des `ComposantOF` et des `OperationOF` (`temps_reel`, `quantite_bonne`, `quantite_rebut`).
- **Facturation et comptabilité.** Une `Facture` (facture ou avoir, `facture_origine`, statut de paiement, verrouillage) contient des `FactureLigne` liées à une `CommandeLigne`. Une `EcritureComptable` se compose de `LigneEcriture` (compte, code analytique) et se rattache à une facture de vente ou fournisseur (`comptabilite/models.py`).
- **Achats et stock.** Une `CommandeFournisseur` contient des lignes, éventuellement liées à une alerte de stock ou à une ligne client ; une `Reception` contient des lignes avec lot et emplacement. Un `Lot` (article, emplacement) reçoit des `MouvementStock`, qui sont immuables : on les corrige par un mouvement d'annulation (`stock/models.py`).
- **Transverse.** `comptes/models.py` contient `Societe`, `LotModification` (journal annulable des modifications en masse) et `EvenementConnexion` ; `codification/models.py` contient `RegleCodification`. L'historique simple-history couvre les modèles principaux.

## 4. Modules et état d'avancement

| Module | État | Fichiers principaux |
|---|---|---|
| Socle technique : articles, matières, postes, tarifs, gammes, nomenclatures | Fait | `technique/` |
| Devis : révisions, onglets, ajout de ligne, PDF, prochaine action | Fait | `chiffrage/models.py`, `admin.py`, `builder*.py`, `documents.py`, `comptes/prochaine_action.py` |
| Chiffrage (moteur, marges, matière par imbrication, réglage par tôle) | Fait | `chiffrage/moteur.py`, `reglage.py`, `decoupe/services/matiere.py` |
| Découpe jet d'eau : temps, vitesses, import IGEMS | Fait, estimations à recaler | `decoupe/services/temps.py`, `vitesses.py`, `lua_materiaux.py` |
| Découpe laser fibre (table du constructeur) | Fait, coefficient 0,85 à recaler | `decoupe/data/base_coupe_laser.json`, `decoupe/services/temps.py` |
| Imbrication tôles (forme réelle, quinconce, formats, parallélisme) | Fait | `decoupe/services/imbrication*.py`, `parallele.py` |
| Profilés : débits en barres, catalogue ArcelorMittal | Fait | `decoupe/services/profiles.py`, `decoupe/profiles_catalogue_arcelor.py` |
| Pliage, soudure, fraisage, tournage, électroérosion | Partiel : temps saisis à la main dans la gamme ou une gamme type, sans calcul ; lignes de pliage du DXF lues mais non chiffrées ; électroérosion absente du code (**À vérifier**) | `technique/gamme_editeur.py`, `GammeType`, `decoupe/services/geometrie.py` |
| Commandes clients : AR, bon de préparation, lignes modifiables | Fait | `chiffrage/models.py`, `etapes.py` |
| Ordres de fabrication, envoi au planning | Partiel : envoi codé mais jamais branché ; retour absent | `chiffrage/production.py`, `planning_sync.py` |
| Livraisons (partielles, lots) | Fait | `chiffrage/models.py` (`Livraison`) |
| Facturation : factures, avoirs, relances, PDF, Factur-X | Partiel : émission légale non branchée | `facturation/` |
| Comptabilité : écritures, autoliquidation, export ISACOMPTA | Fait | `comptabilite/`, `achats/generation.py` |
| Achats : commandes, réceptions multi-lots, factures fournisseur, tarifs historisés | Fait | `achats/` |
| Stock (désactivable) : lots, coulées, inventaire, alertes, traçabilité | Fait | `stock/`, `DJANGO_STOCK_ACTIF` |
| Sous-traitance (conformité simple) | Fait | `soustraitance/` |
| Pilotage : marge réelle, taux de charge | Partiel : faute de temps réels remontés, le calcul reste vide | `pilotage/services.py` |
| Modèles PDF personnalisables | Fait | `documents/` |
| Droits, sécurité, accueil, paramétrage, « À compléter », lots | Fait | `comptes/` |
| Sauvegarde et transfert NAS | Fait, jamais exécuté sur un NAS | `*.sh`, `docs/SAUVEGARDE.md` |
| Lecture STEP | Partiel : prototype sur une branche non fusionnée, cadquery absent de `requirements.txt` | `technique/step.py` (branche `exciting-goldberg-igedwe`) |
| Import OCR | À faire : aucune trace dans le dépôt | **À vérifier** |
| Interface hors admin Django | À faire : décision reportée | `README.md` l. 32-36 |
| Reprise des données Herakles | À faire : seul l'import CSV de tiers existe ; le transfert d'articles et d'historique est **À vérifier** | `commercial/import_tiers.py` |

## 5. Règles métier (chiffrage)

- **Chaîne de calcul** (`chiffrage/moteur.py`). Une ligne passe par le coût matière, puis le coût des opérations (étapes de gamme actives à `date_creation` du devis), puis la marge, et donne le prix de vente.
- **Opération horaire** : `(temps_fixe + temps_variable × qté) / 60 × coût horaire`. Le tarif retenu est celui du poste valide à la date. Les temps sont en minutes : un bug de facteur 60 a été corrigé (`README.md` « Correctif majeur »). Une étape de découpe ne compte pas de réglage par lot.
- **Opération forfaitaire** : `cout_forfaitaire × qté`, utilisé pour la sous-traitance.
- **Matière d'un article simple** : `qté × cout_unitaire`.
- **Composants de nomenclature :**
  - au m² : L × l ;
  - au mètre : L, ou L × poids linéique pour un profilé vendu au kilo ;
  - au kilo : L × l × épaisseur × densité ;
  - à la pièce : quantité × coût.
- **Matière d'une pièce découpée** (`decoupe/services/matiere.py`). La surface consommée compte les feuilles pleines plus la seule bande entamée de la dernière. Surface facturée = pièces + chutes × (1 − part récupérable). Le coût vaut surface facturée × prix de la tôle (au m², au kilo ou à la feuille). Le coût unitaire dépend donc de la quantité. Les profilés suivent la même logique en longueur, avec un trait de scie (`profiles.py`).
- **Temps jet d'eau** (`decoupe/services/temps.py`, `vitesses.py`). Le calcul cumule coupe par segment (vitesse selon le rayon et paliers), coins, amorces, perçage, marquage et 2,75 s de déplacement par contour, puis applique un coefficient d'ajustement. Les vitesses estimées suivent `V = 33,569 × g(qualité) × U^1,0912 / e^1,0988`. Ce modèle a été calé sur 9 temps réels, avec un écart maximal de 1,2 %. Les vitesses relevées sur la machine ne sont jamais écrasées.
- **Temps laser :** longueur ÷ (vitesse de production × 0,85) + un perçage par contour + 1 s par contour. Une épaisseur absente de la table est refusée.
- **Imbrication :**
  - écart entre pièces : 6 mm au jet d'eau ; au laser, max(10 mm, épaisseur) ;
  - capacité des machines : 4000 × 2000 au jet d'eau, 3000 × 1500 au laser ;
  - les formats sont essayés par priorité 1, 2 puis 3, en passant au niveau suivant si le taux de chutes dépasse 20 % (`README.md` « Formats de tôle »).
- **Réglage machine** (`chiffrage/reglage.py`). La mise en place se compte une fois par tôle posée, puis se répartit entre les pièces au prorata de leur temps de coupe.
- **Marges.** Le taux global du devis l'emporte ; sinon le taux saisi sur la ligne ou l'opération ; sinon celui de l'article (matière) ou du poste (opérations). Un prix unitaire forcé remplace le prix matière calculé.
- **Arrondis** (`comptes/montants.py`). Le calcul se fait en Decimal avec arrondi commercial au demi supérieur. Les montants ont 2 décimales, les prix et coûts unitaires 4, les taux 2. L'arrondi s'applique ligne par ligne. Sur la facture, la TVA se calcule par taux sur le HT total (EN 16931).
- **Hypothèses à confirmer :**
  - les consommables (gaz, électricité, abrasif) ne sont pas chiffrés à part : la consommation de gaz et la puissance sont stockées mais jamais utilisées (`decoupe/models.py` l. 592) ; ils sont supposés inclus dans le taux horaire du poste (**À vérifier**) ;
  - les chutes de tôle ne sont pas gérées en stock ;
  - le taux de charge est calculé sur 7 h par jour et par machine, du lundi au vendredi (`pilotage/services.py`).

## 6. Intégrations

| Intégration | Sens et format | Point d'entrée | État |
|---|---|---|---|
| Planning atelier | ERP vers Planning : `POST {PLANNING_API_URL}/ordres-fabrication`, JSON `{numero, article, quantite, date_lancement, operations[]}`, en-têtes `Idempotency-Key` et Bearer ; reprises automatiques, commande `retry_sync_ordres_fabrication` | `chiffrage/planning_sync.py`, `docs/SYNCHRONISATION_PLANNING.md` | Non branché. Planning (v1.124.5, Node/Express et SQLite) n'expose aucune route de ce type ; il importe les commandes par fichier (« import personnalisé » : référence, poste, pièce, quantité, temps). Le retour Planning vers ERP (`temps_reel`, quantités) n'existe pas. |
| DXF / DWG | Fichier vers pièce ; classement des calques en découpe, gravure, pliage ou ignoré par profil d'import | `decoupe/services/geometrie.py` | DXF fait. DWG exige ODA File Converter, absent de l'image Docker. |
| IGEMS `materials.lua` | Fichier vers paramètres de coupe | `decoupe/services/lua_materiaux.py` | Fait |
| Fichiers STEP | Fichier vers dimensions, volume et masse (cadquery) | `technique/step.py`, branche non fusionnée | Prototype non relié au devis |
| Import OCR | — | — | Aucun code dans l'ERP. Une session du 13/09 a porté sur un module Dolibarr qui remplace ScanInvoices, hors de ce dépôt (**À vérifier** si lié) |
| Comptable | Export ZIP ISACOMPTA (ventes, achats, banque), texte à largeur fixe | `comptabilite/isacompta.py`, `export_comptable.py` | Fait |
| Facture électronique | PDF Factur-X EN 16931 | `facturation/facturx.py` | Validé par deux validateurs en ligne ; non transmis |
| API publiques | Adresse (`api-adresse.data.gouv.fr`) et entreprises (`recherche-entreprises.api.gouv.fr`) | `commercial/static/commercial/*.js` | Fait |
| E-mail | SMTP pour les relances et la synthèse quotidienne ; écrit dans les journaux si non configuré | `facturation/relances.py`, `comptes/synthese.py` | Fait |
| API REST | `/api/v1/…`, avec les droits du modèle par défaut | `*/urls.py`, `comptes/permissions.py` | Fait |

## 7. Décisions d'architecture

1. **Admin Django (unfold) comme interface**, plutôt qu'un front séparé, pour livrer vite. Le choix d'une vraie interface est reporté (`README.md` l. 32-36).
2. **Deux bases ERP et Planning synchronisées par API**, plutôt qu'une fusion jugée risquée. L'ERP fait foi pour les postes et les tarifs (cahier des charges, phase 2).
3. **Article unique avec `nature`.** Le coût d'un fabriqué n'est jamais stocké : il est recalculé à chaque devis (cahier des charges, phase 1).
4. **Historisation par plages de dates** (tarifs de poste, gammes, tarifs d'achat), pour recalculer un ancien devis avec les taux de l'époque (`DateRangeHistoriqueMixin`, `technique/models.py` l. 486).
5. **Règles dans `Model.clean()`**, rejouées par l'API via `FullCleanModelSerializer`, pour éviter de les dupliquer (`README.md` « Phase 1 »).
6. **Decimal pour l'argent** au lieu de flottants, avec un test garde-fou `MontantsDecimalTests`.
7. **Pas de Celery ni de Redis :** les tâches périodiques passent par le planificateur du NAS et des commandes `manage.py` (`docs/SYNCHRONISATION_PLANNING.md`).
8. **Imbrication heuristique** (étagères, MaxRects, trame à la forme réelle, quinconce) en processus parallèles. Le No-Fit-Polygon a été écarté, ainsi que le glissement en diagonale (gain inférieur à 1 % pour un calcul 2 à 3 fois plus long ; `README.md` « Quinconce »).
9. **Gabarits PDF dans un mini-langage**, sans Django ni Jinja, pour qu'un modèle modifiable par un utilisateur ne puisse rien exécuter sur le serveur (`documents/moteur.py`).
10. **Facturation légale externalisée** (Tiime ou plateforme agréée). L'automatisation par API a été écartée faute d'API publique documentée.
11. **Stock optionnel**, par société et par article. **Mouvements de stock immuables**, à la manière d'un journal.
12. **Clés naturelles en chaîne.** Renommer un article passe donc par une recréation avec repointage (`README.md` « Renommer un article »).
13. **Déploiement par archive et `VERSION`**, car le NAS n'a pas `.git` (`comptes/version.py`).

## 8. Conventions

- **Nommage :** français partout (modèles, champs, fonctions, messages, commits), `verbose_name` accentués. Les services métier sont dans `<app>/services*.py` et lèvent des exceptions dédiées (`ChiffrageError`, `ErreurMatiere`, `ErreurTemps`…). Les docstrings de module expliquent les formules.
- **Tests :** Django `TestCase` dans `<app>/tests*.py`, lancés par `python manage.py test`. Un test vérifie que chaque section du Paramétrage a son aide (`comptes/aides.py`).
- **Documentation :** chaque évolution ajoute une section au `README.md`, qui sert de journal (3 238 lignes). Le fichier `VERSION` s'incrémente à la main, au format `AAAA.MM.JJ.n`, mais ses valeurs ne suivent pas le calendrier (**À vérifier**).
- **Branches :** Claude travaille sur des branches `claude/*`. La branche active est `claude/project-construction-k7owwb`, celle que le NAS déploie. `main` a reçu 3 PR (la dernière le 24/09) et reste à 113 commits derrière. Il n'y a pas de CI ni de `CLAUDE.md`.
- **Migrations :** une migration Django par changement, parfois avec des données (bases de coupe, catalogue de profilés). La relecture contrôle `makemigrations --check` (`docs/RELECTURE_PRODUCTION.md` §2).

## 9. Lancer, tester, déployer

- **Prérequis :** Python 3.12, PostgreSQL 16, GEOS, Pango et HarfBuzz (pour WeasyPrint). Les variables sont dans `.env`, selon `.env.example` : `DJANGO_SECRET_KEY=<clé>`, `DB_PASSWORD=<mot de passe>`, `DJANGO_ALLOWED_HOSTS=<ip-nas>`, `PLANNING_API_URL=<url>`…
- **En local :**
  ```bash
  python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
  cp .env.example .env
  python manage.py migrate && python manage.py createsuperuser && python manage.py runserver
  ```
- **Tests :**
  ```bash
  python manage.py collectstatic --noinput
  python manage.py test
  ```
  Sans `collectstatic`, les tests de pages échouent (« Missing staticfiles manifest entry »). Dans ce cas, `--parallel` masque les échecs derrière une erreur `pickle` : lancer en série.
- **NAS :**
  ```bash
  cp .env.example .env
  docker compose up -d --build
  docker compose exec web python manage.py createsuperuser
  ```
  Pour mettre à jour : `./update-nas.sh claude/project-construction-k7owwb`. Au démarrage, `entrypoint.sh` lance migrate, populate_history, collectstatic et `verifier_securite`.
- **Tâches planifiées :**
  - `retry_sync_ordres_fabrication` toutes les 15 minutes ;
  - `./sauvegarder-nas.sh` chaque nuit ;
  - `./verifier-sauvegarde.sh --age-max 2` chaque semaine ;
  - `synthese_quotidienne`.
- **Mise en production :**
  ```bash
  python manage.py preparer_production --confirmer --sauvegarde-faite
  ```
  Lancée sans option, la commande liste seulement ce qui serait supprimé. Voir aussi `docs/RELECTURE_PRODUCTION.md` §3 et §6.

## 10. Dette technique, bugs connus, points fragiles

- **Branches divergentes.** `main` est en retard de 113 commits et contient un PDF de 6 Mo (`arcelor-catalogue-2020.pdf`) absent de la branche active. Le STEP est isolé sur une branche.
- **Planning jamais branché.** Le format envoyé suppose une API que Planning n'a pas. Sans retour d'informations, le pilotage reste vide.
- **Estimations non recalées :** le coefficient laser de 0,85, les vitesses de jet d'eau calculées, et les tables normalisées (brides, rondelles, profilés hors catalogue) livrées « non vérifiées » (`docs/RELECTURE_PRODUCTION.md` §4).
- **Sauvegarde jamais exécutée sur le NAS**, et aucun essai sur le UGREEN.
- **Sécurité.** `settings.py` a des valeurs de repli de développement (clé et mot de passe) à remplacer dans `.env`. Le conteneur tourne en root, sans `HEALTHCHECK`.
- **Performance.** Une grosse imbrication peut prendre plusieurs dizaines de secondes (délai gunicorn porté à 300 s). Les pastilles du menu sont recomptées à chaque page.
- **Fichiers très lourds :** `README.md` (274 Ko), `chiffrage/tests.py` (5 513 lignes), `chiffrage/admin.py` (1 585 lignes).
- **Limites fonctionnelles :**
  - DWG sans convertisseur dans l'image ;
  - option « miroir » sans effet ;
  - un article créé depuis un devis peut être modifié par tous les devis qui l'utilisent (voulu) ;
  - les écritures déjà générées ne se régénèrent pas.
- **Tests du 11/10 :** 1 320 tests passés, 21 ignorés, aucun échec (520 s, PostgreSQL 16, Python 3.13 local, après `collectstatic`).

## 11. Prochaines étapes (classées)

1. **Consolider les branches** : fusionner `project-construction` dans `main`, trancher le sort du PDF et de la branche STEP, ajouter un `CLAUDE.md` et une CI qui lance les tests. Effort **S**.
2. **Fixer le contrat ERP ↔ Planning** : soit l'ERP exporte le fichier de l'import personnalisé, soit Planning reçoit une route `/ordres-fabrication` idempotente. Implémenter ensuite le retour des temps réels et des quantités. Effort **L**.
3. **Installer et valider sur le UGREEN** : `.env` sécurisé, sauvegarde, restauration d'essai et planification. Effort **M**.
4. **Recaler les temps de coupe** sur des relevés réels, laser et jet d'eau, puis vérifier les tables normalisées. Effort **S/M**, selon les données à fournir.
5. **Chiffrer les autres procédés** : pliage (nombre de plis lu dans le DXF), soudure et électroérosion. Il faut d'abord définir les règles. Effort **M**.
6. **Reprendre les données d'Herakles** : articles, tiers, gammes, tarifs, puis lancer `preparer_production`. Effort **M**.
7. **Brancher le STEP dans le devis** : cadquery dans l'image, dimensions et masse vers l'article et la nomenclature. Effort **M**.
8. **Facturation légale** : choisir la plateforme agréée et définir le circuit des Factur-X. Effort **M/L**.
9. **Alléger** `README.md`, `chiffrage/tests.py` et `chiffrage/admin.py` en documents et modules thématiques. Effort **S/M**.

## 12. Questions ouvertes (décisions de Marc)

1. Quelle branche fait foi, et faut-il fusionner maintenant dans `main` ?
2. Planning : faut-il adapter l'ERP à l'import par fichier existant, ou ajouter une API dans Planning ? L’ERP fait-il bien foi pour les postes et tarifs, comme prévu au cahier des charges ?
3. Pliage, soudure, fraisage, tournage, électroérosion : faut-il un calcul de temps automatique, ou la saisie de gamme suffit-elle ? Si oui, avec quelles règles ?
4. Les consommables (gaz, abrasif, électricité) sont-ils inclus dans le taux horaire, ou doivent-ils être chiffrés à part ?
5. Quelles données reprendre d'Herakles, et sous quel format d'export ?
6. Quelle plateforme agréée pour la facturation électronique (Tiime ?), et quand ?
7. Import OCR : de quels documents s'agit-il (factures fournisseurs ?), et le module Dolibarr est-il concerné ?
8. Faut-il une interface hors admin Django, et pour quels profils (atelier, mobile) ?
9. Quelle date de mise en production, et la bascule vers le UGREEN se fait-elle avant ?

## Brief de démarrage

```
Projet : ERP maison de DÉCOUPE H2O (dépôt jedifr/ERP), Django 5.2 + DRF + PostgreSQL 16, admin django-unfold, Docker sur NAS (cible UGREEN DXP8800 Plus).
Branche de travail active : claude/project-construction-k7owwb (main a 113 commits de retard) ; lire d'abord docs/SYNTHESE-ERP.md, puis docs/ERP_Specification_Complete_4_Phases.md.
Code en français ; règles métier dans Model.clean() et <app>/services ; montants en Decimal (comptes/montants.py) ; chaque évolution ajoute une section au README et incrémente VERSION.
Chiffrage : chiffrage/moteur.py (matière + opérations + marges), découpe laser/jet d'eau dans decoupe/services (temps, imbrication, matière).
Tests : python manage.py collectstatic --noinput && python manage.py test. Déploiement : ./update-nas.sh <branche>.
Pas en production : documents de test supprimables (preparer_production) ; jamais de secret dans le dépôt.
Planning atelier = dépôt jedifr/Planning (Node/SQLite) : synchro ERP→Planning codée (chiffrage/planning_sync.py) mais non branchée, retour absent.
Priorités : 1) consolider les branches + CLAUDE.md + CI ; 2) contrat ERP↔Planning ; 3) installation et sauvegarde sur le UGREEN ; 4) recalage des temps de coupe.
Ne rien inventer : demander à Marc toute règle de chiffrage non écrite (pliage, soudure, consommables).
```
