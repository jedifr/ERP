# ERP maison

ERP interne pour l'atelier, développé en remplacement progressif d'Herakles.
Cahier des charges complet : [`docs/ERP_Specification_Complete_4_Phases.md`](docs/ERP_Specification_Complete_4_Phases.md).

## Stack technique

- **Backend** : Django 5.2 + Django REST Framework
- **Base de données** : PostgreSQL
- **Admin** : interface d'administration Django habillée avec
  [django-unfold](https://github.com/unfoldadmin/django-unfold) (thème,
  navigation latérale par module, dashboard)
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
- [x] **Phase 4 — Achats et pilotage** (`achats/`, `soustraitance/`,
      `pilotage/`) : commandes fournisseur et réceptions, envois/retours de
      sous-traitance, marge réelle vs prévue, taux de charge des postes

Les 4 phases du cahier des charges sont posées. Reste, hors périmètre des 4
phases : une interface plus soignée que l'admin Django (voir plus bas,
décision volontairement reportée), la synchronisation retour du planning
atelier (Planning → ERP), et les points listés dans « Points encore ouverts »
du cahier des charges.

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
  mouvements-stock, alertes-stock, factures, commandes-fournisseur,
  receptions, envois-sous-traitance, retours-sous-traitance,
  pilotage/marge-reelle/{numero_of}/, pilotage/taux-charge/{poste}/...)

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

## Phase 4 — Achats et pilotage

- **achats** : `CommandeFournisseur`, `LigneCommandeFournisseur`,
  `Reception`, `ReceptionLigne`. Une ligne de commande liée à une
  `AlerteStock` (`alerte_stock_origine`) la clôture automatiquement à sa
  création. Une `ReceptionLigne` met à jour le cumul `quantite_recue` de sa
  ligne de commande et génère un `MouvementStock` en entrée — sur le lot
  unique de l'article (convention actuelle du module stock) : une erreur
  explicite est levée s'il n'existe aucun lot, ou plusieurs (réception
  automatique non applicable dans ce cas, à traiter manuellement).
- **soustraitance** : `EnvoiSousTraitance`, `RetourSousTraitance` — distincts
  du chiffrage (poste "Sous-Traitance" en mode forfaitaire, Phase 1). Un
  retour alimente `quantite_bonne`/`quantite_rebut` sur l'`OperationOF`
  correspondante (retours partiels cumulables) ; une fois la quantité
  envoyée intégralement retournée, l'opération passe au statut `terminee`
  et l'OF peut être considéré comme prêt pour l'étape suivante.
- **pilotage** : aucune nouvelle table (cahier des charges) — fonctions de
  service dans `pilotage/services.py`, exposées en lecture seule via l'API :
  - `marge_reelle_ordre_fabrication(of)` — recalcule le coût réel à partir
    des données remontées sur `OperationOF` (`temps_reel` pour les postes
    horaires, coût figé au devis pour les postes forfaitaires dont le prix
    ne varie pas), comparé à la marge prévue au devis (prix de vente resté
    figé). `donnees_completes=False` tant que toutes les opérations
    horaires n'ont pas remonté leur `temps_reel`.
  - `taux_charge_poste(poste, date_debut, date_fin)` — temps réel cumulé
    rapporté à la capacité disponible (`nombre_machines` × jours ouvrés ×
    heures/jour/machine). Le cahier des charges ne précise pas la base de
    calcul de la capacité (jours ouvrés, heures/jour) : approximation
    lundi-vendredi à 7h/jour/machine, ajustable par appel de la fonction.

## Interface — habillage de l'admin

L'admin Django est thémé avec **django-unfold** (`config/settings.py`, clé
`UNFOLD`) : navigation latérale groupée par module (Socle technique,
Commercial, Chiffrage et production, Stock, Achats, Sous-traitance,
Facturation), icônes, dashboard, recherche globale. Tous les `ModelAdmin` et
`TabularInline` du projet utilisent `unfold.admin.ModelAdmin` /
`unfold.admin.TabularInline` au lieu des classes Django standard — aucun
changement de logique, uniquement la classe de base.

Tous les libellés de champs portent un `verbose_name` explicite (français,
avec accents).

## Constructeur de devis (création à la volée)

Depuis la fiche d'un devis en brouillon (admin), le bouton **"Constructeur
de devis"** (en haut à droite) ouvre une page dédiée
(`chiffrage/builder_views.py`, `chiffrage/templates/chiffrage/devis_builder.html`)
permettant d'ajouter une ligne :

- soit avec un **article existant** (recherche par référence) ;
- soit avec un **nouvel article fabriqué**, créé à la volée avec sa
  nomenclature (composants) et sa gamme (étapes), en une seule transaction
  (`chiffrage/builder.py`, `creer_article_fabrique` — réutilise
  `full_clean()` sur chaque objet, donc les mêmes règles métier que partout
  ailleurs dans l'admin).

Pour un composant matière première, la quantité consommée se saisit selon
l'unité de coût de l'article :
- **Pièce** : juste une quantité.
- **Longueur** (profilé) : longueur (mm) — et si l'article a un poids
  linéique, un champ **poids (kg)** apparaît, synchronisé dans les deux sens
  instantanément (un seul inconnu, conversion sans ambiguïté).
- **Surface**/**Poids** (tôle) : longueur × largeur restent la saisie de
  référence (ce sont les dimensions réelles de découpe — une surface ou un
  poids seuls ne suffisent pas à déterminer deux dimensions), avec surface
  et poids **affichés en direct** à côté au fur et à mesure de la saisie.

Le coût matière et le prix de vente de la ligne sont calculés
**automatiquement dès l'ajout** (le constructeur appelle `calculer_devis()`
juste après avoir créé la ligne — pas besoin de repasser par l'action admin
"Recalculer le chiffrage"). Si le calcul échoue pour une autre ligne du
devis (ex. donnée de référence manquante sur un autre article), la ligne est
tout de même créée et un message d'avertissement explique ce qui bloque le
calcul, sans empêcher l'ajout.

## Conversion poids/surface/unité sur la fiche Article

Sur la fiche d'un article matière première (admin), un champ d'aide
apparaît sous "Coût unitaire" selon l'unité de coût choisie
(`technique/static/technique/article_admin.js`) :
- **Poids** → "Prix équivalent au m²" (calculé via épaisseur × densité)
- **Surface** → "Prix équivalent au kg"
- **Longueur** avec poids linéique renseigné → "Prix équivalent au mètre"

Ce champ est bidirectionnel : le modifier met à jour `cout_unitaire` (le
seul champ réellement enregistré) instantanément, sans recharger la page.

## Recalcul en direct des lignes de devis

Sur la fiche standard d'un devis (admin), modifier la **quantité** ou le
**taux de marge matière appliqué** d'une ligne déjà enregistrée déclenche
un recalcul automatique (délai de 400ms après la dernière frappe), sans
recharger la page ni cliquer sur "Recalculer le chiffrage" :
`chiffrage/static/chiffrage/devis_admin_live.js` envoie la nouvelle valeur à
`POST /admin/chiffrage/devis/<numero>/lignes/<id>/recalculer/`
(`chiffrage/builder_views.py`, `recalculer_ligne_view`), qui réutilise
`calculer_devis()` — une seule implémentation du calcul, côté serveur,
jamais dupliquée en JavaScript.

Limite assumée : une ligne pas encore enregistrée (ajoutée mais devis non
sauvegardé) n'a pas encore d'identifiant, donc pas de recalcul live tant
qu'elle n'a pas été enregistrée une première fois (normalement, via
"Enregistrer" ou le constructeur de devis).

## Montant total HT (matière + opérations / temps machine)

Le moteur de chiffrage calculait déjà le coût des opérations de gamme
(temps machine, main d'œuvre — `DevisLigneOperation.cout_calcule`/
`prix_vente`), mais rien n'additionnait ce montant au prix matière pour
donner un total exploitable : chaque ligne n'affichait que son prix de
vente matière.

Trois niveaux de total sont maintenant disponibles, tous dérivés des mêmes
données déjà stockées (aucune nouvelle table) :

- `DevisLigne.prix_vente_operations` / `prix_vente_total` (matière +
  opérations, pour une ligne) ;
- `Devis.montant_matiere_ht` / `montant_operations_ht` / `montant_total_ht`
  (mêmes montants, cumulés sur tout le devis).

Ces totaux apparaissent :
- sur la fiche Devis (admin), au-dessus des lignes, et se mettent à jour en
  direct avec le recalcul live (quantité/taux de marge d'une ligne) ;
- sur chaque ligne de l'inline Devis et sur la liste `DevisLigne` ;
- dans la liste des devis (colonnes "Montant matière/opérations/total HT") ;
- sur la page Constructeur de devis, avec un total en pied de tableau.

Note technique : Unfold ne pose pas de classe `field-<nom>` sur les champs
readonly de premier niveau d'un ModelAdmin (contrairement à ses tableaux
inline) ; les trois totaux du haut de la fiche Devis sont donc rendus via
des méthodes d'admin (`montant_*_ht_display`) qui les enveloppent dans un
`<span id="...">` pour donner un point d'accroche stable au JS de recalcul
en direct.

## Dupliquer et modifier un article

Sur la fiche d'un article existant (admin), le bouton **"Dupliquer et
modifier"** (en haut à droite, à côté de "Historique") crée une copie de
l'article — tous les champs sauf la référence, qui est générée
automatiquement (`<référence>-COPIE`, puis `-COPIE-2`, `-COPIE-3`... si déjà
prise) — et redirige directement vers la fiche de la copie pour édition.

Pour un article **fabriqué**, sa nomenclature (composants) et sa gamme
(étapes) sont dupliquées avec lui (`technique/services.py`,
`dupliquer_article`) ; le stock (lots/mouvements) n'est jamais dupliqué, la
copie en démarre à zéro. Toute la logique passe par `full_clean()` sur
chaque objet créé, comme partout ailleurs dans l'admin.

Implémentation : même schéma que le "Constructeur de devis" — une vue admin
dédiée (`POST /admin/technique/article/<référence>/dupliquer/`) protégée
par `staff_member_required`, et un override de template
(`admin/technique/article/change_form.html`) ajoutant le bouton dans
`object-tools-items`, visible uniquement sur un article déjà enregistré.

## Codification paramétrable (préfixe + numéro)

Nouvelle app `codification` : un modèle `RegleCodification` (menu
**Paramétrage → Règles de codification**) définit, pour chaque entité
concernée, un préfixe, un nombre de chiffres (largeur du numéro, complété
par des zéros) et une réinitialisation (jamais, ou chaque année — l'année
est alors insérée entre le préfixe et le numéro, ex. `FAC-2026-00001`).

10 entités sont couvertes, avec des préfixes par défaut créés par une
migration de données (`codification/migrations/0002_seed_regles_par_defaut.py`) :
Devis (`DEV-`), Commande (`CDE-`), Ordre de fabrication (`OF-`), Commande
fournisseur (`CDEF-`), Réception (`REC-`), Facture (`FAC-`), Envoi
sous-traitance (`ENVST-`), Retour sous-traitance (`RETST-`), Tiers
(`TIERS-`) et Emplacement (`EMP-`). Volontairement exclus : Article,
Matière, Poste de travail — ce sont des références techniques choisies à la
main (ex. `TOLE-S235-3MM`), pas des numéros de séquence.

Fonctionnement (`codification/services.py`, `generer_code`) :
- le code proposé **pré-remplit** le champ numéro/code du formulaire
  d'ajout de l'entité (`codification/mixins.py`, `CodificationInitialeMixin`,
  branché sur les 10 `ModelAdmin` concernés) ;
- il reste un champ texte normal, modifiable avant enregistrement ;
- le compteur est incrémenté dès l'ouverture du formulaire d'ajout (pas au
  moment d'enregistrer) — un numéro peut donc être "sauté" si le formulaire
  est abandonné sans être enregistré. Compromis assumé pour rester simple
  (pas de réservation temporaire à nettoyer) ;
- si aucune règle n'est configurée pour une entité, le champ reste vide
  comme avant (comportement additif, jamais bloquant).

Pour reprendre une numérotation existante, ajuster `compteur_actuel`
directement sur la règle (le prochain code utilisera `compteur + 1`).

## Adresse de livraison, adresse de facturation et contact sur le devis

En plus du client, un devis peut porter une **adresse de facturation**, une
**adresse de livraison** et un **contact** (tous optionnels — comme le
client est déjà là dès le brouillon, ces informations peuvent être
complétées plus tard). Ces trois champs se comportent comme sur `Commande`
(mêmes modèles `Adresse`/`Contact` de l'app `commercial`) : `Devis.clean()`
vérifie que l'adresse ou le contact choisi appartient bien au client
sélectionné, sinon la validation échoue avec un message explicite.
