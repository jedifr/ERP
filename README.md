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
  - [x] **Chiffrage découpe laser / jet d'eau** (`decoupe/`) : import DXF/DWG
        d'une pièce et imbrication dans une surface de tôle donnée
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
  tarifs-poste, nomenclatures, gammes, pieces-decoupe, imbrications, tiers,
  adresses, contacts, devis, devis-lignes, commandes, ordres-fabrication,
  emplacements, lots, mouvements-stock, alertes-stock, factures,
  commandes-fournisseur, receptions, envois-sous-traitance,
  retours-sous-traitance, pilotage/marge-reelle/{numero_of}/,
  pilotage/taux-charge/{poste}/...)

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

11 entités sont couvertes, avec des préfixes par défaut créés par des
migrations de données (`codification/migrations/0002_seed_regles_par_defaut.py`
et `0006_seed_regle_facture_fournisseur.py`) : Devis (`DEV-`), Commande
(`CDE-`), Ordre de fabrication (`OF-`), Commande fournisseur (`CDEF-`),
Réception (`REC-`), Facture (`FAC-`), Facture fournisseur (`FACF-`), Envoi
sous-traitance (`ENVST-`), Retour sous-traitance (`RETST-`), Tiers
(`TIERS-`) et Emplacement (`EMP-`). Volontairement exclus : Article,
Matière, Poste de travail — ce sont des références techniques choisies à la
main (ex. `TOLE-S235-3MM`), pas des numéros de séquence.

Fonctionnement (`codification/services.py`) :
- `generer_code()` **pré-remplit** le champ numéro/code du formulaire
  d'ajout de l'entité (`codification/mixins.py`, `CodificationInitialeMixin`,
  branché sur les 11 `ModelAdmin` concernés) — un simple aperçu du prochain
  numéro (`compteur_actuel + 1`), qui ne modifie rien tant que rien n'est
  enregistré : consulter le formulaire d'ajout plusieurs fois sans jamais
  sauvegarder renvoie toujours le même code ;
- il reste un champ texte normal, modifiable avant enregistrement ;
- le compteur n'avance réellement qu'à l'enregistrement d'un nouvel objet
  (`enregistrer_code_utilise()`, appelée par `CodificationInitialeMixin.
  save_model()`), à partir du code effectivement utilisé — y compris si
  l'utilisateur a remplacé la suggestion par un numéro plus élevé (le
  compteur rattrape, pour éviter une collision au prochain aperçu) ; un code
  qui ne correspond pas au format de la règle est ignoré, rien n'est
  modifié. **Correctif** : la version initiale incrémentait le compteur dès
  l'ouverture du formulaire d'ajout, y compris pour un formulaire ensuite
  abandonné — un numéro pouvait être "sauté" à chaque visite non suivie
  d'un enregistrement (signalé par l'utilisateur après l'avoir observé sur
  la fiche Tiers) ;
- si aucune règle n'est configurée pour une entité, le champ reste vide
  comme avant (comportement additif, jamais bloquant).

Pour reprendre une numérotation existante, ajuster `compteur_actuel`
directement sur la règle (le prochain code utilisera `compteur + 1`).


**Année sur 2 ou 4 chiffres** : avec la réinitialisation « Chaque année », le champ **« Année dans le code »** choisit `2026` (4 chiffres, comportement historique) ou `26` (2 chiffres). Exemples en 4 chiffres de numéro : préfixe `DC` + 2 chiffres → `DC26-0001` (devis), `CF` → `CF26-0001` (commande fournisseur), `BL` → `BL26-0001` (livraison), `C0` → `C026-0001` (commande client). Le compteur repart à 1 chaque 1er janvier. La liste des règles affiche le **prochain code** de chaque règle ; changer le format n'affecte pas les codes déjà attribués (le compteur de l'année en cours continue tant que le code saisi suit le nouveau format).

## Adresse de livraison, adresse de facturation et contact sur le devis

En plus du client, un devis peut porter une **adresse de facturation**, une
**adresse de livraison** et un **contact** (tous optionnels — comme le
client est déjà là dès le brouillon, ces informations peuvent être
complétées plus tard). Ces trois champs se comportent comme sur `Commande`
(mêmes modèles `Adresse`/`Contact` de l'app `commercial`) : `Devis.clean()`
vérifie que l'adresse ou le contact choisi appartient bien au client
sélectionné, sinon la validation échoue avec un message explicite.

## Calcul live dès l'ajout d'une ligne, prix unitaire forcé, libellés HT

Trois compléments au chiffrage d'un devis :

**Aperçu live sur une ligne pas encore enregistrée** — jusqu'ici, le
recalcul en direct (voir plus haut) ne fonctionnait que sur une ligne déjà
sauvegardée. Désormais, choisir un article et une quantité sur une ligne
*neuve* de l'inline (la ligne vide par défaut, ou une ligne ajoutée via
"Ajouter un objet Ligne de devis supplémentaire") déclenche aussi un calcul
en direct — coût matière, prix de vente matière/opérations/total. Différence
avec le recalcul d'une ligne existante : cet aperçu ne persiste rien en base
(`POST .../lignes/previsualiser/`, `chiffrage/moteur.py::previsualiser_ligne`,
qui réutilise exactement les mêmes règles que `calculer_devis`) et ne met
donc pas à jour les totaux du devis, qui ne reflètent que les lignes
réellement enregistrées.

Point technique notable : une ligne ajoutée dynamiquement est un clone DOM
(bouton "Ajouter..."), et Django déclenche l'évènement `formset:added` sur
la ligne insérée pour permettre de la câbler en JS — mais dans le rendu
Unfold, cet évènement est émis sur le `<tr>` interne, pas sur le `<tbody
class="form-group">` qui l'englobe (celui que cible le reste du script) :
`devis_admin_live.js` remonte donc au `<tbody>` ancêtre via `closest()`. Un
second écueil : le clonage DOM copie les attributs (dont un éventuel
`data-*` marqueur "déjà câblée") mais jamais les écouteurs JS attachés en
`addEventListener` — poser ce marqueur sur le gabarit caché utilisé pour le
clonage aurait donc fait que chaque ligne ajoutée dynamiquement se retrouve
marquée "câblée" sans qu'aucun écouteur n'y soit réellement attaché ; ce
gabarit (`name` contenant `__prefix__`) est donc explicitement exclu du
câblage.

Erreur de calcul (ex. article sans coût unitaire renseigné) : le message
d'erreur du serveur s'affiche directement dans la ligne, en rouge, à la
place des "-" (colonne "Prix de vente total", avec le détail complet en
infobulle). Avant ce correctif, une erreur de calcul restait invisible
(uniquement loguée dans la console du navigateur) — la ligne affichait des
"-" sans aucune explication, ce qui pouvait laisser croire que le calcul
en direct ne fonctionnait pas du tout.

Deux correctifs supplémentaires sur ce même calcul en direct, trouvés en
creusant un signalement "ça ne calcule toujours pas" sur des lignes déjà
enregistrées et déjà remplies :

- **Calcul déclenché aussi au chargement de la page**, pas seulement sur
  modification. Une ligne déjà enregistrée a par définition déjà un
  article et une quantité ; sans un premier calcul automatique, elle
  affichait des "-" jusqu'à ce que quelqu'un retouche un champ — ce qui,
  vu de l'utilisateur, ressemble exactement à "le calcul ne marche pas".
  `wireRowExistante`/`wireRowNouvelle` appellent maintenant la fonction de
  calcul une première fois immédiatement après le câblage de la ligne (en
  plus de l'appeler à chaque modification).
- **Une ligne à problème ne bloque plus les autres.** `calculer_devis()`
  s'arrête à la *première* ligne en erreur (comportement volontairement
  conservé pour l'action admin "Recalculer le chiffrage", en bloc) — mais
  `recalculer_ligne_view` l'appelait quand même pour recalculer une seule
  ligne, si bien qu'une ligne à problème empêchait le calcul en direct de
  **toutes** les autres lignes du même devis, y compris parfaitement
  valides. Nouvelle fonction `chiffrage/moteur.py::calculer_ligne(devis,
  ligne)` qui calcule une seule ligne en isolation ; `recalculer_ligne_view`
  l'utilise à la place de `calculer_devis()`.

Combinés, ces deux bugs expliquaient un signalement où deux lignes
affichaient toutes les deux des "-" au chargement de la page, alors qu'une
seule des deux avait réellement un problème (article sans coût unitaire) —
le calcul ne s'était jamais déclenché pour aucune des deux, et même en le
déclenchant, la ligne valide aurait échoué à cause de l'autre.

**Prix de vente unitaire forcé** — `DevisLigne.prix_vente_unitaire_force`
(optionnel) permet de fixer directement le prix de vente matière d'une
ligne (`= quantité × ce prix`), en remplacement du calcul automatique
(coût matière × marge). Le coût matière calculé reste affiché à titre
informatif. Pris en compte par `calculer_devis`, le recalcul en direct et
l'aperçu d'une ligne neuve.

**Libellés "(HT)"** — les champs de prix de vente (ligne, opérations,
total, et le nouveau prix forcé) précisent maintenant "(HT)" dans leur
libellé, sur la fiche Devis comme sur la page Constructeur. Les montants
"Montant matière/opérations/total HT" du haut de la fiche Devis l'indiquaient
déjà.

## En-têtes de colonnes sur 2 lignes (fiche Devis)

Le tableau des lignes de devis (admin) partait en scroll horizontal : Unfold
force `white-space: nowrap` sur les en-têtes de colonnes, et plusieurs
libellés sont volontairement descriptifs ("Prix de vente unitaire forcé
(HT)"...). `chiffrage/static/chiffrage/devis_admin_live.css` (chargé par
`DevisAdmin.Media.css`) autorise le retour à la ligne et plafonne la largeur
des colonnes (`#lignes-data th { white-space: normal; max-width: 130px; }`)
pour que les en-têtes tiennent sur 2 lignes plutôt que d'élargir le tableau.

## Taux de TVA par ligne et prix TTC

Référentiel `commercial.TauxTVA` (menu **Commercial → Taux de TVA**) : nom,
taux (%), et un indicateur "taux par défaut" (un seul à la fois — même
validation que "adresse principale" sur `Adresse`). Pré-rempli par une
migration de données avec les taux français courants (normal 20 % par
défaut, intermédiaire 10 %, réduit 5,5 %, particulier 2,1 %) ; librement
modifiable ou complétable dans l'admin.

Chaque `DevisLigne` a son propre `taux_tva` (optionnel), pré-rempli
automatiquement avec le taux par défaut du référentiel
(`default=_taux_tva_par_defaut`, une fonction évaluée à la création de
l'instance — donc aussi bien sur une nouvelle ligne de l'inline que sur une
ligne créée par le Constructeur). Un `prix_vente_ttc` (propriété, comme
`prix_vente_total`) applique ce taux au prix de vente total HT de la ligne ;
`Devis.montant_total_ttc` additionne le TTC de chaque ligne — donc correct
même avec des taux différents d'une ligne à l'autre.

Le taux de TVA et le prix TTC suivent le calcul en temps réel déjà en place
(ligne existante comme ligne neuve) et s'affichent aussi sur la page
Constructeur.

## Calcul live sur le formulaire d'AJOUT d'un devis

Troisième cause, plus fondamentale, du même signalement "le calcul en
direct ne marche pas" : sur le formulaire d'**ajout** d'un nouveau devis
(`/admin/chiffrage/devis/add/`), le calcul en direct ne se déclenchait tout
simplement jamais, quoi qu'on saisisse dans les lignes.

En cause : `devis_admin_live.js` déduit le numéro du devis depuis l'URL
(`.../devis/<numéro>/change/`) pour construire les appels de recalcul/
aperçu — mais tant que le devis n'a pas été enregistré une première fois,
l'URL est `.../devis/add/` : il n'y a pas de numéro à en extraire (même si
le champ "Numéro" affiche déjà un code proposé par la codification
automatique — ce n'est qu'une valeur de formulaire, pas encore un objet
Devis en base). `init()` détectait cette absence de numéro et abandonnait
immédiatement, sans câbler aucune ligne.

Comme les endpoints existants (`.../lignes/previsualiser/`,
`.../lignes/<id>/recalculer/`) exigent tous les deux un Devis déjà en base
(ne serait-ce que pour construire leur URL), il fallait un chemin
spécifique pour ce cas : `POST /admin/chiffrage/devis/nouveau-devis/previsualiser-ligne/`
(`previsualiser_ligne_nouveau_devis_view`) ne dépend d'aucun numéro ni
d'aucun objet Devis en base. `previsualiser_ligne()` n'a besoin de l'objet
`devis` que pour lire deux attributs simples (`date_creation`,
`taux_marge_globale`) — jamais une requête qui exigerait qu'il soit
persisté — donc un `Devis(...)` construit en mémoire, jamais enregistré,
avec les valeurs actuelles du formulaire (lues en direct par le JS au
moment du calcul) suffit comme contexte.

Avec ce correctif, remplir article + quantité sur une ligne du formulaire
d'ajout calcule désormais le prix instantanément, avant même d'enregistrer
le devis — exactement le scénario initialement demandé.

## Correctif : date de création au format français rejetée par le calcul live

Régression introduite par la fonctionnalité précédente : sur le formulaire
d'ajout d'un devis, le calcul en direct échouait avec le message « Date de
création invalide. » dès que le champ "Date de création" contenait une
valeur — ce qui est pourtant systématiquement le cas (le widget de date de
l'admin le pré-remplit avec la date du jour).

En cause : `previsualiser_ligne_nouveau_devis_view` lisait cette date avec
`datetime.date.fromisoformat(...)`, qui n'accepte que le format ISO strict
`AAAA-MM-JJ`. Or le projet est configuré en `LANGUAGE_CODE = "fr-fr"`, et
le widget de date de l'admin (Unfold comme Django standard) affiche et
soumet sa valeur au format local `JJ/MM/AAAA` (ex. `"02/09/2026"`) — un
format qu'`fromisoformat()` rejette purement et simplement avec une
`ValueError`, capturée et renvoyée telle quelle comme erreur 400.

Corrigé en remplaçant l'appel par `django.forms.DateField().clean(...)` :
ce champ de formulaire Django connaît nativement `DATE_INPUT_FORMATS` (donc
le format local actif) et accepte aussi bien l'ISO, ce qui couvre les deux
cas sans dépendre d'un format codé en dur. Une `ValidationError` (date
réellement incompréhensible) est traduite en la même erreur 400 qu'avant.

Point technique notable : ce bug n'avait aucune chance d'être détecté par
le test existant (`test_date_creation_invalide_400`), qui envoyait une
chaîne délibérément absurde (`"pas-une-date"`) — un cas qui doit rester en
erreur avec les deux approches. Un nouveau test dédié envoie une date au
format français valide (`"02/09/2026"`) et vérifie que le calcul aboutit,
pour couvrir spécifiquement ce format.

## Constructeur de devis dès la création (devis pas encore enregistré)

Le "Constructeur de devis" (page dédiée pour ajouter des lignes, y compris
des articles fabriqués créés à la volée avec leur nomenclature/gamme)
n'était accessible que depuis la fiche d'un devis **déjà enregistré**,
puisqu'il crée réellement des enregistrements (Article, Nomenclature,
Gamme, DevisLigne) rattachés à un `Devis` existant en base — il a donc
besoin d'un numéro de devis valide dans son URL.

Plutôt que de réécrire le constructeur pour fonctionner entièrement en
mémoire (ce qui aurait exigé de repenser en profondeur sa logique, conçue
pour écrire directement en base à chaque ajout de ligne), le formulaire
d'ajout de devis propose désormais un bouton supplémentaire à côté
d'"Enregistrer" : **"Enregistrer et ouvrir le constructeur"**. Il
enregistre le devis normalement (avec les lignes déjà saisies dans
l'inline, le cas échéant), puis redirige directement vers le constructeur
au lieu de retourner sur la fiche — sans étape intermédiaire.

Implémentation :
- `chiffrage/templates/admin/chiffrage/devis/submit_line.html` étend le
  `admin/submit_line.html` d'Unfold et ajoute ce bouton (nommé
  `_construire`) uniquement quand `not original`, c'est-à-dire seulement
  sur le formulaire d'ajout — il n'a pas de sens une fois le devis créé
  (le bouton "Constructeur de devis" en haut de la fiche prend le relais).
- `DevisAdmin.response_add()` détecte `"_construire" in request.POST` une
  fois le devis effectivement enregistré par Django (l'admin a déjà
  appelé `save_model`/`save_related` à ce stade — les lignes de l'inline
  sont donc déjà en base) et redirige vers
  `admin:chiffrage_devis_builder` avec le numéro du nouvel objet, au lieu
  du comportement par défaut.

Point technique notable : Unfold expose un mécanisme dédié pour ajouter
des boutons à la barre de validation (`actions_submit_line`), mais celui-ci
n'est peuplé par `ActionModelAdminMixin.changeform_view()` que lorsque
`object_id` est fourni — donc jamais sur le formulaire d'ajout. Il a donc
fallu passer par la surcharge de template `submit_line.html` (mécanisme
standard de Django, résolu par app/modèle avant le fallback générique),
plutôt que par cette API, pour couvrir spécifiquement ce cas.

## Adresse et contact par défaut, pré-remplis à la sélection du client

Un tiers peut avoir plusieurs adresses de facturation/livraison et
plusieurs contacts ; `Adresse.est_principale` existait déjà comme repère
"adresse par défaut", mais rien ne l'exploitait automatiquement : il
fallait toujours re-sélectionner manuellement l'adresse de facturation,
l'adresse de livraison et le contact sur chaque nouveau devis, alors même
que c'est presque toujours la même pour un client donné.

Deux ajouts :
- `Contact` gagne un champ `est_principal` (même principe et même
  garde-fou "un seul par tiers" — via `clean()` — que
  `Adresse.est_principale`, qui existait déjà) : le contact par défaut
  proposé pour ce tiers.
- Sur la fiche Devis (ajout comme modification), sélectionner un client
  déclenche automatiquement un appel à
  `GET /admin/chiffrage/devis/tiers/<code>/valeurs-defaut/`
  (`valeurs_defaut_tiers_view`), qui renvoie l'adresse de facturation,
  l'adresse de livraison et le contact marqués principal/principale pour
  ce tiers (`null` si aucun n'est défini). Le JS les injecte alors dans
  les champs correspondants — mais seulement s'ils sont encore vides : un
  champ déjà rempli (choix explicite de l'utilisateur, ou valeur restaurée
  après un changement de client) n'est jamais écrasé.

Point technique notable : les champs `adresse_facturation`,
`adresse_livraison` et `contact` sont des widgets `autocomplete_fields`
(select2 alimentés en Ajax, sans options préchargées). Poser une valeur
dessus par JavaScript ne peut donc pas se faire en modifiant `value` sur le
`<select>` sous-jacent — il faut construire une `Option` avec le texte et
l'identifiant reçus du serveur, l'ajouter au select, puis déclencher
`change` sur l'instance select2 elle-même (API standard de select2 pour ce
cas). Comme ces widgets sont initialisés par le script `autocomplete.js` de
l'admin Django via `django.jQuery`, c'est ce même espace de noms
(`django.jQuery`, pas un `$` global) qu'utilise le JS de la fiche Devis
pour rester compatible.

## Correctif : ligne vide "obligatoire" sur les inlines Adresse/Contact d'un tiers

Signalé : modifier un tiers qui a déjà (par exemple) une adresse affichait
systématiquement une deuxième ligne, vide, sous la vraie — avec des
astérisques rouges "obligatoire" sur adresse/code postal/ville (champs
réellement obligatoires sur le modèle `Adresse`) alors que l'utilisateur
n'avait pas l'intention d'en ajouter une. Idem pour les contacts.

En cause : `AdresseInline`/`ContactInline` utilisaient `extra = 1` — le
réglage standard Django/Unfold qui ajoute toujours une ligne vide
supplémentaire "prête à remplir" à la fin d'un inline, en plus des objets
déjà enregistrés. Pratique quand les champs sont optionnels, gênant ici
puisque la plupart sont obligatoires : la ligne fantôme n'a jamais été
voulue mais a l'air de l'être.

Corrigé en passant `extra = 0` sur les deux inlines : plus aucune ligne
n'est ajoutée automatiquement, seuls les objets déjà enregistrés sont
affichés. Le lien "Ajouter un objet Adresse/Contact supplémentaire" reste
disponible pour en ajouter une volontairement — le comportement standard
d'un inline Django, juste sans son ajout automatique.

## Contact associé à une adresse de livraison précise

Le contact par défaut d'un tiers (`Contact.est_principal`, section
précédente) est une propriété globale du tiers — mais un client avec
plusieurs sites de livraison a souvent un interlocuteur différent par
site. `Contact` gagne donc un champ optionnel `adresse_livraison` (FK vers
`commercial.Adresse`, forcément de type Livraison et du même tiers que le
contact — vérifié dans `Contact.clean()`, même esprit que la validation
déjà en place sur `Devis.clean()` pour adresse_facturation/adresse_livraison/
contact vis-à-vis du client).

Le pré-remplissage automatique du contact sur la fiche Devis en tient
compte, avec un ordre de priorité clair :
1. le contact associé à l'adresse de livraison retenue, s'il y en a un ;
2. sinon le contact principal du tiers (`est_principal`).

Ce choix est fait à deux moments distincts :
- **à la sélection du client** : `valeurs_defaut_tiers_view` calcule
  d'abord l'adresse de livraison par défaut du tiers, puis applique cet
  ordre de priorité pour choisir le contact — une seule requête, un choix
  atomique et cohérent (évite toute course entre "adresse de livraison
  remplie" et "contact déjà rempli avec le mauvais choix" côté JS).
- **quand l'adresse de livraison est changée après coup**, indépendamment
  du client (nouveau site sélectionné manuellement) : un nouvel endpoint
  dédié, `GET /admin/chiffrage/devis/adresses/<id>/contact-associe/`
  (`contact_associe_adresse_view`), renvoie le contact associé à cette
  adresse précise ; le JS (`wireContactParAdresseLivraison()`) l'appelle à
  chaque changement du champ "Adresse de livraison" et propose ce contact
  — toujours sans écraser un contact déjà choisi.

## Unité des temps dans le constructeur de devis

Les champs "Temps fixe" et "Temps variable" d'une étape de gamme (mode de
calcul horaire) n'affichaient aucune unité — ambigu sans connaître la
convention du projet. Les temps sont exprimés en minutes dans toute
l'application (`OperationOF.temps_prevu`/`temps_reel`, `Gamme.temps_fixe`/
`temps_variable`) ; les libellés du constructeur l'indiquent maintenant
explicitement : "Temps fixe (min)" et "Temps variable (min/pièce)" — ce
second suffixe précise en plus qu'il s'applique par pièce produite (il est
multiplié par la quantité de la ligne de devis dans `moteur.py` :
`temps_fixe + temps_variable × quantité`), pas seulement son unité.

## Constructeur de devis : impossible de valider une ligne dont le prix ne se calcule pas

Signalé : ajouter une ligne dans le constructeur avec une matière sans
coût unitaire renseigné (ou, côté "nouvel article fabriqué", un composant
de nomenclature dans le même cas) créait quand même l'article, sa
nomenclature/gamme éventuelle et la ligne de devis — seul un avertissement
("le chiffrage n'a pas pu être recalculé") signalait le problème, mais
tout restait enregistré avec un prix inconnu. Le test qui couvrait ce
comportement s'appelait d'ailleurs très explicitement
`test_post_article_sans_cout_unitaire_avertit_sans_bloquer`.

Corrigé : `_traiter_ajout_ligne` (`chiffrage/builder_views.py`) enchaîne
maintenant la création de l'article (le cas échéant), l'ajout de la ligne
de devis et le calcul de son prix (`calculer_ligne`, pas `calculer_devis`
— pour ne juger que la ligne qu'on ajoute, indépendamment de l'état
d'éventuelles autres lignes déjà présentes sur ce devis) **dans une seule
transaction atomique**. Si le prix ne peut pas être calculé, tout est
annulé — article, nomenclature, gamme, ligne de devis — et la réponse
devient une erreur 400 avec le message explicatif, exactement comme un
autre champ invalide ; il n'y a plus d'état intermédiaire "ligne créée
mais non chiffrée". Côté JS (`devis_builder.js`), le message d'erreur
s'affiche en rouge sans recharger la page (au lieu du recharge-avec-
avertissement précédent), pour laisser le formulaire tel quel et permettre
de corriger sans tout ressaisir.

## Correctif majeur : le coût des opérations horaires était 60 fois trop élevé

Signalé par l'utilisateur, avec un calcul manuel de référence : pour une
étape de gamme au poste LASER (150 €/h), avec 10 min de temps fixe + 1 min
de temps variable par pièce, le coût attendu pour 1 pièce est
`(10 + 1) / 60 × 150 = 27,50 €` — le prix affiché ne correspondait pas.

En cause : `cout_etape_gamme()` (`chiffrage/moteur.py`) calculait
`temps × tarif.cout_horaire` directement. Or `Gamme.temps_fixe`/
`temps_variable` sont exprimés en **minutes** (voir la section précédente
sur l'unité des temps du constructeur) alors que `TarifPoste.cout_horaire`
est un tarif en **€/heure** — il manquait la conversion (`/ 60`) avant de
multiplier. Concrètement, toute étape de gamme en mode horaire facturait
60 fois son coût réel : 27,50 € devenait 1 650 €.

Ce même bug (mêmes unités, même faute) existait aussi dans l'app
`pilotage`, à deux endroits qui comparent des temps réels remontés par
l'atelier (`OperationOF.temps_reel`, également en minutes) à un tarif
horaire ou à une capacité exprimée en heures :
- `cout_reel_operation()` — coût réel d'une opération d'OF (utilisé par
  `marge_reelle_ordre_fabrication()`, marge réelle vs prévue) ;
- `taux_charge_poste()` — le temps réel cumulé (minutes) était comparé
  directement à une capacité disponible en heures
  (`nombre_machines × jours_ouvrés × heures_par_jour`), gonflant le taux
  de charge calculé du même facteur 60. `temps_reel_cumule` dans la
  réponse de cette fonction est donc désormais exprimé en heures (comme
  `capacite_disponible`), et non plus en minutes brutes.

Les trois corrigés de la même façon : diviser le temps en minutes par 60
avant de le multiplier par un montant en €/heure ou de le comparer à une
capacité en heures. Point technique notable : ce bug n'avait aucune chance
d'être détecté par les tests existants, qui codaient tous la même erreur
dans leurs valeurs attendues (`(10 + 5×3) × 50 = 1250`, sans jamais
diviser par 60) — corrigés en même temps que le code (`chiffrage/tests.py`,
`pilotage/tests.py`), avec le calcul manuel de l'utilisateur repris tel
quel comme vérification indépendante (`27,50 € / 15,00 € / 4,5833 €` par
pièce pour 1, 2 et 12 pièces).

## Libellé d'article, visible et saisissable dès le devis

Un article n'avait que sa référence (ex. `PIECE-00042`) comme identifiant
lisible — pas de nom/description. Ajouté `Article.libelle` (texte libre,
optionnel), et `Article.__str__` l'intègre désormais partout où l'article
est affiché (`"PIECE-00042 — Platine support moteur"`) : select2 des
lignes de devis, résultats de recherche du constructeur, listes admin —
sans changement de code supplémentaire à ces endroits, puisqu'ils
affichent déjà `str(article)`.

Modifiable dès la création de l'article :
- **Constructeur de devis, "Nouvel article fabriqué"** : nouveau champ
  "Libellé" à côté de "Référence", transmis à `creer_article_fabrique()`.
- **Fiche Article** (`ArticleAdmin`) : champ visible dans la liste et la
  recherche (`search_fields`).
- **"Dupliquer et modifier"** (`dupliquer_article`) : le libellé est
  copié comme les autres champs.

Pour un article déjà existant choisi sur une ligne de devis, le petit menu
(⋮) à côté du champ autocomplete (widget standard de l'admin Django,
`RelatedFieldWidgetWrapper`) permet de voir/modifier l'article — donc son
libellé — sans quitter la fiche du devis.

## Prix de vente unitaire sur les lignes de devis

Les lignes de devis n'affichaient que des montants **totaux** pour la
ligne (prix de vente matière, opérations, total HT/TTC) — pas de prix
"à l'unité", pourtant utile pour comparer des lignes de quantités
différentes ou vérifier un tarif au coup d'œil.

Ajouté `DevisLigne.prix_vente_unitaire` (propriété calculée, jamais
stockée) = `prix_vente_total / quantite` — `None` tant que le chiffrage
n'a pas été calculé, comme les autres montants dérivés. Branché partout où
les autres montants de ligne le sont déjà : inline de la fiche Devis,
`DevisLigneAdmin`, endpoints de calcul/aperçu en direct
(`recalculer_ligne_view`, `previsualiser_ligne_view`,
`previsualiser_ligne_nouveau_devis_view`, `moteur.previsualiser_ligne()`),
JS de calcul live (`devis_admin_live.js`), et tableau "Lignes existantes"
du constructeur.

Aucune nouvelle règle de calcul : c'est une lecture différente de données
déjà calculées (`prix_vente_total`), donc pas de risque d'incohérence avec
les montants totaux déjà affichés — y compris quand un prix unitaire est
forcé (`prix_vente_unitaire_force`), puisque celui-ci influence déjà
`prix_vente_matiere` en amont.

## Délai sur le devis : liste paramétrable + saisie libre

Nouveau champ `Devis.delai` (texte libre, optionnel) pour annoncer un
délai de livraison sur le devis. La contrainte du besoin — "on va le
chercher dans une liste paramétrable, mais on peut aussi le taper
directement" — ne correspond ni à un `ForeignKey` (empêcherait la saisie
libre) ni à un `ChoiceField` (même problème) : elle correspond exactement
au `<datalist>` HTML natif, qui associe un champ texte libre à une liste
de suggestions sans jamais contraindre la valeur saisie.

- Nouveau référentiel `commercial.DelaiPropose` (`libelle` + `ordre`
  d'affichage), géré depuis un admin dédié (`DelaiProposeAdmin`) — vide au
  départ, à peupler selon les délais habituels de l'atelier (aucune valeur
  par défaut : contrairement aux taux de TVA, un délai type n'a rien
  d'universel).
- `DelaiWidget` (`chiffrage/widgets.py`) : sous-classe de
  `forms.TextInput` dont `render()` ajoute un `<datalist id="delai-
  suggestions">` peuplé depuis `DelaiPropose.objects.all()`, en plus du
  champ texte (`list="delai-suggestions"` sur l'`<input>`). Branché sur le
  champ `delai` via un `ModelForm` dédié (`DevisAdminForm`) sur
  `DevisAdmin.form`.

Résultat : le champ "Délai" de la fiche Devis propose les valeurs du
référentiel dans son autocomplétion native du navigateur, mais accepte
n'importe quel texte tapé à la main — vérifié en tapant un délai hors
liste ("Livraison express sous 48h"), accepté sans erreur.

## Livraisons partielles d'une commande

Jusqu'ici, une `Commande` n'avait pas de lignes à elle : les quantités
venaient directement des lignes du devis, et rien ne suivait ce qui avait
effectivement été livré. Demandé : pouvoir livrer une commande
**partiellement**, **article par article**, avec une **quantité livrée
différente de la quantité commandée**, en laissant apparaître un
**reliquat** quand la livraison est incomplète.

Architecture reprise à l'identique du modèle déjà en place côté achats
(`CommandeFournisseur` → `LigneCommandeFournisseur` → `Reception` →
`ReceptionLigne`), pour la cohérence et parce qu'il couvre exactement le
même besoin côté réception fournisseur :

- **`CommandeLigne`** (nouveau) : une ligne par article commandé —
  `quantite_commandee` (figée au moment de la commande) et
  `quantite_livree` (cumul recalculé, `editable=False`). Propriétés
  calculées `reliquat` (= commandée − livrée) et `entierement_livree`.
  Créée automatiquement par `production.lancer_en_production()`, **une
  par ligne de devis, quelle que soit sa nature** — contrairement aux
  ordres de fabrication, qui ne concernent que les articles FABRIQUE, le
  suivi de livraison doit couvrir aussi les matières premières vendues
  directement.
- **`Livraison`** / **`LivraisonLigne`** (nouveaux, numérotation
  automatique via la codification — nouvelle entité `LIVRAISON`, préfixe
  `LIV-`) : une livraison peut porter sur plusieurs lignes de commande, et
  une ligne de commande peut être livrée en plusieurs fois.
  `LivraisonLigne.clean()` refuse qu'une livraison dépasse le reliquat
  restant (`quantite déjà livrée + nouvelle quantité > quantité
  commandée`), avec le reliquat déjà connu dans le message d'erreur.

**Effet de bord stock**, symétrique à la réception fournisseur (qui crée
un mouvement `ENTREE`) : `LivraisonLigne._appliquer()` crée un mouvement
`SORTIE` (`MouvementStock`) sur le lot de l'article livré. Différence
assumée avec le mode achats : un article fabriqué sur mesure n'a le plus
souvent **aucun lot de stock** (`gere_en_stock` vaut faux par défaut pour
un `FABRIQUE`) — la sortie de stock est donc **sautée silencieusement**
plutôt que de bloquer la livraison (contrairement à la réception
fournisseur, qui exige un lot existant). Si plusieurs lots existent pour
l'article (cas ambigu, comme côté achats), `LivraisonError` est levée.

Point technique notable, corrigé par rapport au modèle achats d'origine :
dans `ReceptionLigne._appliquer()`, la mise à jour du cumul reçu a lieu
*avant* la résolution du lot — si celle-ci échoue (lots ambigus), la ligne
de réception reste tout de même enregistrée avec son cumul incrémenté,
sans mouvement de stock associé (état incohérent). `LivraisonLigne.save()`
évite ce piège : la résolution du lot est faite *avant* toute écriture, et
l'ensemble (`save()` de la ligne + mise à jour du cumul + mouvement de
stock) est englobé dans une transaction atomique — si le lot est ambigu,
tout est annulé, y compris l'enregistrement de la ligne elle-même. Aucun
état "ligne enregistrée mais jamais répercutée" n'est possible.

`LivraisonAdmin.save_formset()` intercepte `LivraisonError` pour l'afficher
comme un message d'erreur normal de l'admin plutôt que de laisser
remonter une page 500 (même pattern que `ReceptionAdmin` côté achats).

Vérifié de bout en bout (Playwright) : devis validé → `lancer_en_production`
crée une commande avec sa ligne (quantité commandée 10, livrée 0, reliquat
10) → une première livraison de 6 unités laisse un reliquat de 4, visible
immédiatement sur la fiche Commande.

## Correctif : étape de gamme créée dans le constructeur, prix des opérations à 0

Signalé avec capture : un article fabriqué ("toto123") créé via le
constructeur, avec une étape de gamme correctement renseignée (poste
LASER, 10 min fixe + 1 min variable), affichait pourtant "Prix vente
opérations (HT)" à 0,00 et aucun poste listé dans la colonne "Opérations"
du tableau "Lignes existantes" — sans aucune erreur, contrairement aux
autres lignes du même devis qui, elles, affichaient un prix correct.

En cause : `addGammeRow()` (`chiffrage/static/chiffrage/devis_builder.js`)
pré-remplissait la "Date de début" d'une nouvelle étape avec **la date du
jour** (`new Date().toISOString()`), sans lien avec la date de création du
devis en cours. Or `gamme_active()` (`chiffrage/moteur.py`) ne retient que
les étapes dont `date_debut <= devis.date_creation` — une étape datée
d'aujourd'hui sur un devis créé à une date antérieure (le cas courant : la
plupart des devis ne sont pas construits le jour même de leur création)
est donc **silencieusement exclue** du calcul, sans lever d'erreur (une
gamme vide de résultats n'est pas une gamme invalide). Reproduit et
confirmé localement : `gamme_active(article, devis.date_creation)` renvoie
0 étape dès que `Gamme.date_debut` est postérieure à `Devis.date_creation`.

Corrigé en exposant `devis.date_creation` au JS (`devis-builder-data`,
`chiffrage/templates/chiffrage/devis_builder.html`) et en l'utilisant
comme valeur par défaut de la nouvelle étape, à la place de la date du
jour. Un test dédié documente explicitement ce mécanisme côté serveur
(`test_etape_datee_apres_le_devis_est_silencieusement_ignoree`), pour
qu'un futur changement de `gamme_active()` ne puisse pas faire revivre ce
piège sans le remarquer.

**Pour la ligne déjà créée en production** (l'étape de gamme existante de
"toto123" reste datée après `date_creation` du devis) : il faut corriger
sa date de début manuellement — soit depuis la fiche Article (via le
nouveau lien "Voir la fiche de cet article", section suivante), soit
directement dans Techniques → Gammes.

## Ouvrir la fiche d'un article directement depuis le constructeur

Ajouté deux liens, pour ne plus avoir à chercher l'article dans le menu
"Articles" :
- dans le tableau "Lignes existantes", chaque référence d'article est
  désormais un lien vers sa fiche (`admin:technique_article_change`),
  ouvert dans un nouvel onglet (`target="_blank"`) pour ne pas perdre sa
  place dans le constructeur ;
- dans le panneau "Ajouter une ligne", en mode "Article existant", un lien
  "Voir la fiche de cet article ↗" apparaît dès qu'un article est
  sélectionné dans la recherche (masqué à nouveau si la recherche est
  modifiée).

## Ajout d'utilisateur simplifié (app `comptes`)

Le formulaire d'ajout d'utilisateur de Django (`auth.User`) n'était pas
habillé par Unfold : le champ `password1`/`password2` de
`UserCreationForm` ne passe jamais par `ModelAdmin.formfield_for_dbfield`
(seul point où Django ajoute la classe CSS `vTextField`/`vPasswordField`
utilisée par le thème), et Unfold ne fournit pas de secours pour un
`<input>` sans classe — le CSS d'Unfold réinitialise l'apparence native de
tous les champs, boîte et bordure comprises. Résultat : sur ce formulaire
précis, aucun champ ne semblait "cliquable" (ni le mot de passe, ni sa
confirmation).

Correction et simplification en un seul geste (app `comptes/`) :
- `comptes.admin.UserAdmin` (Django `UserAdmin` + `unfold.admin.ModelAdmin`)
  remplace l'enregistrement par défaut de `auth.User` (et `auth.Group`,
  touché par le même souci sur son propre formulaire) ;
- `add_form` reprend `unfold.forms.UserCreationForm` (qui, lui, réattribue
  les bons widgets Unfold à `password1`/`password2`), étendu avec
  `first_name`/`last_name` (facultatifs) pour saisir un nom dès la
  création, et sans le choix "Authentification par mot de passe"
  (Activée/Désactivée) — un ajout de Django 5.1 pensé pour le SSO/LDAP,
  hors sujet ici ;
- `add_fieldsets` se limite à : identifiant, prénom, nom, mot de passe (x2)
  et un seul réglage — "Statut équipe" (`is_staff`), qui conditionne
  l'accès à l'admin. Le reste (groupes, permissions, statut
  super-utilisateur...) reste modifiable après coup sur la fiche complète,
  comme le rappelle le bandeau "After you've created a user...".
- "Utilisateurs" et "Groupes" apparaissent maintenant dans le menu
  "Paramétrage" (`UNFOLD["SIDEBAR"]`) — jusque-là non listés, donc
  seulement accessibles en tapant l'URL ou via la recherche.

## Habillage : palette "atelier" et tableau de bord d'accueil

Deux ajouts purement visuels, sans toucher au fonctionnement de l'admin :

- **Palette** : `UNFOLD["COLORS"]["primary"]` remplace le violet par défaut
  d'Unfold par une palette ambre/acier (valeurs OKLCH de l'échelle `amber`
  de Tailwind) — se répercute partout où Unfold utilise sa couleur
  primaire (boutons, liens actifs, bouton de connexion, icône du logo...).
  La palette `base` (gris neutre) reste celle par défaut.
- **Tableau de bord** (`UNFOLD["DASHBOARD_CALLBACK"]` →
  `comptes.dashboard.dashboard_callback`) : au-dessus de la liste des
  applications, 5 indicateurs calculés à la volée (aucune table dédiée,
  comme l'app `pilotage`) — devis en brouillon, devis validés ce mois-ci,
  CA facturé ce mois-ci (HT), alertes de stock actives (mise en évidence
  en rouge si > 0) et OF lancés ce mois-ci. Chaque carte est un lien direct
  vers la liste filtrée correspondante.
  - `templates/admin/index.html` (nouveau `TEMPLATES["DIRS"]` au niveau du
    projet, pour que ce template passe avant celui d'Unfold dans l'ordre
    de résolution) reprend le `admin/index.html` d'Unfold à l'identique et
    y insère juste la grille de cartes en tête du bloc `content`.

## Créer une commande directement (sans devis formel)

Certaines ventes n'ont pas de devis à proprement parler : le client
commande directement. Plutôt que de dupliquer le moteur de chiffrage (qui
ne calcule que sur `Devis`/`DevisLigne`, jamais sur `CommandeLigne` — voir
"Livraisons partielles d'une commande" plus haut), cette fonctionnalité
réutilise entièrement le constructeur de devis existant :

- Sur le formulaire d'AJOUT d'un devis, un second bouton **"Créer une
  commande directement"** (`_construire_commande`, à côté de "Enregistrer
  et ouvrir le constructeur") enregistre le devis puis ouvre le même
  constructeur, avec `?commande_directe=1` dans l'URL.
- Ce paramètre (lu par `devis_builder_view`, jamais persisté en base — pas
  de nouveau champ ni de migration) bascule juste l'habillage de l'écran :
  titre "Constructeur de commande", texte explicatif ("ce devis ne sert
  que de support de calcul interne, il n'est jamais montré au client"), et
  un bouton **"Valider et créer la commande →"** à la place du simple lien
  de retour. Ce bouton POST vers une nouvelle vue,
  `valider_commande_directe_view` (URL `<numero>/valider-commande/`), qui
  fait exactement ce que fait déjà l'action d'admin "Lancer en
  production" sur la liste des devis — passer le devis en `VALIDE` puis
  appeler `lancer_en_production(devis)` — mais dans une seule transaction
  (`transaction.atomic()`) : si `lancer_en_production` échoue (ex. client
  sans adresse principale), le passage en `VALIDE` est annulé avec elle,
  le devis reste en brouillon et modifiable au lieu de se retrouver
  verrouillé sans commande créée. Succès -> redirection directe vers la
  fiche de la commande créée.
- Le lien "Constructeur de devis" de la fiche devis (object-tool) et le
  lien "Retour à la fiche devis" du constructeur se souviennent du mode
  via ce même paramètre d'URL, pour l'aller-retour entre les deux écrans.
  Une fois la commande créée, revisiter le constructeur affiche un lien
  direct vers elle à la place du bouton de validation.

## Correctif : lignes invisibles sur la fiche commande + prix/TVA/date de livraison par ligne

Remonté par capture d'écran : une commande existante affichait "Lignes de
commande" complètement vide. Cause réelle, pas juste cosmétique : cette
commande avait été créée avant l'ajout du modèle `CommandeLigne`
(fonctionnalité "Livraisons partielles") — aucune ligne n'existait en
base, il n'y avait donc rien à afficher.

- **`CommandeLigne.devis_ligne`** (FK vers `DevisLigne`, nullable) relie
  chaque ligne de commande à sa ligne de devis d'origine — posé
  automatiquement par `lancer_en_production`. Trois propriétés en
  lecture seule s'appuient dessus pour afficher **prix de vente unitaire,
  taux de TVA, montant HT et montant TTC** sans dupliquer ces valeurs (le
  prix vient toujours du devis, jamais recalculé sur la commande) :
  `taux_tva`, `prix_vente_unitaire`, `montant_ht`, `montant_ttc` — `None`
  tant qu'aucune ligne de devis n'est reliée.
- **`CommandeLigne.date_livraison_prevue`** (date, éditable, facultative) :
  chaque ligne peut avoir sa propre date, indépendante des autres lignes
  de la même commande — modifiable directement dans l'inline "Lignes de
  commande" de la fiche.
- **`production.synchroniser_lignes_commande(commande)`** : filet de
  sécurité rejouable sans risque — recrée toute ligne manquante par
  rapport au devis (le bug remonté) et relie `devis_ligne` sur les lignes
  qui ne l'ont pas encore, sans jamais toucher une `quantite_commandee`
  déjà enregistrée (une divergence avec le devis reste une décision
  manuelle). Exposé comme action d'admin **"Synchroniser les lignes
  depuis le devis"** sur la liste des commandes.
- **Migration de données** (`0009_synchroniser_lignes_commande_existantes`) :
  applique cette même synchronisation à toutes les commandes existantes
  au `migrate` — corrige automatiquement les commandes déjà en production
  sans action manuelle (dont celle remontée dans le rapport).

## Rattacher une commande fournisseur à une ligne de commande client

`achats.LigneCommandeFournisseur.commande_ligne_client` (FK optionnelle
vers `chiffrage.CommandeLigne`) trace l'achat qui sert à approvisionner une
ligne de commande client précise — plusieurs lignes d'achat (réappro en
plusieurs fois, ou fournisseurs différents) peuvent pointer vers la même
ligne de commande client. Choisi côté ligne (pas en-tête de commande
fournisseur) pour permettre le mélange, sur une même commande fournisseur,
d'achats destinés à des clients différents.

Trois effets, une fois le rattachement fait :
- **`CommandeLigne.date_livraison_possible`** (propriété, jamais stockée) :
  la plus tardive des `date_livraison_prevue` des commandes fournisseur
  rattachées — la ligne client n'est complète que quand tout est arrivé.
  Toujours recalculée en direct, donc jamais périmée. À la différence de
  `date_livraison_prevue` (l'engagement pris auprès du client, saisi à la
  main) : celle-ci n'est **jamais** réécrite automatiquement, même quand un
  achat est rattaché — les deux dates coexistent volontairement, l'une
  reflète ce qu'on a promis, l'autre ce que l'appro permet réellement.
  (Affichée via un petit formatage dédié, `date_livraison_possible_display`
  dans `chiffrage/admin.py` : Unfold ne localise en `jj/mm/aaaa` que les
  vrais champs de modèle — une propriété readonly comme celle-ci serait
  sinon affichée en ISO, `str(date)` brut.)
- **`CommandeLigne.statut_approvisionnement`** (propriété) : résumé
  lisible de l'avancement des achats rattachés — ex. "Aciers du Nord —
  reçu 8/20". Purement informatif, ne touche jamais `quantite_livree`
  (livraison au client, pilotée indépendamment par `Livraison`).
- **Clôture automatique d'alerte de stock** : à la création d'une ligne de
  commande fournisseur avec `commande_ligne_client` renseigné (et sans
  `alerte_stock_origine` choisie à la main), une alerte de stock active
  pour le même article est recherchée et clôturée automatiquement — réutilise
  le mécanisme déjà existant (`alerte_stock_origine`), simplement déclenché
  par ce nouveau rattachement plutôt que par une sélection manuelle.

## Contenu en pleine largeur d'écran

Le contenu de l'admin (`#content`) était plafonné à une largeur maximale
centrée (classe Tailwind `container` d'Unfold) — gênant sur les fiches à
tableaux larges (lignes de devis/commande), qui scrollaient horizontalement
dans un espace réduit alors que l'écran avait de la place de libre à
droite.

`comptes.layout.global_callback` (branché sur `UNFOLD["GLOBAL_CALLBACK"]`,
exécuté par Unfold sur *chaque* page admin, contrairement à
`DASHBOARD_CALLBACK` qui ne concerne que l'accueil) injecte
`is_fullwidth: "1"` dans le contexte de toutes les pages. Nécessaire pour
les fiches (`change_view`) : Unfold n'y expose pas
`ModelAdmin.list_fullwidth` (qui ne fonctionne que sur les listes, où le
contexte `cl` existe) — `GLOBAL_CALLBACK` est le seul point d'accroche qui
couvre aussi bien les fiches que les listes, sans avoir à surcharger un
template par modèle.

## Lignes de commande modifiables après enregistrement (surcharges + traçabilité)

Quantité, prix de vente unitaire, taux de TVA et désignation restaient
figés une fois la commande créée. Ils sont maintenant modifiables, avec
trois garde-fous discutés et validés avant développement :

- **Surcharges, jamais d'écrasement du devis** : `quantite_commandee`,
  `prix_vente_unitaire` (nouveau champ, avant une propriété qui lisait
  `devis_ligne`), `taux_tva` (idem, nouvelle FK propre à la commande) et
  `designation` (nouveau champ) sont pré-remplis depuis le devis à la
  création (`lancer_en_production`/`synchroniser_lignes_commande`), puis
  librement modifiables — `devis_ligne` reste un simple pointeur vers la
  valeur d'origine, jamais touché. `montant_ht`/`montant_ttc` restent des
  propriétés, mais recalculées depuis les valeurs courantes de la ligne
  (plus depuis le devis).
  - `taux_tva`, devenu éditable, est un `<select>` — pour garder l'affichage
    compact ("20%", pas "Taux normal (20.0%)") jusque dans les options du
    menu déroulant (pas seulement en lecture seule), `CommandeLigneForm`
    (chiffrage/admin.py) déclare `taux_tva` avec un `ModelChoiceField` dont
    `label_from_instance` ne renvoie que le pourcentage — branché sur
    `CommandeLigneInline` et `CommandeLigneAdmin`. Le même correctif
    (`TauxTVACompactChoiceField`, factorisée pour être réutilisable) est
    aussi branché côté devis, sur `DevisLigneForm`/`DevisLigneInline`/
    `DevisLigneAdmin` : `DevisLigne.taux_tva` est un champ distinct de
    `CommandeLigne.taux_tva`, donc jamais couvert par la première
    correction — le menu déroulant "Lignes de devis" affichait encore le
    libellé complet du référentiel tant que ce second branchement n'était
    pas fait.
- **Traçabilité complète** (pas seulement le bouton "Historique" générique,
  qui ne liste que les noms de champs) : `CommandeLigneModification`,
  peuplé par `CommandeAdmin.save_formset`/`CommandeLigneAdmin.save_model`
  (il faut `request.user`, indisponible au niveau du modèle) — une ligne
  par champ suivi réellement modifié (`champ`, `ancienne_valeur`,
  `nouvelle_valeur`, `utilisateur`, `date_modification`), visible en
  inline sur la fiche de la ligne de commande. Migration `0011` : backfill
  de `prix_vente_unitaire`/`taux_tva` sur les lignes déjà existantes
  (sinon elles se seraient retrouvées vides après la migration de schéma).
- **Quantité — augmentation** : plutôt que de modifier une ligne dont
  l'ordre de fabrication est déjà lancé (ses temps machine resteraient
  basés sur l'ancienne quantité, jamais recalculés), l'admin autorise
  maintenant l'ajout d'une nouvelle ligne à une commande existante
  (`CommandeLigneInline` : l'ajout n'était pas permis avant). Un
  avertissement (pas un blocage — l'inverse reste possible) s'affiche si
  la quantité d'une ligne existante augmente alors qu'un OF existe déjà
  pour son article. `production.lancer_ligne_en_production(ligne)` crée
  l'OF d'une seule ligne (factorisé avec `lancer_en_production` via
  `_creer_ordre_fabrication`) — exposé comme action d'admin **"Lancer
  cette ligne en production (OF)"** sur la liste des lignes de commande.
- **Quantité — diminution** : aucune tentative de clôturer l'OF côté ERP
  (ses statuts sont alimentés à sens unique depuis le planning atelier,
  piloter sa clôture depuis l'ERP entrerait en conflit avec cette
  synchronisation). Diminuer `quantite_commandee` recalcule juste le
  `reliquat` côté client ; `CommandeLigne.clean()` refuse de descendre
  en dessous de `quantite_livree` (déjà livré ne peut pas être "délivré"),
  et refuse de changer l'article d'une ligne déjà partiellement livrée.

## Nouvelles natures d'article achetées + fournisseurs multiples avec historique de tarifs

`Article.Nature` comptait deux valeurs (matière première, fabriqué) ; trois
natures d'articles achetés s'y ajoutent : **service acheté**,
**consommable**, **composant**. Elles sont costées exactement comme une
matière première (directement depuis `cout_unitaire`, sans nomenclature) —
`chiffrage/moteur.cout_matiere_article` ne teste plus `nature ==
MATIERE_PREMIERE` pour décider du calcul direct, mais `nature == FABRIQUE`
pour décider de la décomposition via nomenclature (seul un fabriqué en a
une) ; toute autre nature, existante ou nouvelle, passe par le calcul
direct — généralisation sans changement de comportement pour les deux
natures préexistantes.

Nouveau modèle `achats.ArticleFournisseur` : associe un article acheté (donc
pas fabriqué — `clean()` le refuse, un fabriqué est produit en interne) à un
fournisseur pouvant le livrer, avec sa **référence** et sa **désignation**
propres à ce fournisseur (distinctes de celles internes à l'article).
Plusieurs fournisseurs peuvent être associés au même article (contrainte
d'unicité seulement sur le *couple* article+fournisseur) — inline "Fournisseurs
d'article" sur la fiche Article pour les ajouter rapidement, et fiche dédiée
par association (comme `CommandeLigne` : inlinée ET dotée de sa propre page)
pour gérer son historique de tarifs.

Traçabilité des tarifs : `achats.TarifAchatArticle`, sur le même principe
que `technique.TarifPoste` (réutilise le même mixin
`DateRangeHistoriqueMixin` — refuse le chevauchement de deux tarifs actifs
sur le même `ArticleFournisseur`). `ArticleFournisseur.tarif_actuel` renvoie
le tarif dont la période couvre aujourd'hui. Cette traçabilité est purement
déclarative pour l'instant : elle n'alimente pas automatiquement
`Article.cout_unitaire` (qui reste saisi manuellement et utilisé tel quel
par le moteur de chiffrage) — un rapprochement automatique serait une
évolution ultérieure séparée.

## Nouvelle app comptabilite : plan comptable général français + journaux comptables

Nouvelle app `comptabilite`, sur le principe des dictionnaires Dolibarr
(cf. capture d'écran fournie "Dictionnaires - Journaux comptables").

- **`CompteComptable`** (plan comptable) : `code` (clé primaire), `libelle`,
  `classe` (1 à 8, déduite automatiquement du 1er chiffre du code —
  champ non éditable), `compte_parent` (hiérarchie, ex. 1013 sous 101 sous
  10 sous 1), `systeme` (Système de base / Système développé — distingue
  les comptes obligatoires des comptes de détail facultatifs du PCG),
  `actif`.
- **`JournalComptable`** (dictionnaire des journaux, même principe que la
  capture d'écran Dolibarr) : `code`, `libelle`, `nature` (Achats, Ventes,
  Banque, Caisse, Notes de frais, Opérations diverses, Reports à nouveaux),
  `actif`. Migration `0002` préremplit le jeu standard (AC, VT, BQ1, CA, ER,
  OD, AN) — les journaux propres à l'entreprise (un par compte bancaire,
  ex. BQ2 "BANQUE POPULAIRE") s'ajoutent librement depuis l'admin, comme
  demandé ("permettre d'en ajouter de nouveaux").

**Import du PCG officiel en un clic** : bouton "Importer le plan comptable
officiel (PCG *millésime*)" sur la liste des comptes comptables
(`CompteComptableAdmin.actions_list`, mécanisme Unfold pour une action de
liste qui n'a pas besoin de sélection — contrairement aux actions
classiques Django admin). Il déclenche `comptabilite.pcg.importer_pcg()`,
qui charge le jeu de données embarqué dans l'app
(`comptabilite/data/pcg_<millésime>.json`) et écrit en `bulk_create`/
`bulk_update` (idempotent — rejouable sans dupliquer, met à jour un
libellé modifié entre-temps), le tout dans une transaction unique. Même
fonction exposée en CLI (`manage.py importer_pcg`, pour un déploiement/CI).

*Historique* : la première version faisait un `update_or_create` par
compte (~1700 requêtes pour 861 comptes) — sur du matériel modeste (NAS),
cette volée de petites transactions pouvait dépasser le délai d'un worker
Gunicorn (30s) et le faire tuer en plein import (`SystemExit` dans
`connection.commit()`, plan comptable à moitié chargé). Passer en bulk
(quelques requêtes au total) ramène l'import à une fraction de seconde.

Ce jeu de données est un **instantané embarqué**, pas un téléchargement à
la volée depuis un site tiers au moment du clic : plus fiable (aucune
dépendance à la disponibilité d'un service externe en production), et le
contenu exact importé est versionné dans le dépôt. Source : le plan
comptable général publié annuellement par l'ANC (Autorité des Normes
Comptables), au format JSON structuré (`code`, `libellé`, `système`,
`parent`) republié par
[github.com/arrhes/PCG](https://github.com/arrhes/PCG) sous licence
CC0 (domaine public) — millésime 2026, 861 comptes. Pour passer
à un millésime plus récent : remplacer le fichier JSON embarqué et relancer
l'import (le `update_or_create` absorbe les libellés modifiés sans
dupliquer les comptes inchangés).

## Génération des écritures comptables (depuis les factures de vente)

Périmètre volontairement limité aux **factures de vente** : c'est le seul
document "comptable" existant dans l'app (`facturation.Facture`, pont vers
Tiime). Les achats n'ont pas d'équivalent "facture fournisseur" aujourd'hui
(seulement des commandes/réceptions logistiques dans l'app `achats`) — la
génération des écritures d'achat serait une évolution ultérieure séparée.

- **`EcritureComptable`** (journal, date, pièce, libellé, `facture`
  d'origine optionnelle) + **`LigneEcriture`** (compte, libellé, débit,
  crédit — jamais les deux à la fois, jamais aucun des deux :
  `LigneEcriture.clean()`). Une écriture peut aussi être saisie
  entièrement à la main (pas seulement générée).
- **Équilibre imposé côté admin** : `LigneEcritureFormSet.clean()`
  (formset personnalisé sur l'inline "Lignes d'écriture") refuse
  l'enregistrement si total débit ≠ total crédit — la partie double n'est
  jamais laissée en défaut ne serait-ce que temporairement.
- **`ParametresComptables`** : ligne de configuration unique (journal des
  ventes, compte client, compte de vente, compte de TVA collectée par
  défaut), modifiable dans l'admin (section "Paramètres comptables").
  Rien n'est figé dans le code : si un compte n'y est pas configuré,
  `ParametresComptables.charger()` retombe en mémoire (jamais écrit tant
  que l'utilisateur n'a rien choisi) sur le code PCG usuel correspondant
  s'il existe en base (411 — Clients, 706 — Prestations de services,
  44571 — TVA collectée) — fonctionne donc "out of the box" dès que le
  plan comptable officiel a été importé, sans étape de configuration
  obligatoire.
- **Génération** : `comptabilite.generation.generer_ecriture_facture(facture)`
  — regroupe les lignes de la commande facturée par taux de TVA
  (`CommandeLigne.montant_ht`/`montant_ttc`, valeurs courantes,
  surchargeables — pas celles figées du devis) et pose une ligne "Clients"
  au débit (TTC total) plus, par taux de TVA distinct, une ligne "Ventes"
  (HT) et une ligne "TVA collectée" (si non nulle) au crédit. Repli sur
  `Facture.montant_ht`/`montant_ttc` si la commande n'a aucune ligne
  chiffrée. Idempotent (`OneToOneField` facture ↔ écriture) : ne génère
  jamais deux écritures pour la même facture, renvoie l'existante.
  Exposé dans l'admin par l'action **"Générer l'écriture comptable"** sur
  la liste des factures (action de sélection standard, pas
  `actions_list` cette fois : contrairement à l'import du PCG, il y a ici
  une sélection naturelle — les factures cochées).

## Compte comptable spécifique par article + codes analytiques

- **`ArticleCompteVente`** (`comptabilite/models.py`) : associe un article à
  un compte de vente précis (`OneToOneField`), qui prime sur
  `ParametresComptables.compte_vente_defaut` lors de la génération d'une
  écriture — ex. un fabriqué facturé en 701 "Ventes de produits finis"
  plutôt que le 706 générique. `comptabilite.generation._repartition_lignes`
  regroupe désormais les lignes de la commande facturée par (taux de TVA,
  **compte de vente**, code analytique) plutôt que par seul taux de TVA :
  une même facture peut donc poser plusieurs lignes "Ventes" à des comptes
  différents selon les articles vendus, tout en restant équilibrée.
  Inline "Compte de vente d'article" sur la fiche Article, fiche dédiée
  dans `comptabilite/admin.py`.
- **`ArticleCompteAchat`** : même principe côté achat (compte de charge,
  ex. 601/607). Purement déclaratif pour l'instant — il n'existe pas de
  document "facture fournisseur" dans l'app (`achats` n'a que des
  commandes/réceptions logistiques), donc pas encore de génération
  automatique d'écriture d'achat ; sert de référence pour une saisie
  manuelle, et de point d'ancrage prêt pour une future génération.
- **`CodeAnalytique`** : dictionnaire libre de codes comptables
  complémentaires (comptabilité analytique — atelier, chantier, centre de
  coût...), même principe que `JournalComptable`. Optionnel sur
  `ArticleCompteVente`/`ArticleCompteAchat` (code analytique par défaut de
  l'article) et sur `LigneEcriture` (posable à la main sur n'importe quelle
  ligne). Repris automatiquement sur la ligne "Ventes" d'une écriture
  générée depuis la facture — **jamais** sur les lignes Clients/TVA
  collectée, qui ne portent pas de dimension analytique en pratique
  comptable courante.

## Postes de gestion + régime fiscal du tiers (import d'un référentiel existant)

Fonctionnalité demandée à partir de deux exports Excel fournis par
l'utilisateur ("poste de gestion achats"/"ventes" d'un logiciel de gestion
existant) : un même référentiel de 145 codes partagé entre achat et vente,
chacun avec un compte différent selon que le tiers est français, français
exonéré de TVA, intracommunautaire ou hors UE — nuance absente jusqu'ici de
`ArticleCompteVente`/`ArticleCompteAchat` (un seul compte fixe).

- **`Tiers.regime_fiscal`** (`commercial/models.py`) : France / France
  exonérée / Intracommunautaire (UE) / Hors UE. Détermine, pour une vente,
  quel compte utiliser parmi ceux du poste de gestion de l'article vendu
  (le client) ; pour un achat, pareil côté fournisseur.
- **`PosteGestion`** (`comptabilite/models.py`) : classification achat *et*
  vente à la fois (ex. "MP" Matière première : achetée ET revendue en
  négoce), avec 4 comptes d'achat (un par régime fiscal du fournisseur) et
  5 comptes de vente (un par régime + un "TVA majorée", cas particulier
  hérité de la source, non rattaché au régime fiscal — à sélectionner à la
  main si besoin) + un code analytique par défaut. Contrairement à
  `ArticleCompteVente`/`Achat`, un poste n'est pas limité à un seul
  article : il couvre aussi les charges générales qui n'en ont jamais
  (assurance, abonnements, carburant, télécom...).
- **`ArticleCompteVente`/`ArticleCompteAchat` étendus** : nouveau champ
  `poste_gestion`, prioritaire sur le compte fixe existant s'il est
  renseigné — `resoudre_compte_vente(regime_fiscal)`/
  `resoudre_compte_achat(regime_fiscal)` choisissent alors le bon compte
  du poste selon le régime fiscal du tiers. `clean()` impose l'un des deux
  (poste ou compte fixe), jamais aucun. `comptabilite.generation` lit
  désormais le régime fiscal du client (`facture.commande.devis.client`)
  pour résoudre le compte de vente de chaque ligne, et lève une erreur
  claire si le poste n'a pas de compte configuré pour ce régime précis
  plutôt que de deviner.
- **Achats sans article** (`achats.LigneCommandeFournisseur`) : `article`
  devient optionnel, nouveau `poste_gestion` (+ `designation` libre) pour
  les lignes de charge générale sans équivalent stocké — `clean()` impose
  l'un des deux. `ReceptionLigne` ne mouvemente plus le stock pour une
  ligne sans article (rien à réceptionner physiquement), mais continue de
  cumuler `quantite_recue`.
- **Import** : `comptabilite/data/postes_gestion.json` (les 145 postes,
  fusion des deux exports) + `comptabilite.postes_gestion.importer_postes_gestion()`,
  bouton "Importer les postes de gestion (achat/vente)" sur la liste des
  postes (même mécanisme `actions_list` que l'import du PCG). Écrit en
  bulk pour les mêmes raisons que `pcg.importer_pcg` (voir plus haut —
  leçon du timeout Gunicorn). Les comptes référencés descendent à un
  niveau de détail (6 chiffres, ex. `602100`) plus fin que le PCG officiel
  (max 5) : l'import les **crée automatiquement** s'ils manquent, en
  système développé, avec le libellé du premier poste qui les référence —
  un point de départ raisonnable, à affiner ensuite dans l'admin au besoin
  (177 comptes créés ainsi sur le jeu de données fourni).

## Enrichissement Tiers/Article : comptes par tiers, bibliothèque de paiement, pays, téléphones typés, gamme conditionnelle

Lot de six demandes ponctuelles.

- **`comptabilite.TiersCompteComptable`** : compte client et/ou compte
  fournisseur propres à un tiers (`OneToOneField`, les deux à la fois pour
  un tiers "les deux"), prioritaire sur
  `ParametresComptables.compte_client_defaut` — même principe que
  `ArticleCompteVente`. Le côté fournisseur reste déclaratif (pas de
  génération d'écriture d'achat, comme `ArticleCompteAchat`).
- **`commercial.ConditionPaiement`** : bibliothèque (remplace le texte
  libre `Tiers.conditions_paiement`, devenu une FK), avec `nombre_jours` +
  `fin_de_mois` plutôt qu'un simple libellé — exploitable plus tard pour
  calculer une échéance, contrairement à `DelaiPropose` qui n'est qu'une
  suggestion de texte.
- **`commercial.Pays`** (code ISO2, `est_ue`) + `Adresse.pays` : dès que
  l'adresse de livraison principale d'un tiers (repli sur la facturation
  principale si pas de livraison) a un pays renseigné,
  `Adresse.save()` recalcule automatiquement `Tiers.regime_fiscal` — France
  si le pays est la France, intracommunautaire si `est_ue`, hors UE sinon.
  Ne touche jamais un régime positionné manuellement sur "France exonérée"
  (indécidable depuis le seul pays). Jeu de départ : UE-27 + quelques
  partenaires courants, extensible dans l'admin.
- **`commercial.ContactTelephone`** : remplace l'ancien champ unique
  `Contact.telephone` — plusieurs numéros typés par contact (portable **et**
  bureau **et** fax en même temps), `ContactAdmin` en fiche dédiée avec
  l'inline (`Contact` était déjà enregistré seul, donc pas de limite
  d'inline imbriqué).
- **Gamme masquée si non fabriqué** : `technique/static/technique/article_admin.js`,
  `initGammeToggle()` — cache la section "Gammes" (`#gamme_etapes-group`)
  tant que `Nature ≠ Fabriqué`, sur le même principe que les autres
  toggles déjà en place sur la fiche Article.
- **`achats.TarifAchatArticle.frais_port`** : champ optionnel ajouté à
  l'historique de tarif déjà existant (voir plus haut, "Nouvelles natures
  d'article achetées + fournisseurs multiples").

**Changement cassant assumé** : `Tiers.conditions_paiement` passe de texte
libre à une liste déroulante — toute valeur déjà saisie à la main est
perdue à la migration (pas de conversion automatique, discuté avec
l'utilisateur : aucune donnée de production n'en dépendait encore).

## IBAN, échéance calculée, devise, comptes auxiliaires auto-générés

Quatre pistes proposées après le lot précédent, validées une à une par
l'utilisateur.

- **`Tiers.iban` / `Tiers.bic`** : coordonnées bancaires du tiers (utile
  côté fournisseur en vue d'un virement). `commercial.models.valider_iban()`
  vérifie le format et la clé de contrôle (modulo 97, ISO 13616) dans
  `Tiers.clean()` — une faute de frappe est détectée avant de partir dans
  un virement, plutôt que de stocker un IBAN invalide tel quel.
- **`Facture.date_echeance`** (propriété calculée, pas de colonne) :
  `self.commande.devis.client.conditions_paiement.calculer_echeance(self.date_facturation)`.
  `ConditionPaiement.calculer_echeance()` ajoute `nombre_jours` à la date de
  référence puis, si `fin_de_mois`, reporte au dernier jour du mois
  obtenu. Renvoie `None` si le client n'a pas de conditions de paiement,
  ou si celles-ci sont purement descriptives (`nombre_jours` vide, ex.
  "Comptant" sans jour chiffré). Affichée en lecture seule dans la liste
  des factures.
- **`commercial.Devise`** (référentiel ISO 4217, jeu de départ EUR/USD/GBP/CHF
  via migration de données, extensible dans l'admin) + `Tiers.devise` et
  `Commande.devise` — un tiers hors UE (régime fiscal "Hors UE") ne
  facture pas forcément en euros, ce que le régime fiscal seul ne dit pas.
  `Commande.devise` reprend automatiquement celle du client à la création
  (`chiffrage.production.lancer_en_production()`), sans empêcher de la
  changer ensuite au cas par cas.
- **Comptes auxiliaires auto-générés** (`TiersCompteComptable.code_client` /
  `code_fournisseur`) : convention propre au cabinet comptable de
  l'utilisateur — 5 caractères (lettres et/ou chiffres) qu'il détermine
  lui-même, concaténés à "411" (compte client) ou "401" (compte
  fournisseur). `clean()` valide le format (exactement 5 caractères
  alphanumériques) ; `save()` résout ou crée le `CompteComptable`
  correspondant (système développé) et l'assigne à `compte_client`/
  `compte_fournisseur` — pas de doublon si le même code est réutilisé pour
  un autre tiers (`get_or_create` sur le code du compte). Les champs
  `compte_client`/`compte_fournisseur` restent utilisables directement en
  échappatoire (compte déjà existant, numérotation différente) quand aucun
  code à 5 caractères n'est renseigné.

## Écriture comptable d'achat

`comptabilite.generation.generer_ecriture_facture` (vente) avait son
pendant manquant côté achat : `ArticleCompteAchat` et
`PosteGestion.compte_achat_*` étaient déjà en place, mais purement
déclaratifs, faute de document "facture fournisseur" auquel les
accrocher — l'app achats ne portait que des documents logistiques
(`CommandeFournisseur`, `Reception`).

- **`achats.FactureFournisseur`** : symétrique de `facturation.Facture`
  côté vente — la facture "légale" arrive du fournisseur (papier/email/PDF,
  hors ERP), ce modèle garde une trace interne (numéro généré par
  codification, `reference_fournisseur` pour le numéro réel donné par le
  fournisseur, montants HT/TTC, statut de paiement) et sert de point de
  départ à la génération de l'écriture.
- **`LigneCommandeFournisseur.taux_tva`** (+ `montant_ht`/`montant_ttc`
  calculés) : manquait pour pouvoir répartir la TVA déductible par taux,
  même principe que `CommandeLigne` côté vente.
- **`achats.generation.generer_ecriture_achat()`** : Fournisseurs (401) au
  crédit — compte spécifique du fournisseur si `TiersCompteComptable` en a
  un, sinon le compte par défaut — Achats + TVA déductible au débit, une
  paire de lignes par groupe (taux de TVA, compte d'achat, code
  analytique) distinct sur la commande fournisseur facturée. Le compte
  d'achat est celui d'`ArticleCompteAchat` pour une ligne avec article
  (résolu selon le régime fiscal du fournisseur), celui du poste de
  gestion pour une ligne de charge générale sans article, sinon le compte
  d'achat par défaut. Structure rigoureusement symétrique de
  `generer_ecriture_facture` (débit/crédit inversés) — mêmes règles de
  conception (idempotent, repli sur les montants globaux si la commande
  n'a aucune ligne, code analytique jamais posé sur la ligne
  Fournisseurs/TVA).
- **`ParametresComptables`** : quatre nouveaux champs côté achat (journal,
  compte fournisseur/achat/TVA déductible par défaut), avec repli sur les
  codes PCG usuels 401/601/44566 comme pour les champs vente existants ;
  `journal_achats` préconfiguré sur "AC" par migration de données, même
  principe que `journal_ventes`/"VT".
- **`EcritureComptable.facture_fournisseur`** : `OneToOneField` référencé
  par nom d'app (`"achats.FactureFournisseur"`), pas importé directement —
  `achats` importe déjà `comptabilite.models` (pour `PosteGestion` et
  maintenant `EcritureComptable`/`LigneEcriture`/`ParametresComptables` via
  `achats.generation`), un import direct dans l'autre sens créerait un
  cycle. Django résout la chaîne de caractères après le chargement de
  toutes les apps — aucun souci d'ordre d'import.
- **Action admin** "Générer l'écriture comptable" sur la liste des
  factures fournisseur, même mécanisme que côté facture de vente.

## Fiche Tiers : téléphones imbriqués, aperçu de compte en direct, réorganisation

Trois retours sur la fiche Tiers, après vérification qu'une partie de la
demande (email et association contact/adresse de livraison) était déjà en
place.

- **Numéros de téléphone imbriqués** : `ContactTelephone` (plusieurs
  numéros typés par contact) est maintenant saisissable directement dans
  le tableau "Contacts" de la fiche Tiers, sans passer par la fiche
  Contact dédiée — `ContactInline.inlines = [ContactTelephoneInline]`,
  rendu par `unfold.admin.ModelAdmin` qui embarque nativement
  `NestedInlinesModelAdminMixin` (pas de nouvelle dépendance, ni de
  changement de modèle).
- **Aperçu du compte comptable en direct** : dès que 5 caractères valides
  sont saisis dans "Code client"/"Code fournisseur"
  (`TiersCompteComptable`), un texte apparaît sous le champ indiquant le
  compte qui sera utilisé — son libellé s'il existe déjà en base ("→
  411DUPON — Dupont SAS (compte existant)"), ou qu'il sera créé sinon.
  Nouvel endpoint `apercu_compte_comptable_view` (staff uniquement,
  `commercial/admin.py`) interrogé en AJAX par
  `commercial/static/commercial/tiers_admin.js` (débounce 300 ms),
  suivant le même principe que les aperçus déjà en place sur la fiche
  Devis (`chiffrage/builder_views.py`).
- **Réorganisation de la fiche** : le fieldset "Commercial" passe en
  dernière position (après "Coordonnées bancaires") et l'inline "Compte
  comptable de tiers" passe en tête des tableaux (avant "Adresses" et
  "Contacts") — Django affiche toujours tous les fieldsets avant tous les
  inlines, cette combinaison est ce qui rapproche le plus les deux blocs
  demandés.

**Correctifs après retours utilisateur** :

- "Adresse de livraison associée" (`ContactAdmin`, fiche Contact autonome)
  utilisait `autocomplete_fields`, qui interroge `AdresseAdmin` sans aucun
  filtre — le menu proposait les adresses de n'importe quel tiers.
  Restreint, via `get_form`/`formfield_for_foreignkey`, aux seules adresses
  de livraison du tiers du contact — vide (avec un `help_text` explicite)
  tant que le contact et son tiers n'ont pas été enregistrés une première
  fois.
- Le même champ, sur le tableau Contacts de la fiche Tiers
  (`ContactInline`), avait d'abord reçu la même restriction — mais elle
  rendait le champ inutilisable à la création d'un tiers : aucune adresse
  n'existe encore en base tant que le tiers n'a pas été enregistré, donc
  impossible de lier un contact à une adresse tout juste tapée dans le
  même formulaire (signalé par l'utilisateur, capture à l'appui). Corrigé
  différemment ici : `adresse_livraison` est exclu du formulaire
  (`ContactInlineForm`, `exclude = ["adresse_livraison"]`) et remplacé par
  un champ `adresse_livraison_ref` qui référence une ligne du tableau
  Adresses par son indice (ex. `"1"` pour `adresses-1-*`), pas par un pk —
  une ligne pas encore enregistrée n'en a pas encore au moment où Django
  valide le formulaire. Les options du `<select>` sont construites en JS
  (`tiers_admin.js`) à partir des lignes du tableau Adresses affichées à
  l'écran (type Livraison uniquement, réactif à la frappe et aux lignes
  ajoutées/supprimées) — jamais interrogées en base. `TiersAdmin.
  save_related()` résout la référence en instance `Adresse` réelle une
  fois que toutes les adresses ont vraiment été enregistrées (y compris
  celles créées dans la même requête), et revalide au passage que
  l'adresse ciblée appartient bien à ce tiers et est de type Livraison —
  le contrôle que `Contact.clean()` fait normalement, mais qui n'est plus
  déclenché pour ce champ puisqu'il est exclu du `ModelForm`.
  `ContactAdmin` (fiche Contact autonome, sans tableau Adresses à côté)
  garde la première approche, plus simple, suffisante dans son contexte.

## Facture : montants et échéance calculés à titre indicatif depuis la commande

`montant_ht`/`montant_ttc` restent des champs saisis à la main (la facture
réelle, émise dans Tiime, fait foi — peut différer : facturation partielle
d'une commande sur plusieurs factures, remise, arrondi) mais n'étaient
jusqu'ici calculables d'aucune façon depuis les lignes de la commande.

- **`Facture.montant_ht_calcule`/`montant_ttc_calcule`** (propriétés) :
  somme des lignes actuelles de la commande (`CommandeLigne.montant_ht`/
  `montant_ttc`), en ignorant les lignes sans prix renseigné — même
  logique que `comptabilite.generation._repartition_lignes` côté
  génération d'écriture. `None` si la commande n'a aucune ligne chiffrée.
- **Pré-remplissage automatique** sur le formulaire d'ajout d'une facture :
  dès qu'une commande est choisie, `facturation/facture_admin.js`
  interroge `montants_calcules_commande_view` (nouvel endpoint AJAX,
  `facturation/admin.py`) et renseigne `montant_ht`/`montant_ttc` — sans
  jamais écraser une valeur déjà saisie à la main, même principe que
  `chiffrage/devis_admin_live.js`.
- **Rappel en lecture seule** sur la fiche (ajout et modification) :
  "Montants calculés depuis la commande (indicatif)" — utile pour repérer
  après coup un écart avec les lignes actuelles de la commande (ex.
  modifiées depuis la création de la facture).

**Non traité, sur demande explicite de l'utilisateur** : un vrai suivi
des règlements (date, montant, rapprochement bancaire) — `statut_paiement`
reste un texte libre. Chantier plus lourd, à faire séparément le jour où
Tiime ne suffit plus comme source de vérité sur les encaissements.

## Correctif : ligne de téléphone vide bloquant l'enregistrement d'un tiers

`ContactTelephoneInline` (`extra=1`) affiche une ligne vide sous chaque
contact. Sans valeur par défaut sur `type_telephone`, le `<select>` du
navigateur sélectionnait son premier choix ("Portable") même sans qu'on y
touche — suffisant pour que Django considère la ligne comme modifiée, et
donc exige un numéro (champ obligatoire) même sur une ligne qu'on ne
voulait pas remplir. `ContactTelephone.type_telephone` a désormais
`default=TypeTelephone.PORTABLE` : la valeur par défaut du modèle
correspond alors exactement à ce que le `<select>` affiche sans
interaction, et Django détecte correctement qu'une ligne non touchée n'a
pas changé (elle est simplement ignorée, comme prévu) — sans rien
affaiblir : une ligne où l'utilisateur renseigne effectivement un numéro
reste normalement validée.

## Autocomplétion d'adresse (API Adresse, data.gouv.fr)

Le champ "Adresse" (`Adresse.adresse`) propose désormais des suggestions
en tapant, via l'**API Adresse** de l'État (Base Adresse Nationale,
Etalab) : `commercial/static/commercial/adresse_autocomplete.js`,
interrogée en direct depuis le navigateur (`api-adresse.data.gouv.fr/search/`,
gratuite, sans clé, CORS ouvert — aucun détour par le backend). Au clic
sur une suggestion, "Code postal" et "Ville" se remplissent avec elle.
Fonctionne aussi bien sur la fiche Adresse autonome que sur le tableau
Adresses imbriqué dans la fiche Tiers (`AdresseInline`) : le script
détecte tout seul si le champ s'appelle `adresse` (fiche seule) ou
`adresses-N-adresse` (ligne de tableau), et en déduit le nom des champs
frères à remplir. Ne couvre que la France (le service lui-même) — une
adresse hors France reste à saisir à la main, comme avant.

**Vérification** : la construction de la requête, l'affichage des
suggestions et le remplissage des champs ont été vérifiés avec l'appel
réseau intercepté (réponse simulée) — l'environnement d'exécution de cette
session bloque explicitement les appels sortants vers ce domaine
(politique réseau du bac à sable, sans rapport avec le navigateur réel de
l'utilisateur, qui appellera l'API directement, sans passer par ce
même proxy).

## Contact associé à une adresse de facturation (en plus de livraison)

`Contact.adresse_livraison` ne couvrait que les adresses de type
Livraison (section précédente "Fiche Tiers : téléphones imbriqués...") —
signalé comme trop restrictif : un contact comptabilité, par exemple, est
associé à une adresse de facturation, pas de livraison.

Renommé en `Contact.adresse_associee` (migration `RenameField` +
`AlterField` écrite à la main, pour préserver les données existantes —
`makemigrations` non interactif aurait par défaut fait un
`RemoveField`+`AddField`, perdant les liens déjà enregistrés) : accepte
maintenant indifféremment une adresse de Livraison ou de Facturation du
même tiers. `Contact.clean()` ne vérifie plus que le type — de toute
façon `Adresse.TypeAdresse` n'en compte que deux, la restriction n'avait
plus de sens dès lors que les deux sont acceptés — seule l'appartenance
au bon tiers reste contrôlée. Même changement côté `ContactInlineForm`/
`TiersAdmin._resoudre_reference_adresse` (fiche Tiers) et `ContactAdmin.
formfield_for_foreignkey` (fiche Contact autonome), qui filtraient tous
les deux sur le type Livraison uniquement.

Le `<select>` "Adresse associée" du tableau Contacts (fiche Tiers)
préfixe maintenant chaque option par son type ("Livraison — Site
principal", "Facturation — Service comptabilité") pour les distinguer
quand un tiers a les deux.

**Vérifié** : création d'un tiers avec une adresse de livraison et une
adresse de facturation dans le même enregistrement, contact lié à
l'adresse de facturation dès la création (sans pk pour l'adresse au
moment de la validation du formulaire — même mécanisme que pour
livraison) — confirmé en base après enregistrement.

## Autocomplétion SIRET/SIREN <-> raison sociale + adresse du siège

En tapant la raison sociale d'un tiers, l'application propose maintenant
les entreprises correspondantes (nom, SIRET, adresse du siège) — et
inversement, en tapant un SIREN ou un SIRET, elle propose de compléter la
raison sociale et l'adresse. Basé sur **Recherche d'entreprises**
(`recherche-entreprises.api.gouv.fr`, base SIRENE, service public gratuit
sans clé, CORS ouvert) : `commercial/static/commercial/
entreprise_lookup.js`, interrogé en direct depuis le navigateur, sur le
même principe que l'autocomplétion d'adresse (section précédente).
Chargé uniquement sur `TiersAdmin` (fiche Tiers), seule fiche portant à
la fois "Raison sociale" et "SIRET".

- **En tapant "Raison sociale"** (≥ 3 caractères) : une liste déroulante
  de suggestions apparaît (nom + adresse du siège) ; la sélection remplit
  "SIRET" et propose l'adresse du siège (voir ci-dessous).
- **En tapant "SIRET"** (9 chiffres — un SIREN — ou 14 — un SIRET
  complet) : dès qu'une correspondance exacte est trouvée, complète
  automatiquement "Raison sociale" (seulement si elle est encore vide, un
  SIRET ne prouvant pas que le nom déjà saisi est erroné) et, si un
  SIREN seul avait été tapé, complète le champ en SIRET complet du siège
  — c'est le sens inverse demandé : « ou l'inverse en tapant le Siren ou
  le Siret ».
- **Adresse du siège proposée** : uniquement si le tableau Adresses de la
  fiche Tiers est encore entièrement vide (`adresses-TOTAL_FORMS == 0`)
  — dès qu'une adresse existe, impossible de savoir si c'est celle du
  siège ou une autre, mieux vaut ne rien écraser plutôt que deviner. Le
  cas échéant, une ligne "Livraison — Siège" est ajoutée et remplie
  automatiquement, en simulant le même clic sur "Ajouter un objet Adresse
  supplémentaire" que ferait l'utilisateur (aucun accès direct au DOM
  interne du formset Django/Unfold) puis en écoutant l'événement
  `formset:added` qu'Unfold déclenche une fois la ligne effectivement
  ajoutée et réindexée, pour la remplir au bon indice.

**Vérifié** (appel réseau intercepté — même contrainte réseau du bac à
sable que pour l'autocomplétion d'adresse, sans rapport avec le
navigateur réel de l'utilisateur) : suggestions affichées en tapant une
raison sociale, sélection remplissant SIRET + adresse (nouvelle ligne
"Livraison — Siège") ; saisie d'un SIRET connu remplissant
automatiquement raison sociale + adresse ; non-régression vérifiée dans
les deux sens quand une raison sociale ou une adresse étaient déjà
saisies à la main (rien n'est écrasé, aucune ligne fantôme ajoutée).

## Correctif : numéro de téléphone d'un contact rendu optionnel

Signalé : le numéro de téléphone d'un contact n'est pas toujours connu au
moment de la saisie, mais `ContactTelephone.numero` était un champ
obligatoire — bloquant dès qu'on choisissait un type (ex. "Bureau") sans
renseigner de numéro (une ligne réellement vide et non touchée était déjà
tolérée, voir "Correctif : ligne de téléphone vide bloquant
l'enregistrement d'un tiers").

`numero` passe à `blank=True`. `ContactTelephone.__str__()` et
`ContactAdmin.telephones_display` (colonne "Téléphones" de la liste des
contacts) s'adaptent : un numéro absent affiche simplement le type
("Bureau") plutôt que "Bureau : " suivi de rien.

## Adresse : livraison et facturation à la fois

`Adresse.type_adresse` (un choix unique, Livraison OU Facturation) est
remplacé par deux cases à cocher indépendantes, `est_livraison` et
`est_facturation` — une même adresse peut donc être les deux en même
temps (ex. un client dont le siège reçoit aussi bien les marchandises que
les factures), plutôt que de devoir la dupliquer en deux lignes
identiques. Migration `RenameField`-style écrite à la main (`AddField` ×2
→ report des données existantes → `RemoveField`) pour ne perdre aucune
donnée. Au moins une case doit être cochée (`Adresse.clean()`).

Tout ce qui filtrait ou affichait par `type_adresse` est adapté :
`Adresse.__str__`/nouvelle propriété `types_affiches` ("Livraison",
"Facturation" ou "Livraison + Facturation"), unicité de l'adresse
principale (désormais vérifiée indépendamment par type, une adresse
cochée pour les deux devant être unique sur chacun des deux volets),
application du régime fiscal (même priorité livraison puis repli
facturation qu'avant), recherche de l'adresse principale par type
(`chiffrage.production._adresse_principale`, `valeurs_defaut_tiers_view`),
et le JS (`tiers_admin.js`, `entreprise_lookup.js`) qui construisait le
libellé "Livraison — Nom" du sélecteur "Adresse associée" d'un contact.

## Glisser-déposer des lignes de devis + conversion en commande en un clic

Deux demandes liées : pouvoir réordonner les lignes d'un devis, et que cet
ordre soit repris sur la commande une fois convertie.

- **`DevisLigne.ordre`** (nouveau champ, entier) pilote désormais l'ordre
  d'affichage (`Meta.ordering = ["devis", "ordre", "id"]`) — et, de fait,
  l'ordre des `CommandeLigne` créées par `lancer_en_production` (qui
  itère `devis.lignes.all()`, donc déjà trié).
- **Glisser-déposer natif** (HTML5 `draggable`, pas de librairie externe) :
  `chiffrage/static/chiffrage/devisligne_reorder.js`, une poignée "⠿"
  ajoutée devant chaque ligne du tableau "Lignes de devis". Au dépôt,
  renumérote le champ caché `ordre` de chaque ligne selon sa nouvelle
  position DOM — jamais affiché ni saisi à la main, persisté au prochain
  "Enregistrer" comme le reste du formulaire.
  **Piège rencontré et corrigé** : la ligne "extra" toujours vide du
  tableau (`DevisLigneInline.extra = 1`) ne doit *jamais* recevoir de
  valeur d'ordre tant qu'aucun article n'y est choisi — sinon Django la
  considère comme "modifiée" et exige qu'elle soit intégralement remplie
  (article, quantité), bloquant tout l'enregistrement. Même classe de bug
  que le correctif précédent sur les lignes de téléphone/adresse vides ;
  couverte par un test de régression Python de bout en bout
  (`ReordonnerLignesDevisAdminTests`) qui rejoue le POST équivalent.
- **"Convertir en commande" en un clic** : la conversion existait déjà
  comme action d'admin sur la liste des devis ("Lancer en production",
  `lancer_en_production`) — désormais aussi accessible directement
  depuis la fiche d'un devis validé (menu d'outils de la fiche, à côté de
  "Constructeur de devis"), sans repasser par la liste. Nouvelle vue
  `convertir_en_commande_view` (POST, même logique que l'action
  existante) ; si une commande existe déjà pour ce devis, le bouton est
  remplacé par un lien direct vers elle.

**Vérifié** : glisser-déposer d'une ligne en tête de tableau puis
enregistrement — l'ordre persiste après rechargement ; conversion d'un
devis validé en un clic, redirection vers la commande créée avec ses
lignes dans le même ordre que le devis.

## Fiche Commande : création directe, calcul en direct, TVA automatique

Sept demandes liées, toutes centrées sur la fiche Commande — jusqu'ici
uniquement modifiable après coup (créée par `lancer_en_production` depuis
un devis), sans les conforts déjà en place côté Devis.

- **Devis facultatif** (`Commande.devis` passe à `null=True, blank=True`) :
  une commande peut désormais être créée directement depuis
  `/admin/chiffrage/commande/add/`, sans passer par un devis. Migration en
  3 temps (`AddField` du nouveau `client` sans contrainte -> report des
  données depuis `devis.client` pour les commandes existantes ->
  `AlterField` non-nullable) pour ne rien perdre.
- **`Commande.client`** (nouveau, obligatoire) : jusqu'ici le client
  n'était accessible que via `commande.devis.client` — impossible dès lors
  que le devis devient facultatif. Pré-rempli depuis `devis.client` par
  `lancer_en_production`/le "Convertir en commande" de la section
  précédente, mais toujours modifiable ensuite (demandé explicitement).
- **`Commande.reference_client`** (nouveau, obligatoire) : la référence
  que le client donne à sa propre commande (numéro de bon de commande...).
  Comme cette référence n'est jamais connue au moment de la conversion
  d'un devis, `lancer_en_production` la laisse vide à la création — à
  compléter ensuite à la main, la fiche l'exigera dès le premier
  enregistrement.
- **Adresses filtrées par client** : dès que "Client" est choisi (ajout
  comme modification), "Adresse de facturation"/"Adresse de livraison" se
  pré-remplissent avec les adresses principales de ce client — réutilise
  l'endpoint déjà exposé côté Devis (`valeurs_defaut_tiers_view`), sans
  jamais écraser un choix déjà fait (même principe que partout ailleurs
  dans ce projet).
- **Bouton "Créer une commande directement" supprimé** (et son détour par
  un devis-support jamais montré au client, `?commande_directe=1`) : les
  points ci-dessus le rendent inutile, la commande se crée maintenant
  directement avec les mêmes conforts.
- **Taux de TVA = Article × régime fiscal du client**, automatique mais
  modifiable : nouveau `Article.taux_tva` (taux "normal", régime France)
  et `chiffrage.moteur.resoudre_taux_tva(article, client)` — un client au
  régime France applique le taux de l'article (ou le taux par défaut du
  référentiel s'il n'en a pas), un client exonéré/intracommunautaire/hors
  UE applique toujours 0 % (nouveau taux "Taux zéro" ajouté au
  référentiel). Simplement suggéré : le `<select>` "Taux de TVA" n'est
  rempli que s'il est encore vide, jamais écrasé une fois modifié à la
  main.
- **Calcul automatique du prix** (quantité / gamme / nomenclature), comme
  pour le devis : nouvelle fonction `previsualiser_ligne_commande` et
  endpoints dédiés (`recalculer_ligne_commande_view`,
  `previsualiser_ligne_commande_view`,
  `previsualiser_ligne_nouvelle_commande_view`), mêmes règles que
  `previsualiser_ligne` mais toujours avec les marges par défaut de
  l'article/poste — `CommandeLigne` n'a pas de champ de surcharge de marge
  par ligne. Ne s'applique **que** sur une ligne sans devis d'origine
  (`devis_ligne` vide) : une ligne héritée d'un devis reste une
  *surcharge* au sens du modèle, jamais recalculée toute seule — changer
  sa quantité ne touche que la quantité, pas le prix, jusqu'à ce qu'on le
  retouche explicitement.
- **Date de livraison copiée de la ligne précédente** : à l'ajout d'une
  nouvelle ligne dans le tableau, sa date de livraison prévue reprend
  celle de la dernière ligne déjà présente (signalé comme le cas le plus
  courant — plusieurs lignes saisies à la suite pour la même livraison) —
  seulement si la nouvelle ligne n'en a pas encore.

`commande_admin_live.js` (nouveau) regroupe ces quatre derniers
comportements ; `commande_admin_live.js`/`devis_admin_live.js` partagent
le même formset "lignes" (`CommandeLigne.commande` et `DevisLigne.devis`
ont toutes deux `related_name="lignes"`) mais s'exécutent sur des pages
différentes, sans conflit.

**Vérifié** : création d'une commande sans devis avec calcul de prix et
suggestion de TVA en direct sur une ligne fraîchement ajoutée ; adresses
pré-remplies dès le choix du client ; date de livraison copiée sur une
deuxième ligne ; bouton "Créer une commande directement" bien absent du
formulaire d'ajout d'un devis.

## Fusion du module « Chiffrage découpe laser / jet d'eau »

L'app `decoupe` (voir section dédiée plus haut) avait été développée sur une
branche séparée (`claude/great-dijkstra-ilhwgi`), déjà réintégrée à cette
branche-ci en amont — la fusion s'est donc faite en avance rapide, sans
conflit. `decoupe/admin.py` utilisait encore l'admin Django brut
(`django.contrib.admin.ModelAdmin`/`TabularInline`) au lieu des classes
habillées `unfold.admin` employées par le reste du projet — corrigé pour
rester cohérent visuellement avec les autres fiches (le champ "Fichier
source" bénéficie maintenant du même composant de dépôt de fichier que le
reste de l'admin).

**Vérifié** : import d'un fichier DXF d'exemple (`decoupe/exemples/`) via le
formulaire d'ajout — géométrie extraite automatiquement (surface, périmètre,
dimensions, contours intérieurs), statut passé à "Importée".

## Numéro de version

Le NAS n'a pas `.git` (le code y arrive par archive tar.gz via
`update-nas.sh`) : impossible d'y afficher un hash de commit. La version
suivie ici est donc un simple fichier texte, `VERSION` à la racine du dépôt
(ex. `2026.09.25.1`), incrémenté par Claude à chaque push notable.

- **Badge dans l'admin** : `UNFOLD["ENVIRONMENT"]` (`comptes/version.py`,
  `badge_environnement`) affiche `v<VERSION>` en pastille bleue en haut à
  droite de **toutes** les pages admin (mécanisme Unfold standard — cf.
  `unfold/helpers/label.html`/`userlinks.html`), et
  `UNFOLD["ENVIRONMENT_TITLE_PREFIX"]` (`prefixe_titre`) l'ajoute aussi
  dans l'onglet du navigateur (`[v<VERSION>] Nom de la page`). Deux
  emplacements pour la même info, visibles sans avoir à ouvrir un menu.
- **Vérification en terminal** : `update-nas.sh` affiche désormais
  `Version déployée : <contenu de VERSION>` à la fin de la mise à jour,
  juste après l'état des conteneurs — pratique pour confirmer par SSH
  qu'un `git push` a bien été pris en compte, sans passer par le
  navigateur.

Pour vérifier que la dernière version est bien installée : comparer le
badge affiché dans l'admin (ou la sortie d'`update-nas.sh`) avec le
contenu du fichier `VERSION` sur la branche `claude/project-construction-k7owwb`
sur GitHub.

**Vérifié** : badge `v2026.09.25.1` visible sur le tableau de bord et sur
une page de liste (ex. Devis) ; préfixe `[v2026.09.25.1]` présent dans le
titre de l'onglet du navigateur sur les deux pages.

## Analyse en direct et aperçu visuel des pièces à découper

Auparavant, l'extraction de la géométrie (surface, périmètre, contours...)
n'avait lieu qu'à l'enregistrement de la fiche `PieceDecoupe`, et rien
n'affichait la silhouette de la pièce. Deux ajouts sur `decoupe/admin.py` :

- **Analyse dès la sélection du fichier, avant tout enregistrement** :
  `decoupe/static/decoupe/piecedecoupe_admin.js` écoute le `change` du champ
  "Fichier source" et envoie le fichier en AJAX vers une nouvelle vue
  (`decoupe/admin_views.py::analyser_fichier_view`,
  `/admin/decoupe/piecedecoupe/analyser/`) qui appelle directement
  `extraire_geometrie()` sur un fichier temporaire, **sans rien
  persister** — l'enregistrement réel (via `PieceDecoupe.importer_geometrie()`)
  a toujours lieu normalement au clic sur "Enregistrer", cette vue ne fait
  qu'anticiper le même résultat pour l'affichage. Les champs Statut, Format
  source, Message erreur, Avertissements, Surface, Périmètre, Largeur,
  Hauteur et Nb contours se mettent à jour en direct avec le résultat.
- **Aperçu visuel** : nouveau champ "Aperçu" (méthode `apercu_piece`) qui
  affiche la silhouette de la pièce (contour extérieur + trous) en SVG,
  aussi bien en direct sur le formulaire (avant save, à partir de la
  réponse AJAX) que sur une fiche déjà enregistrée (à partir de
  `contour_json` persisté). Réutilise le rendu de contour de
  `decoupe/services/apercu_svg.py` (jusque-là utilisé seulement pour
  l'aperçu d'une feuille imbriquée) via une nouvelle fonction
  `generer_svg_piece()`, dédiée à une pièce seule sans feuille englobante.

Point technique notable : Unfold ne pose une classe CSS `field-<nom>` (point
d'accroche pour du JS ciblant un champ readonly précis) que sur ses tableaux
inline, pas sur les champs readonly de premier niveau d'un formulaire — même
contrainte déjà rencontrée sur `DevisAdmin` (montants HT/TTC). Les champs
concernés sont donc exclus du formulaire (`exclude`) et remplacés par des
méthodes `readonly_fields` dédiées (`statut_display`, `surface_mm2_display`,
etc.), chacune enveloppant sa valeur dans un `<span id="decoupe-...">` que le
JS peut cibler de façon stable.

**Vérifié** : import d'un fichier DXF d'exemple (pièce ronde à trou central) —
analyse (statut, surface, périmètre, dimensions, contours) et aperçu SVG
affichés en direct avant tout clic sur "Enregistrer" ; aperçu toujours présent
après enregistrement sur la fiche de modification.

## Matière/épaisseur à l'import, profils de calques, rotation libre et miroir

Quatre ajouts sur le module découpe, pensés du point de vue devis/méthodes :

### Matière, épaisseur et pas de rotation dès l'import

`PieceDecoupe` gagne trois champs saisissables dès le formulaire d'ajout (avant
tout enregistrement, comme le reste de l'analyse en direct) :

- **`matière`** et **`épaisseur`** : la pièce n'a plus besoin d'être déjà liée
  à un article fabriqué pour enregistrer dans quelle tôle elle doit être
  découpée.
- **`pas_rotation_deg`** (5° / 45° / 90°, ou vide) remplace l'ancienne case à
  cocher "rotation autorisée" (oui/non) : on choisit maintenant la finesse de
  rotation autorisée pour l'imbrication, pas seulement si elle l'est.
- **`symétrie_autorisée`** (case à cocher, cochée par défaut) : à décocher
  quand la pièce ne peut pas être retournée (miroir) — gravure, marquage non
  symétrique...

### Profils d'import (calques DXF/DWG)

Nouveau modèle **Profil d'import** (menu "Chiffrage découpe → Profils
d'import") : une liste de règles *calque → rôle* (Découpe / Gravure-marquage
/ Pliage / Ignoré), réutilisable d'un import à l'autre plutôt que de reclasser
les calques à la main à chaque fois.

Corrige au passage un vrai défaut, pas seulement un confort : **sans profil**,
`extraire_geometrie()` traitait toutes les entités du fichier comme de la
découpe, sans distinction de calque — un logo gravé (tracé fermé, mais pas
destiné à être découpé) dessiné sur un calque séparé était donc détecté à tort
comme un **trou à découper**. Avec un profil sélectionné à l'import (champ
`profil_import` sur la fiche PieceDecoupe, appliqué aussi à l'analyse en
direct avant enregistrement), seuls les calques classés "Découpe" alimentent
la reconstruction du contour (silhouette + vrais trous) ; les calques
"Gravure"/"Pliage" sont extraits à part comme de simples tracés (longueur de
gravure calculée séparément, pas comptée dans le périmètre de découpe) ; les
calques "Ignoré" sont exclus. Un calque présent dans le fichier mais absent
du profil reste traité comme découpe, avec un avertissement (plutôt que
silencieusement ignoré, pour ne jamais faire disparaître de la matière à
découper sans le signaler).

L'aperçu SVG de la pièce (déjà en place) affiche maintenant la découpe en
bleu, la gravure en orange et le pliage en vert pointillé — pour vérifier
d'un coup d'œil que le classement des calques est correct.

Un nouveau champ `a_gravure`, détecté automatiquement (informatif, pas
modifiable), signale la présence de tracés de gravure. Le JS du formulaire
d'ajout (`piecedecoupe_admin.js`) décoche automatiquement "symétrie
autorisée" dès qu'une gravure est détectée en direct — une suggestion, pas
une contrainte imposée après coup : rien ne revient la recocher/décocher
silencieusement lors d'un réimport ultérieur, c'est toujours la décision
explicite de l'utilisateur qui prévaut une fois la fiche enregistrée.
Changer de profil d'import sur une fiche déjà enregistrée relance
automatiquement l'analyse (pas besoin de réuploader le fichier).

### Imbrication à angles de rotation libres

Le moteur d'imbrication (`decoupe/services/imbrication.py`) n'essayait
auparavant que deux orientations par pièce (0° et 90°, sur la seule base de
sa largeur/hauteur à plat). Il essaie maintenant **toutes les orientations
autorisées par `pas_rotation_deg`** (jusqu'à 72 angles différents pour un pas
de 5°), calculées à partir du contour réel de la pièce (pas juste de son
rectangle englobant à 0°) — les plus compactes en premier. Ça change
réellement le résultat : une pièce en losange (carré tourné à 45°, par
exemple) ne tient pas dans une feuille à 0°/90° mais tient une fois ramenée à
son orientation "carrée" à 45°, une case que l'ancien moteur ne considérait
tout simplement jamais.

### Miroir : ajouté au moteur, mais sans effet observable pour l'instant — et c'est voulu

`symetrie_autorisee` et un champ `Placement.miroir` existent bien dans le
moteur. Mais en creusant l'implémentation, un fait géométrique s'est imposé :
**retourner une pièce (miroir) ne change jamais la largeur ni la hauteur de
son rectangle englobant**, quelle que soit sa forme — une réflexion est une
isométrie qui préserve l'étendue de la pièce sur chaque axe. Pour un moteur
qui compare des pièces par leur rectangle englobant (c'est le cas ici, pas
une imbrication polygonale exacte), retourner une pièce n'ouvre donc jamais
une possibilité de placement que la rotation seule n'explorait pas déjà. Le
moteur n'a par conséquent jamais besoin de retourner une pièce pour la caser,
et ne le fait jamais — `Placement.miroir` reste toujours `False` aujourd'hui.

La contrainte "pas de symétrie si gravure" est donc toujours respectée, mais
par construction plutôt que par un choix actif du moteur. Elle n'aura un
effet observable sur le nombre de feuilles/le placement que le jour où une
véritable imbrication polygonale (No-Fit-Polygon — les pièces glissées dans
les concavités les unes des autres) remplacera l'actuelle approche par
rectangles englobants : c'est un chantier nettement plus lourd, qu'on a
délibérément laissé de côté ici plutôt que de le lancer à la légère. Les
champs `symetrie_autorisee`/`miroir` sont conservés, prêts pour ce jour-là.

**Vérifié** : import d'un fichier DXF à 3 calques (découpe / gravure /
pliage) via un profil dédié — calques détectés et classés correctement,
aperçu SVG coloré par rôle, gravure non comptée comme trou, case "symétrie
autorisée" auto-décochée, matière/épaisseur/pas de rotation persistés ; nouvel
admin "Profils d'import" avec ses règles en inline. Tests : nouvelle suite sur
la classification par calque, le moteur d'imbrication à angles libres (le cas
du losange notamment) et le caractère volontairement sans-effet du miroir.

## Aperçu visuel de l'imbrication dans l'admin

`generer_svg_feuille()` (silhouette réelle des pièces placées sur une
feuille) existait déjà, mais n'était branché que sur un endpoint API brut
(`/api/v1/imbrications/<id>/apercu/<feuille>/`) — la fiche ImbricationJob de
l'admin n'affichait que des chiffres (nombre de feuilles, taux
d'utilisation), sans aucun moyen de voir le plan de découpe. Nouveau champ
"Aperçu des feuilles" sur la fiche : une image par feuille calculée, avec les
pièces à leur position, rotation et échelle réelles.

Les SVG générés portent des attributs `width`/`height` en millimètres
(pensés pour un export imprimable via l'API, à l'échelle 1:1) — beaucoup trop
grands affichés tels quels sur une page admin (une feuille 1000×1000mm
s'afficherait à ~3780px de large). L'aperçu admin les contraint à une largeur
d'écran raisonnable (480px max) sans toucher à la fonction partagée avec
l'API.

**Vérifié** : imbrication de 15 pièces sur une feuille 1000×1000mm — plan de
découpe affiché directement sur la fiche, à une taille d'affichage correcte,
avec le message "Aucune feuille calculée pour l'instant" avant tout calcul.

## Sens d'imbrication et coin de départ

Deux nouveaux réglages sur la fiche Imbrication :

- **Direction** : "Horizontal" (rangées remplies horizontalement, empilées
  verticalement — comportement historique, reste la valeur par défaut) ou
  "Vertical" (colonnes remplies verticalement, empilées horizontalement).
- **Coin départ** : le coin de la feuille où démarre le placement —
  "Bas gauche" (nouvelle valeur par défaut, convention machine la plus
  courante), "Bas droite", "Haut gauche" ou "Haut droite".

Techniquement, `decoupe/services/imbrication.py` calcule toujours en interne
dans un repère canonique (origine en haut à gauche, algorithme d'étagères
inchangé) ; `direction` présente juste la feuille et chaque pièce à
l'algorithme avec largeur/hauteur inversées en mode vertical ("une étagère"
devient alors une colonne), et `coin_depart` réfléchit les coordonnées
obtenues selon l'axe concerné une fois le placement calculé — deux réglages
indépendants, combinables librement.

`direction` n'est pas qu'un réétiquetage cosmétique : sur un lot de pièces de
tailles différentes, le sens de remplissage peut changer le nombre de
feuilles nécessaires (une pièce qui ne rentre pas à côté d'une autre en mode
"rangées" peut tenir à côté en mode "colonnes", ou inversement) — démontré
par un test dédié (deux pièces 120×60 et 60×120 : 2 feuilles en horizontal, 1
seule en vertical, sur une même feuille 200×150). En revanche, pour un lot
de pièces **toutes de la même taille**, la direction ne change jamais le
nombre de feuilles ni le taux d'utilisation (démontrable algébriquement :
`floor(A/a)×floor(B/b)` est symétrique en échangeant les rôles des deux
axes) — elle ne change alors que l'ordre/l'emplacement de remplissage.

**Vérifié** : deux pièces de tailles différentes sur une feuille 200×150 —
tiennent sur 1 seule feuille en mode vertical/bas gauche (visible sur la
capture, les deux pièces calées en bas de la feuille), contre 2 feuilles
nécessaires en horizontal/haut gauche pour le même lot.

## Imbrication en direct (sans enregistrer)

Jusqu'ici, voir le résultat d'un changement sur la fiche Imbrication
(dimensions de feuille, direction, coin de départ, lignes pièce/quantité)
demandait de cliquer sur "Enregistrer et continuer les modifications" —
`ImbricationJobAdmin.save_related()` ne recalculait qu'à l'enregistrement.

Nouvelle vue AJAX (`previsualiser_imbrication_view`,
`/admin/decoupe/imbricationjob/previsualiser/`) : reconstruit l'état courant
du formulaire (feuille, direction, coin de départ, chaque ligne
pièce/quantité du formset — y compris une pièce tout juste ajoutée ou une
ligne supprimée, sans persister quoi que ce soit) et appelle
`calculer_imbrication()` directement. `imbricationjob_admin.js` déclenche ce
recalcul (avec un anti-rebond de 400 ms) sur toute modification pertinente —
y compris sur le **formulaire d'ajout**, avant même la première
sauvegarde — et met à jour en direct l'aperçu des feuilles ainsi que les
chiffres (nombre de feuilles, surfaces, taux d'utilisation, coût matière
estimé, pièces non placées).

Comme pour PieceDecoupeAdmin, chaque chiffre readonly est enveloppé dans un
`<span id="...">` dédié plutôt qu'affiché tel quel (même contrainte Unfold,
déjà documentée : pas de classe `field-<nom>` sur les champs readonly de
premier niveau).

**Vérifié** : sur le formulaire d'ajout (jamais enregistré, aucun
`ImbricationJob` en base), sélection d'une pièce et saisie d'une quantité —
aperçu des feuilles et statistiques mis à jour en direct, sans passer par
"Enregistrer".

## Tableau des calques éditable à l'analyse d'un DXF/DWG

Jusqu'ici, le classement des calques (découpe / gravure / pliage / ignoré)
ne pouvait se faire qu'en amont, via un `ProfilImportDecoupe` préparé à
l'avance (voir plus haut). Il manquait un moyen de voir, au cas par cas et
sans rien préparer, la liste des calques présents dans le fichier qu'on
vient d'importer, et de corriger leur rôle directement à l'écran.

Nouveau champ `PieceDecoupe.regles_calques_manuelles` (JSON,
`{calque: rôle}`, prioritaire sur `profil_import` dans
`importer_geometrie()`) et tableau interactif affiché juste sous l'aperçu,
dès l'analyse terminée (avant même l'enregistrement) : une ligne par calque
détecté, avec un menu déroulant Découpe / Gravure marquage / Pliage / Ignoré
par calque. Choisir un rôle relance aussitôt l'analyse (même mécanisme AJAX
que le reste de la fiche) et met à jour aperçu, contours, surface et
détection de gravure en direct.

`ErreurImportGeometrie` porte désormais `calques`/`calques_roles` même en
cas d'échec (ex. plus aucun calque de découpe après une mauvaise
correction) : le tableau reste affiché et corrigeable sans devoir
ré-uploader le fichier. `analyser_fichier_view` accepte aussi un `piece_id`
en plus de `fichier_source` — nécessaire pour rejouer l'analyse sur une
pièce déjà enregistrée (changer un rôle de calque sur le formulaire de
modification ne repasse pas par le champ fichier). Construction du tableau
côté JS entièrement par API DOM (`textContent`/`dataset`, jamais
`innerHTML`) : les noms de calques viennent du fichier uploadé, donc non
fiables.

**Correctif découvert pendant la vérification visuelle** : à l'ouverture de
la fiche d'une pièce déjà enregistrée, l'initialisation du widget select2
du champ "Profil import" émettait elle-même un évènement `change` — sans
aucune action de l'utilisateur — ce qui relançait une analyse par défaut et
écrasait silencieusement le tableau de calques (et le classement
enregistré) au prochain "Enregistrer". Corrigé en ne réagissant plus qu'à
un changement réel de valeur du champ (comparaison à la dernière valeur
connue) plutôt qu'à tout évènement `change`.

**Vérifié** : import d'un DXF à 3 calques (COUPE/GRAVURE/PLIAGE, un logo
triangulaire sur GRAVURE pris pour un trou tant que le calque reste classé
"Découpe" par défaut) — tableau de 3 lignes affiché, correction
GRAVURE→Gravure et PLIAGE→Pliage : nombre de contours intérieurs passe de 1
à 0, gravure détectée passe à "Oui", aperçu SVG recoloré en direct.
Enregistrement puis réouverture de la fiche : classement et statistiques
bien persistés (confirmé aussi directement en base) — y compris après
correction du bug de ré-analyse fantôme ci-dessus, qui aurait sinon
silencieusement réinitialisé le classement à l'ouverture.

## Recette du module A (devis → commande) : priorité 1, intégrité

Issu de l'audit de recette (cahier de tests + analyse d'écart) : les défauts
« Must have » du module Devis/Commandes, corrigés dans l'ordre de gravité.
Chaque point est couvert par des tests dédiés (`ValidationDesSaisiesTests`,
`DevisValideVerrouilleTests`, `LancementEnProductionRobusteTests`,
`StatutCommandeTests` dans `chiffrage/tests.py`).

- **Saisies absurdes refusées** : quantité d'une ligne de devis strictement
  positive ; prix forcé, taux de marge (ligne, opération, devis), prix et
  quantité de ligne de commande jamais négatifs. Les validateurs sont sur les
  champs du modèle, donc appliqués partout : admin, recalcul AJAX et API.
- **Devis validé = devis verrouillé** : c'est le prix engagé auprès du client.
  Sur la fiche admin tous les champs passent en lecture seule (sauf le statut)
  et les lignes ne peuvent plus être ajoutées, modifiées ni supprimées ; même
  règle pour l'API (lignes, devis, recalcul) et pour le recalcul en direct
  (409). Pour modifier, repasser le devis en « Brouillon » — refusé dès
  qu'une commande en est issue. L'action « Recalculer le chiffrage » ignore
  les devis validés (sinon un changement de coût matière modifiait un prix
  déjà engagé).
- **Lancement en production robuste** : verrou de ligne sur le devis
  (`select_for_update`) et conversion de toute collision de numéro en erreur
  métier — un double-clic ne crée plus de commande en double ni d'erreur 500.
  La synchro planning (appel réseau) reste exécutée après le commit.
- **Statut de commande structuré** (`Commande.Statut`) : *En cours*, *Soldée*
  (déduit automatiquement quand toutes les lignes sont entièrement livrées),
  *Annulée*. Le statut n'est plus saisissable : l'annulation passe par
  l'action admin « Annuler la commande » ou `POST /api/v1/commandes/<n>/annuler/`,
  refusée dès qu'une livraison existe (il faudrait alors un retour client et
  un avoir) ; aucune livraison n'est possible sur une commande annulée. Les
  OF déjà lancés ne sont pas arrêtés automatiquement : l'action le rappelle.
  La migration `0015` reprend les anciens statuts texte libre (soldée si tout
  est livré, sinon en cours).

**Vérifié** (Playwright) : fiche d'un devis validé — seul le statut reste
éditable, lignes en lecture seule sans bouton d'ajout ; commande annulée
visible en liste, statut non éditable sur sa fiche.

Bug trouvé en écrivant les tests : après un POST invalide, Django réécrit
l'instance avec le statut soumis, ce qui désactivait le verrou en plein rendu
de la page d'erreur (KeyError) — le verrou relit donc le statut en base.

## Recette du module A : priorité 2, traçabilité, arrondis, contrôle à la validation

- **Piste d'audit** (`django-simple-history`) sur les devis, leurs lignes et les
  commandes : qui, quand, et ancienne → nouvelle valeur de chaque champ, y
  compris les suppressions de ligne. Consultable via le bouton « Historique »
  de chaque fiche. Elle est en **lecture seule** : la restauration d'une
  version précédente est désactivée (elle aurait contourné le verrou du devis
  validé). Au démarrage, `populate_history --auto` crée le point de départ des
  enregistrements antérieurs (idempotent). Les lignes de commande gardent en
  plus leur journal dédié `CommandeLigneModification`.
- **Arrondis au centime** : prix TTC d'une ligne, montants HT/TTC d'une ligne
  de commande, totaux d'un devis et montants indicatifs de facture. Le total
  TTC est la somme des lignes arrondies (comme sur la facture Tiime) et ne
  laisse plus de résidu flottant (`0.1 + 0.2` s'affiche `0.3`). Les prix
  unitaires gardent leur précision ; le passage des champs en `Decimal` reste
  un chantier à part (migration lourde).
- **Contrôle à la validation d'un devis** (`chiffrage/validation.py`) : le
  chiffrage est recalculé, puis la validation est refusée — le devis reste en
  brouillon avec un message par raison — si le devis est vide, si une ligne
  n'est pas chiffrable, ou si une ligne est **vendue sous son coût** sans la
  permission dédiée. Même contrôle côté API (transaction annulée en cas de
  refus).
- **Permissions métier** : `valider_devis` (sans elle, le champ statut
  disparaît du formulaire et l'API répond 403), `valider_vente_sous_cout`,
  `annuler_commande`. À attribuer par groupe dans l'admin (Utilisateurs →
  Groupes) ; un superutilisateur les a toutes.

**Vérifié** (Playwright) : un utilisateur habilité à valider mais pas à vendre
sous le coût tente de valider un devis dont la ligne est à 10 € HT pour un coût
de 50 € — refus affiché, devis resté en brouillon ; page d'historique du devis
avec auteur et changement de statut.

Bugs trouvés par les tests de cette étape : `FullCleanModelSerializer` applique
les valeurs soumises sur l'instance avant toute vérification, ce qui rendait la
validation d'un devis par l'API impossible (verrou déclenché à tort) et
permettait de déplacer une ligne hors d'un devis validé ; les contrôles lisent
désormais l'état d'origine en base. Le retour en brouillon après un refus passe
par `save()` avec motif, pour que l'historique ne mente pas.

## Recette du module A : priorité 3, rôles métier et API sous permissions

- **Quatre groupes prédéfinis**, créés au premier `migrate` (jamais réécrits
  ensuite : leurs permissions restent ajustables dans Utilisateurs → Groupes) :

  | Groupe | Peut |
  |---|---|
  | Commercial | créer et modifier devis, lignes et commandes ; consulter les livraisons. Ne valide pas, n'annule pas, ne supprime pas un devis. |
  | Responsable commercial | + valider un devis, annuler une commande, supprimer un devis, saisir les livraisons. |
  | Direction | + valider un devis **vendu sous le coût**. |
  | Atelier | consulter les commandes ; consulter et modifier les ordres de fabrication. Aucun accès aux devis ni aux prix de revient. |

  Un compte sans groupe n'a accès à rien : à rattacher à un groupe à la
  création de l'utilisateur.
- **API du module sous permissions** (`comptes.permissions.ModelPermissionsAvecLecture`) :
  avant, tout compte connecté lisait et écrivait tout via `/api/v1/` alors que
  l'admin le lui interdisait. Désormais lecture = permission « voir », écriture =
  ajout / modification / suppression ; les actions spéciales (valider,
  recalculer, lancer en production, annuler, resynchroniser) vérifient leur
  permission propre.

**Reste à faire hors module A** : les autres modules (stock, facturation,
achats, comptabilité, pilotage) exposent toujours leur API en
`IsAuthenticated` seul — même correction à y appliquer — et le contrôle
`DEBUG`/`SECRET_KEY` au démarrage en production.

## Recette du module B (stock) : priorité 1, intégrité du journal

Constats de l'audit corrigés (tests : `SortieSuperieureAuDisponibleTests`,
`JournalImmuableTests`, `TracabiliteMouvementTests`, `ContrePassationTests`) :

- **Journal immuable** : un mouvement ne se modifie ni ne se supprime (modèle,
  requêtes groupées, admin, API). Avant, l'édition ou la suppression d'un
  mouvement laissait le solde du lot inchangé, donc faux. Le solde d'un lot
  est désormais toujours égal à la somme de ses mouvements.
- **Correction par contre-passation** : action « Annuler le mouvement » (page
  de confirmation avec motif obligatoire) ou `POST /api/v1/mouvements-stock/<id>/annuler/`.
  Crée le mouvement inverse, lié à l'original ; refusé si déjà annulé, si c'est
  lui-même une annulation, ou si l'inversion rendrait le stock négatif. Une
  alerte prévient quand le mouvement vient d'un document (réception, livraison) :
  ce document n'est pas corrigé automatiquement. Permission `annuler_mouvement`.
- **Plus de sortie au-delà du disponible** : refus à la validation du formulaire
  et à l'enregistrement, sous verrou de ligne sur le lot (deux sorties
  simultanées ne passent plus toutes les deux). Une livraison qui dépasserait le
  stock est annulée en bloc avec un message demandant de régulariser le stock.
- **Auteur et horodatage** sur chaque mouvement ; **motif obligatoire** pour
  un mouvement saisi à la main (sans référence d'origine).
- **Fin des résidus flottants** : quantités conservées à 6 décimales
  (`0.1 + 0.2 - 0.3` donne exactement 0 et ne fausse plus les seuils d'alerte).
- Défaut latent corrigé : `date_mouvement` avait pour défaut un *datetime* sur un
  champ date, ce qui faisait échouer la réponse de l'API pour un mouvement créé
  sans date.

Correction de l'audit : `Lot.quantite` était déjà en lecture seule (admin et
API) ; le vrai risque venait de l'édition des mouvements, traitée ci-dessus.
La saisie d'un mouvement depuis la fiche d'un lot reste possible (ajout
seulement). Les lignes d'un journal existant n'ont pas d'auteur ni d'heure.

**Vérifié** (Playwright) : journal lisible (colonnes Article/Emplacement),
page de confirmation d'annulation, annulation d'une entrée de 12 (lot 44 → 32)
avec le mouvement d'origine conservé et la contre-passation tracée.

## Recette du module B (stock) : priorité 2, fonctions manquantes du flux

Tests : `TransfertTests`, `InventaireTests`, `ValorisationTests`,
`VerrouLotEtHistoriqueTests` (`stock/tests.py`).

- **Transferts** (menu Stock → Transferts de stock, API `/api/v1/transferts-stock/`) :
  déplacent une quantité d'un lot vers un autre emplacement en une opération
  atomique — une sortie et une entrée liées par la même référence
  `TRANSFERT-n`, lot cible créé au besoin, valorisation conservée. Refusé si
  le stock source ne suffit pas ou si l'emplacement est identique. Immuables :
  on défait un transfert par un transfert inverse.
- **Inventaires** (Stock → Inventaires, API `/api/v1/inventaires/`) : on saisit
  la quantité comptée par lot (action « Ajouter tous les lots en stock » pour
  pré-remplir), puis « Valider l'inventaire » (permission `valider_inventaire`)
  transforme chaque écart en mouvement d'ajustement tracé (`INVENTAIRE-n`,
  motif « Écart d'inventaire »), jamais en réécriture du solde. Un inventaire
  validé est figé (lignes, suppression) ; il garde la quantité théorique et
  l'écart de chaque ligne.
- **Valorisation au coût moyen pondéré** : chaque entrée peut porter un coût
  unitaire (les réceptions fournisseur reprennent le prix d'achat de la ligne
  de commande) ; le lot en déduit son coût moyen et sa valeur (colonnes de la
  liste des lots). Les sorties ne changent pas le coût moyen.
  **Attention** : le stock déjà présent *sans* coût compte pour 0 dans la
  moyenne — valorisez le stock initial (entrée avec coût, ou inventaire) pour
  des valeurs exactes. Les lots existants démarrent à un coût moyen de 0.
- **Livraison sur plusieurs lots en FIFO** : la sortie consomme les lots du plus
  ancien au plus récent (avant : « plusieurs lots » bloquait la livraison).
  Si le stock total ne suffit pas, toute la ligne est annulée, sans sortie
  partielle. La **réception** fournisseur reste, elle, limitée à un lot par
  article (choix du lot de destination : voir module C/achats).
- **Lot : article et emplacement figés** dès qu'il a un mouvement (formulaire,
  API et modèle) ; pour déplacer, utiliser un transfert. **Historique** en
  lecture seule sur les lots et les emplacements.

**Vérifié** (Playwright) : liste des lots avec coût moyen et valeur (52 × 1,25 =
65 €) ; inventaire brouillon puis validé (compté 49, théorique 52, écart −3,
sortie d'ajustement créée, formulaire figé).

## Recette du module B (stock) : priorité 3, rôles et API sous permissions

- **Deux nouveaux rôles** : *Magasinier* (consulte, saisit mouvements, lots,
  transferts et inventaires en brouillon ; ne corrige pas un mouvement ni ne
  valide un inventaire) et *Responsable stock* (+ annuler un mouvement, valider
  un inventaire, gérer les emplacements). *Atelier* peut consulter les lots,
  *Direction* consulter le stock.
- **API du stock sous permissions** (lecture incluse), comme pour le module A :
  avant, tout compte connecté lisait et écrivait lots et mouvements.
- **Les définitions de groupes sont centralisées** dans `comptes/groupes.py`
  (permissions inter-applications). Un groupe déjà créé n'est jamais réécrit ;
  pour lui ajouter les permissions apparues depuis dans sa définition par défaut :
  `python manage.py synchroniser_groupes` (ajoute seulement, ne retire jamais).

**Correction du module A (priorité 3)** : les groupes *Commercial*, *Responsable
commercial* et *Atelier* n'avaient pas la permission « voir » des modèles liés
(client, adresse, contact, article, poste…). Or les listes à autocomplétion de
l'admin répondent 403 sans elle : un commercial ne pouvait pas choisir un
client sur un devis. C'est corrigé, et un test parcourt désormais les
autocomplétions de chaque rôle. Si ces groupes existent déjà sur votre
installation, lancez `python manage.py synchroniser_groupes` après la mise à jour
(à ajouter à votre routine de mise à jour si vous ajustez souvent les rôles).
Autre défaut corrigé au passage : `comptes` n'ayant pas de modèle, Django
n'émettait jamais son signal `post_migrate` ; la création des groupes est
désormais branchée sur celui d'`auth`.

## Recette du module C (livraison et facturation) : priorité 1, intégrité

Tests : `CommandeDirecteTests`, `VerrouFactureTests`, `ValidationFactureTests`,
`MigrationStatutPaiementTests` (`facturation/tests.py`), `AnnulationLivraisonTests`
(`chiffrage/tests.py`).

- **Facture émise = facture verrouillée.** Dès que la référence Tiime est
  renseignée (ou qu'une écriture comptable existe), montants, date, commande,
  référence et mode de création ne se modifient plus (admin, API, modèle) et la
  facture ne se supprime plus ; seul le suivi du paiement reste libre. Une erreur
  se corrige par un avoir (priorité 2). Avant l'émission, tout reste libre, y
  compris corriger les montants dans la même saisie que la référence.
- **Statut de paiement structuré** (*À payer*, *Partiellement payée*, *Payée*)
  avec date de paiement posée automatiquement au passage à « Payée » ; la
  migration `0003` ramène les anciens textes libres à ces trois valeurs.
- **Contrôles de saisie** : facture antérieure à la commande ou datée dans le
  futur, TTC inférieur au HT, montants négatifs, paiement antérieur à la facture ;
  montants arrondis au centime ; une facture existante n'est jamais remplacée
  par un POST d'API de même numéro. Historique en lecture seule.
- **Défaut corrigé : commande sans devis.** L'échéance d'une facture et la
  génération d'écriture comptable lisaient `commande.devis.client` et plantaient
  pour une commande créée directement ; elles utilisent `commande.client`.
- **Livraison immuable et annulable.** Les lignes d'une livraison ne se modifient
  ni ne se suppriment plus (avant : modifier une quantité ou supprimer une
  livraison laissait cumul livré et stock faux). L'action « Annuler la livraison »
  (motif obligatoire, permission `annuler_livraison`) rétablit le cumul livré,
  contre-passe les sorties de stock (y compris sur plusieurs lots) et rouvre la
  commande ; la livraison reste consultable avec auteur, date et motif.
  Refusée si déjà annulée ou si la commande est déjà facturée (avoir d'abord).

**Vérifié** (Playwright) : fiche d'une facture émise — seul le paiement est
éditable ; page de confirmation d'annulation d'une livraison.

## Recette du module C : priorité 2, lignes de facture, avoirs, cohérence livraison/facture

Tests : `LignesDeFactureTests`, `AvoirsTests`, `PreparerFactureTests`,
`EcrituresFactureAvoirTests` (`facturation/tests.py`).

- **Lignes de facture** (`FactureLigne`) : ce qu'une facture facture réellement
  (quantité d'une ligne de commande, prix et TVA figés au moment de la
  facturation). Elles donnent, par ligne de commande, le **déjà facturé** (net
  d'avoirs, brouillons compris) et le **livré non facturé**, désormais affichés sur
  la commande. On ne peut pas facturer plus que le **livré** (ni deux fois la même
  quantité d'une facture à l'autre) ; la case « facturation anticipée »
  (permission `facturer_avant_livraison`) lève cette limite jusqu'au commandé,
  pour un acompte ou une facturation à la commande. Les lignes se figent avec la
  facture émise ; les montants HT/TTC de la facture sont repris des lignes quand
  ils sont laissés vides (jamais écrasés s'ils sont saisis : Tiime fait foi).
- **Préparer une facture** (bouton sur la liste des factures, ou
  `POST /api/v1/factures/preparer/`) : crée une facture brouillon avec le livré non
  encore facturé d'une commande, numérotée par la règle de codification
  « Facture » (repli `FAC-<commande>`). Reste à renseigner la référence Tiime une
  fois la facture émise.
- **Avoirs** : type de document *Avoir*, lié à sa facture d'origine, motif
  obligatoire, montants négatifs, quantités limitées à ce que la facture portait
  (net des avoirs déjà faits), pas d'avoir sur un avoir. Bouton « Créer un avoir »
  sur la fiche d'une facture émise (permission `creer_avoir`, API
  `POST /api/v1/factures/<n>/avoir/`) : crédite le solde restant ; un avoir
  partiel se saisit à la main. Un avoir rouvre le « reste à facturer » et permet
  d'annuler la livraison correspondante.
- **Écritures comptables fondées sur la facture** : une facture partielle ne
  comptabilise plus toute la commande, deux factures d'une même commande ont
  chacune leur écriture juste, et un avoir génère l'écriture inverse (Clients au
  crédit, Ventes et TVA au débit). Les factures sans lignes gardent l'ancien
  calcul.
- **Annulation de livraison** : contrôle précis (facturé net par ligne contre ce
  qui resterait livré) au lieu du refus global de la priorité 1.

**À savoir à la mise à jour** : les factures *antérieures* n'ont pas de lignes et
ne comptent donc pas dans le « déjà facturé » — l'écran « Préparer une facture »
leur proposerait de refacturer le livré. Pour chaque facture existante, ajoutez
ses lignes dans l'admin (quantités effectivement facturées) avant d'en préparer
de nouvelles sur la même commande.

**Vérifié** (Playwright) : page « Préparer une facture » (commande livrée 8 sur 20,
non facturée) ; facture préparée avec sa ligne (8 × 12,50 = 100 € HT, 120 € TTC)
et les boutons « Créer un avoir » / « Historique ».

## Recette du module C : priorité 3, impayés, écarts, rôles

Tests : `RetardsEtEcartsTests`, `RolesFacturationTests` (`facturation/tests.py`),
`CodesDesGroupesTests`, `ConnexionDirecteTests` (`comptes/tests.py`).

- **Impayés visibles** : une facture (jamais un avoir) non soldée dont
  l'échéance calculée est dépassée est « en retard » — mention dans la colonne
  Échéance, filtre « En retard de paiement » sur la liste, et tuile *Factures en
  retard* du tableau de bord (nombre et montant TTC à relancer). Le « CA
  facturé » du mois est désormais net d'avoirs. Les relances automatiques
  (envoi d'e-mails) ne sont pas incluses : il n'y a pas d'infrastructure d'envoi.
- **Écart avec les lignes** : colonne de la liste des factures qui signale la
  différence entre le montant HT saisi (Tiime) et le total des lignes (remise,
  erreur de recopie).
- **Rôles** : *Facturation* (prépare et saisit les factures, consulte la
  comptabilité) et *Responsable facturation* (+ avoirs, facturation anticipée,
  génération des écritures comptables). *Responsable commercial* peut annuler une
  livraison. L'action « Générer l'écriture comptable » exige désormais la
  permission d'ajouter une écriture. API des factures et de leurs lignes soumise
  aux permissions du modèle.
- **Après la mise à jour** : `python manage.py synchroniser_groupes` pour ajouter
  les nouvelles permissions aux groupes déjà créés (ajout seulement). Un test
  vérifie désormais que chaque code de permission des groupes existe : une faute
  de frappe serait sinon ignorée en silence (une avait été commise puis détectée
  ici sur `LigneEcriture`).
- **Connexion directe** sur `/admin/login/` sans paramètre `next` : retour à
  l'accueil de l'admin au lieu d'une page 404 (`/accounts/profile/`).

**Vérifié** (Playwright) : liste des factures (échéance « en retard », colonne
d'écart, bouton « Préparer une facture ») et tableau de bord (1 facture en retard,
120 € TTC à relancer, CA net d'avoirs).

## Recette du module D (sécurité transverse) : priorité 1

Tests : `ApiSecuriseeParDefautTests`, `MediaProtegeTests`, `DiagnosticSecuriteTests`,
`LimitationConnexionTests`, `GroupesMetierTests` (`comptes/tests.py`).

- **API sécurisée par défaut.** Toute route d'API exige désormais la permission du modèle
  (lecture = « voir », écriture = ajout/modification/suppression) : c'est le réglage par
  défaut de DRF (`ModelPermissionsAvecLecture`), donc un futur ViewSet est protégé sans rien
  ajouter. Avant, technique, commercial, comptabilité, achats, sous-traitance et découpe
  n'exigeaient qu'un compte connecté. Un test parcourt **toutes** les routes enregistrées.
  Les marges réelles et taux de charge (pilotage) exigent la nouvelle permission
  `voir_marges` (rôle *Direction*).
- **Nouveaux rôles** pour les modules sans rôle jusque-là : *Méthodes et bureau d'études*
  (articles, matières, gammes, postes, découpe), *Achats* (achats, sous-traitance),
  *Comptabilité*. Le rôle *Commercial* peut maintenant créer et modifier ses clients,
  adresses et contacts (il ne pouvait que les consulter). Un test vérifie que les listes à
  autocomplétion de chaque rôle répondent.
- **Fichiers `/media/` protégés.** Les plans DXF/DWG des clients étaient lisibles par
  quiconque connaissait l'URL, sans connexion. Ils exigent maintenant un compte connecté ayant
  le droit de voir les pièces à découper (autres fichiers : compte du personnel).
- **Configuration de production.** `DEBUG` vaut `False` si la variable est absente (avant :
  `True`). Un diagnostic (`manage.py verifier_securite`, lancé au démarrage du conteneur,
  et **bandeau rouge sur l'accueil de l'admin, visible des seuls superutilisateurs**) signale :
  mode DEBUG, clé secrète par défaut ou trop courte, `ALLOWED_HOSTS` ouvert, mot de passe
  de base par défaut, cookies non sécurisés. Rien ne bloque le démarrage : un réglage douteux
  est signalé, jamais cause de panne. Nouveaux réglages `.env` : `DJANGO_SESSION_HEURES`
  (12 par défaut), `DJANGO_COOKIES_SECURISES`, `DJANGO_HSTS_SECONDS` (à activer seulement
  une fois l'ERP servi en HTTPS). Mots de passe : 10 caractères minimum.
- **Connexion : anti force brute et journal.** Après 5 échecs en 15 minutes sur un même
  identifiant, la connexion est refusée — même avec le bon mot de passe — jusqu'à ce que les
  échecs sortent de la fenêtre ; une connexion réussie remet le compteur à zéro, un
  administrateur peut débloquer (permission `debloquer_compte`). Le compteur est par
  identifiant, pas par IP : derrière le reverse proxy du NAS toutes les requêtes ont la même
  adresse et un blocage par IP verrouillerait tout le monde. S'applique aussi à l'API en
  authentification de base. Le *Journal des connexions* (Paramétrage) trace réussites,
  échecs, refus et déblocages, en consultation seule, conservé 180 jours.
- Corrigé au passage : `LOGIN_URL` n'était pas défini (la redirection vers la connexion
  pointait sur `/accounts/login/`, inexistant).

**Vérifié** (Playwright) : 5 échecs puis bon mot de passe → refusé avec le message
« compte verrouillé, réessayez dans 15 minute(s) » ; bandeau de sécurité sur l'accueil ;
journal des connexions.

## Recette du module D (sécurité transverse) : priorité 2

Tests : `GestionDesAccesTests`, `AuditDesDroitsTests`, `VerrouOptimisteTests`
(`comptes/tests.py`).

- **Faille corrigée : élévation de privilèges.** Un compte avec la seule permission « modifier
  les utilisateurs » pouvait se cocher « superutilisateur » ou s'ajouter à n'importe quel
  groupe. Utilisateurs et groupes sont désormais réservés aux **superutilisateurs** : qui gère
  les accès a, de fait, tous les accès. À noter : seuls les superutilisateurs créent des
  comptes et les rattachent à un groupe.
- **Audit des droits** (Paramétrage → *Audit des droits*, ou `manage.py audit_droits`) : qui
  a accès à quoi, comptes sans groupe, jamais connectés ou inactifs depuis 90 jours, nombre de
  superutilisateurs, et **cumuls de droits incompatibles** (séparation des pouvoirs) — par
  exemple saisir une facture *et* la comptabiliser, ou saisir un mouvement de stock *et*
  valider l'inventaire. Rien n'est bloqué : une petite structure cumule souvent les rôles, ici
  le cumul devient visible pour être décidé. Les rôles *Responsable facturation* et
  *Responsable stock* cumulent ainsi volontairement certains droits et apparaissent comme tels.
- **Plus d'écrasement silencieux entre deux onglets ou deux personnes.** Les fiches des devis,
  commandes, livraisons, factures, lots et emplacements portent un numéro de version (dernière
  entrée de l'historique, champ caché). Si la fiche a changé depuis l'ouverture de la page,
  l'enregistrement est refusé avec le nom de l'auteur et l'heure, sans rien perdre : la saisie
  reste affichée pour être recopiée. Limite connue : seule la fiche parente est versionnée,
  pas ses lignes en tableau (le recalcul en direct des lignes de devis les enregistre au fil de
  la saisie). Les fiches sans historique (tiers, articles…) ne sont pas encore couvertes.
- **Double-clic sur « Enregistrer »** : un seul envoi part. Les envois suivants sont bloqués
  plutôt que de désactiver le bouton (un bouton désactivé n'est pas transmis, et le serveur
  perdrait la distinction « Enregistrer » / « Enregistrer et continuer »).
- **Départ d'un collaborateur** : désactiver le compte coupe immédiatement sa session ; changer
  son mot de passe invalide ses autres sessions ; la désactivation est tracée dans
  l'historique de l'utilisateur. Les sessions durent 12 h (`DJANGO_SESSION_HEURES`).

**Vérifié** (Playwright) : deux contextes de navigateur sur la même fiche — le second
enregistrement est refusé avec « modifiée par bruno-d le … » ; un double-clic envoie une seule
requête POST.

## Recette du module D (sécurité transverse) : priorité 3, sauvegarde et dépendances

- **Dépendances** : `pip-audit` sur `requirements.txt` ne signale aucune vulnérabilité connue.
- **Sauvegarde et restauration** (les données n'étaient sauvegardées nulle part) :
  - `./sauvegarder-nas.sh [dossier] [nombre]` : dump compressé de la base + archive des
    fichiers déposés (plans DXF/DWG), vérifie leur intégrité, refuse une sauvegarde vide,
    garde les 14 dernières (dossier par défaut : `/volume1/docker/erp_sauvegardes`). À lancer
    à la main, ou chaque nuit via le Planificateur de tâches de DSM (script défini par
    l'utilisateur : `cd /volume1/docker/erp && ./sauvegarder-nas.sh`). **Copiez ce dossier hors
    du NAS** : une sauvegarde qui ne vit que sur le NAS ne protège pas d'une panne du NAS.
  - `./restaurer-nas.sh fichier_base.sql.gz [fichiers.tar.gz]` : remplace la base par la
    sauvegarde, après confirmation (taper `RESTAURER`) et sauvegarde de sécurité de l'état
    actuel dans `erp_sauvegardes/avant_restauration`.
  - `update-nas.sh` lance désormais la sauvegarde avant chaque mise à jour (effective à partir
    de la mise à jour *suivante*) ; en cas d'échec il demande confirmation. Passer outre :
    `ERP_UPDATE_SANS_SAUVEGARDE=1 ./update-nas.sh ...`.
  - Testé de bout en bout (avec un faux `docker` branché sur une base locale) : sauvegarde,
    rotation, destruction volontaire de données, restauration. Ce test a révélé un défaut du
    premier jet (un `ls` final en échec faisait avorter la restauration), corrigé.

**Hors périmètre, à connaître** : pas d'authentification à deux facteurs (django-otp, chantier
à part) ; le conteneur tourne en root (le passer en utilisateur dédié demande d'adapter les
droits du volume des fichiers déposés) ; pas de CSP (le thème de l'admin utilise des scripts
en ligne) ; l'API accepte toujours l'authentification de base (désormais protégée par la
limitation des tentatives) ; les fiches sans historique (tiers, articles…) ne sont pas
couvertes par le verrouillage optimiste.

## Gap analysis, lot 1 : cycle de vie du devis

Tests : `CycleDeVieDevisTests` (`chiffrage/tests.py`).

- **Validité de l'offre** : à la validation, le devis reçoit une date « valable jusqu'au » (30
  jours, réglable par `DEVIS_VALIDITE_JOURS`). Une offre expirée ne peut plus devenir une
  commande tant que sa validité n'est pas prolongée ; repasser un devis en brouillon efface
  l'échéance. Les devis existants ne reçoivent aucune date (pas d'expiration inventée).
- **Réponse du client** : *en attente*, *accepté* (posé par la création de la commande, tracé
  dans l'historique), *refusé* (motif obligatoire), *remplacé* (par une révision). Elles se
  renseignent sur la fiche d'un devis validé **sans déverrouiller le prix** : seuls la réponse,
  le motif et la date de validité y restent modifiables. Un devis refusé ou remplacé ne peut
  pas devenir une commande. La migration marque « accepté » les devis déjà transformés.
- **Révision** (bouton *Réviser ce devis* sur la fiche) : crée un brouillon `…-R2` (puis `-R3`…)
  avec le même en-tête et les mêmes lignes, et marque l'original « remplacé » : le prix déjà
  envoyé n'est jamais réécrit. Refusée pour un brouillon, un devis déjà commandé ou déjà
  remplacé.
- **Pilotage** : colonnes *réponse* et *valable jusqu'au*, filtres *Expirés* et *Expirent sous 7
  jours*, tuiles d'accueil *Devis sans réponse* (et combien à relancer) et *Taux de
  transformation* (devis acceptés sur envoyés, 90 jours, hors remplacés).

**Vérifié** (Playwright) : liste (statuts de réponse et dates), fiche d'un devis validé
(réponse et validité modifiables, reste verrouillé, bouton *Réviser*), révision en un clic
(`DEV-2026-101-R2`), tuiles d'accueil.

## Gap analysis, lot 2 : documents PDF (devis, bon de livraison) et fiche Société

Tests : `DocumentsPdfTests` (`chiffrage/tests.py`). Nouvelles dépendances : `reportlab`, `pillow`
(auditées, aucune vulnérabilité connue).

- **Fiche Société** (Paramétrage → *Société*) : raison sociale, adresse, contacts, SIRET, TVA
  intracommunautaire, capital, RCS, IBAN/BIC, **logo**, et les mentions imprimées sur les devis
  (conditions de vente, pénalités de retard…) et les bons de livraison. Une seule fiche. **À
  remplir avant d'imprimer le premier document.**
- **Devis en PDF** (bouton *PDF du devis* sur la fiche, ou `GET /api/v1/devis/<n>/pdf/`) :
  en-tête société, client et adresses, lignes avec prix unitaire HT, TVA par taux, totaux HT /
  TVA / TTC, délai, conditions de règlement du client, date de validité, mention « Annule et
  remplace » pour une révision, cadre « Bon pour accord » à signer. Un devis non validé est
  marqué **PROVISOIRE** en filigrane ; un devis vide ou dont une ligne n'est pas chiffrée n'est
  pas imprimable (message explicite : un prix absent n'est pas un prix de 0).
- **Bon de livraison en PDF** (bouton *Bon de livraison (PDF)* sur la fiche d'une livraison) :
  adresses de livraison et de facturation, référence de commande client, quantités livrée /
  commandée / **reliquat**, colonne *N° de coulée* (alimentée au lot suivant), cadre de
  signature du destinataire ; filigrane **ANNULÉ** si la livraison l'est.
- La **facture** n'est pas concernée : la facture légale est émise par Tiime.
- Les textes saisis par les utilisateurs sont échappés avant d'entrer dans le PDF (pas de
  balisage injecté). Le téléchargement exige la permission « voir » du devis / de la livraison.

**Vérifié** (rendu des PDF en images) : devis avec logo, en-tête et pied de page légaux, totaux ;
bon de livraison avec reliquat et cadre de signature.

## Gap analysis, lot 3 : traçabilité matière et réception sur plusieurs lots

Tests : `ReceptionTracabiliteTests`, `TracabiliteLivraisonTests` (`achats/tests.py`),
`AutocompletionsTracabiliteTests` (`comptes/tests.py`).

- **N° de coulée et certificat 3.1 sur le lot** : deux champs sur chaque lot, recherche par
  n° de coulée dans la liste des lots. Les certificats sont des fichiers `/media/stock/…`,
  lisibles seulement avec la permission « voir un lot ».
- **Réception sur plusieurs lots** (avant : un seul lot par article, sinon la réception était
  refusée) : chaque ligne de réception peut désigner le lot de destination, ou donner un **n° de
  coulée** (et un emplacement) — une coulée déjà connue complète son lot, une nouvelle coulée
  **crée** son lot, et le certificat joint est rattaché au lot. Sans rien préciser, l'ancien
  comportement subsiste (lot unique de l'article).
- **Livrer une coulée précise** : champ *Lot livré* sur la ligne de livraison (pour que le
  certificat remis corresponde à la matière expédiée) ; sans choix, les lots sont toujours
  consommés du plus ancien au plus récent. Un lot d'un autre article est refusé.
- **Traçabilité d'un lot** (bouton sur la fiche d'un lot) : d'où il vient (réceptions et
  fournisseurs) et où il est parti (bons de livraison, clients, commandes, livraisons annulées
  signalées) — de quoi répondre à un rappel de matière ou à une réclamation. Le **n° de coulée
  s'imprime sur le bon de livraison**.
- **Défaut corrigé** : l'enregistrement d'une ligne de réception n'était pas atomique et
  mettait à jour le cumul reçu avant de chercher le lot. Si aucun lot n'existait (ou s'il y en
  avait plusieurs), l'erreur était affichée mais la ligne et le cumul restaient enregistrés.
  Désormais tout ou rien. Le rôle *Responsable commercial* reçoit la consultation des lots
  (choix du lot livré) : `manage.py synchroniser_groupes` après mise à jour.

**Vérifié** (Playwright) : page de traçabilité du lot C-2026-0455 (réception REC-0099 des Aciers
du Rhône, livraison BL-0208 chez Métallerie Durand) ; bon de livraison imprimant la coulée.

## Gap analysis, lot 4 : relances de paiement par e-mail et synthèse quotidienne

**Relances de paiement.** Sur la liste des factures, sélectionnez les factures en retard puis l'action « Envoyer une relance de paiement » (droit `relancer_facture`, donné aux groupes Facturation, Responsable facturation et Direction). Le message part au contact du client dont la fonction contient compta/factur/financ, sinon au contact principal, sinon au premier contact avec une adresse. Trois niveaux de ton (courtois, ferme, mise en demeure) selon le nombre de relances déjà envoyées ; l'IBAN et les coordonnées de la fiche Société sont ajoutés. Refus explicites : avoir, facture payée, facture non émise, facture pas encore en retard, relance déjà envoyée il y a moins de `DJANGO_RELANCE_DELAI_MIN_JOURS` jours (7 par défaut), client sans adresse e-mail. Un échec d'envoi est journalisé (`envoyee = non`, motif) et ne compte pas comme une relance. L'historique est visible sur la fiche facture (onglet « Relances de paiement », lecture seule) et la colonne « Relances » de la liste.

**Synthèse quotidienne.** `python manage.py synthese_quotidienne` envoie un e-mail récapitulatif : factures en retard, devis expirés ou expirant sous 7 jours. Options : `--afficher` (affiche sans envoyer), `--destinataires a@x.fr,b@x.fr`, `--si-non-vide` (n'envoie rien s'il n'y a rien à signaler). Destinataires par défaut : `DJANGO_SYNTHESE_DESTINATAIRES`.

**Configuration e-mail** (fichier `.env`) : `DJANGO_EMAIL_HOST`, `DJANGO_EMAIL_PORT`, `DJANGO_EMAIL_USER`, `DJANGO_EMAIL_PASSWORD`, `DJANGO_EMAIL_TLS`, `DJANGO_EMAIL_EXPEDITEUR`. Sans `DJANGO_EMAIL_HOST`, les messages s'affichent seulement dans les logs (mode test, rien n'est envoyé).

Sur le NAS : après mise à jour, lancer `sudo docker compose exec web python manage.py synchroniser_groupes`, puis programmer dans le Planificateur de tâches DSM, chaque matin :
`cd /volume1/docker/erp && docker compose exec -T web python manage.py synthese_quotidienne --si-non-vide`.

## Gap analysis, lot 5 : fiabilisation de la synchronisation avec le planning atelier

Le planning reste facultatif (tant que `PLANNING_API_URL` est vide, rien n'est envoyé et rien n'est signalé comme anormal). Une fois l'API branchée :

- **Clé d'idempotence liée au contenu** : `Idempotency-Key = <numéro OF>-<empreinte du contenu envoyé>`. Un renvoi après coupure réseau ne crée pas de doublon côté planning ; un OF modifié part avec une nouvelle clé et met donc le planning à jour.
- **OF modifié après synchronisation** (quantité, date, gamme) : détecté par comparaison d'empreinte et renvoyé automatiquement par la reprise planifiée. Les OF synchronisés avant cette version ne sont pas renvoyés tant qu'ils ne changent pas.
- **Reprise avec attente croissante** : 5 min, 10, 20… plafonnée à 6 h entre deux tentatives ; après `PLANNING_SYNC_MAX_TENTATIVES` échecs (5 par défaut) l'OF passe en « Échec persistant ».
- **Dernière erreur conservée** sur chaque OF (colonne « Dernière erreur » de la liste, champ en lecture seule dans la fiche et l'API). Les champs de synchronisation ne sont plus modifiables à la main.
- **Tuile « Synchro planning »** sur le tableau de bord (rouge s'il y a des échecs persistants, lien direct vers la liste filtrée) et rubrique dédiée dans la synthèse quotidienne.
- `manage.py retry_sync_ordres_fabrication` : respecte le délai de reprise, renvoie les OF modifiés, verrouille chaque OF (deux exécutions simultanées ne l'envoient pas deux fois). Option `--inclure-echecs` pour retenter les échecs persistants après avoir corrigé la cause.

À planifier dans le Planificateur de tâches DSM, toutes les 15 minutes :
`cd /volume1/docker/erp && docker compose exec -T web python manage.py retry_sync_ordres_fabrication`.

## Gap analysis, lot 6 : export CSV des listes

Sur les listes principales (devis et lignes, commandes et lignes, livraisons, ordres de fabrication, factures, lots, mouvements et alertes de stock, tiers, contacts, articles, commandes/réceptions/factures fournisseur et lignes, écritures comptables), cochez des lignes (ou « Sélectionner les N éléments » pour toute la liste filtrée) puis l'action **« Exporter la sélection en CSV (colonnes affichées) »**.

- Le fichier contient exactement les colonnes de la liste (un export ne révèle jamais plus que l'écran, les colonnes réservées à certains droits le restent).
- Droit nécessaire : « voir » le modèle ; chaque export est tracé dans les logs (`docker compose logs web | grep "Export CSV"`).
- Format prévu pour Excel/LibreOffice en français : UTF-8 avec BOM, séparateur `;`, virgule décimale, dates JJ/MM/AAAA, nombres négatifs (avoirs) conservés comme nombres.
- Protection contre l'injection de formule : un texte commençant par `=`, `+`, `-` ou `@` est préfixé d'une apostrophe.
- Limite de 20 000 lignes par export (filtrer la liste au-delà).

Pour ajouter l'export à une autre liste : `from comptes.exports import ExportCsvMixin` puis `class MonAdmin(ExportCsvMixin, ModelAdmin)`.

## Passage des montants en Decimal

Tous les montants, prix, coûts et taux sont désormais des nombres décimaux exacts (`Decimal`, champ `comptes.champs.ChampDecimal`) et non plus des flottants : plus de `0.30000000000000004`, plus d'écart de centimes avec Tiime ou avec les écritures comptables.

**Règles (définies une seule fois dans `comptes/montants.py`)**
- Montants (HT, TTC, débit, crédit, prix de vente d'une ligne, frais de port) : 2 décimales.
- Prix et coûts unitaires (achat, coût matière, coût horaire, coût moyen pondéré) : 4 décimales (une vis à 0,0035 € existe).
- Prix de vente unitaire des lignes de commande et de facture : 6 décimales, pour qu'un total de devis ramené à l'unité redonne exactement le même total une fois remultiplié, même sur de grandes quantités.
- Taux (TVA, marges) : 2 décimales.
- Arrondi commercial (0,5 vers le haut : 1,005 → 1,01, alors que le flottant donnait 1,00), appliqué **ligne par ligne** ; les totaux sont la somme des lignes arrondies, ce qui correspond à la facture Tiime. Le prix de vente d'une ligne de devis (matière, chaque opération) est donc arrondi au centime à la source.
- Les quantités, durées et dimensions (mm, kg, minutes) restent des flottants : ce sont des grandeurs physiques, converties au moment de les multiplier par un prix.

**Ce qui change pour vous**
- Un montant saisi avec plus de décimales que son champ (ex. 12,345 € sur un montant) est refusé au lieu d'être silencieusement arrondi.
- Les listes affichent 100,00 au lieu de 100,0 ; l'API et le constructeur de devis renvoient toujours des nombres JSON (pas des chaînes).
- Les totaux d'un devis peuvent différer d'un centime de l'ancien affichage, car chaque ligne et chaque opération est maintenant arrondie avant d'être additionnée.

**Mise à jour de la base (NAS)** : la migration convertit les colonnes en place (PostgreSQL arrondit les anciennes valeurs au centime, ou à 4/6 décimales pour les prix). Les factures, déjà arrondies à 2 décimales, ne changent pas. **Faites une sauvegarde avant** : `./sauvegarder-nas.sh`, puis `./update-nas.sh claude/project-construction-k7owwb`.

Un test garde-fou (`MontantsDecimalTests.test_aucun_champ_monetaire_en_flottant`) échoue si un champ de montant, prix, coût, taux, débit ou crédit redevient un `FloatField`.

## Gestion de stock optionnelle

Toutes les sociétés ne gèrent pas un stock. Deux niveaux, indépendants :

- **Par article** : la case « Géré en stock » (cochée par défaut pour une matière première, décochée pour un fabriqué). Une réception ou une livraison d'un article non géré enregistre seulement la quantité reçue / livrée, sans lot ni mouvement. Désigner un lot pour un article non géré reste refusé (erreur de saisie).
- **Pour toute la société** : `DJANGO_STOCK_ACTIF=false` dans `.env` (puis `docker compose up -d`). Disparaissent alors le menu Stock, les écrans Lots / Mouvements / Emplacements / Alertes (accès direct refusé), la tuile « Alertes de stock », la rubrique de la synthèse quotidienne, les champs de stock des articles (géré en stock, stock minimum, quantité de réapprovisionnement) et les champs lot / coulée / certificat des réceptions et des livraisons. Les nouveaux articles ne sont jamais « gérés en stock ». Les données de stock déjà saisies ne sont pas touchées (elles redeviennent visibles si vous réactivez).

Le flux devis → commande → livraison → facture, les achats, la comptabilité et les PDF fonctionnent à l'identique sans stock. Ce que le stock apporte en plus (traçabilité des coulées, coût moyen pondéré, alertes) n'existe évidemment que lorsqu'il est activé.

## Devis : révisions et indices

Un devis validé (envoyé au client) ne se modifie plus. Pour le faire évoluer sans écraser ce qui a été envoyé, on crée un **nouvel indice** :

- Fiche du devis → **« Nouvel indice (réviser) »**. Un **motif** est obligatoire (ce qui change : remise, quantité, délai…).
- Le nouvel indice (B, puis C… ; AA après Z) est créé en **brouillon**, avec les mêmes lignes, sous le numéro `<devis d'origine>-<indice>` (ex. `DEV-2026-101-B`). Modifiez-le, chiffrez-le, validez-le comme n'importe quel devis (nouvelle date de validité à la validation).
- L'indice précédent passe à « **remplacé** » : il reste consultable, mais ne peut plus devenir une commande. Il n'est pas compté dans le taux de transformation.
- Un devis **déjà devenu commande** ne se révise plus : modifiez les lignes de la commande (chaque changement de quantité, prix ou TVA y est tracé).
- Si vous **supprimez** un indice brouillon abandonné, l'indice précédent redevient valable (retour à « en attente de réponse »).
- La fiche affiche l'**historique des indices** (numéro, date, statut, réponse du client, motif) et, pour une révision, un **tableau des changements** ligne par ligne par rapport à l'indice précédent (« à chiffrer » tant que le nouvel indice n'est pas recalculé).
- La liste des devis a une colonne **Indice** et un filtre « Derniers indices seulement ».
- Le **PDF** d'un indice porte « Indice B », « Annule et remplace DEV-… (indice A) » et « Modification : … ».
- API : `POST /api/v1/devis/<numéro>/reviser/` avec `{"motif": "..."}` (droit de modifier un devis) ; `indice`, `revision`, `motif_revision` et `devis_origine` sont renvoyés en lecture seule.

Les révisions déjà créées avant cette version gardent leur numéro `-R2` ; leur indice (B) s'affiche normalement.

## Commandes clients : AR, bon de préparation et ordres de fabrication

**Flux** : devis validé → **« Créer la commande »** (le devis ne crée plus que la commande et ses lignes) → sur la fiche de la commande, **« Créer les ordres de fabrication »**.

**Ordres de fabrication (OF)**
- Un OF par ligne de commande d'**article fabriqué** (les matières achetées et services n'en ont pas). La **quantité commandée** est reprise automatiquement.
- Une page d'aperçu montre ce qui sera créé avant validation. La case **« Regrouper les lignes du même article »** fusionne les lignes d'un même article en un seul OF (quantités additionnées, date de livraison la plus proche).
- Chaque OF reprend : la **date de livraison prévue** (la plus proche des lignes couvertes), la **gamme** (opérations, postes, temps prévus calculés sur la quantité) et la **nomenclature** (composants à sortir = quantité par pièce × quantité à fabriquer, avec longueur/largeur). Gamme et nomenclature sont **figées** à la création : modifier ensuite la fiche article ne change pas un OF déjà lancé.
- Le bouton est **rejouable sans doublon** : seules les lignes sans OF sont traitées (une ligne ajoutée plus tard se lance en recliquant). Chaque OF garde la liste des lignes de commande qu'il couvre.
- Les OF sont envoyés au planning atelier (date de livraison et composants inclus dans le message ; les OF antérieurs ne sont pas renvoyés pour autant).
- Droit : « ajouter un ordre de fabrication » (Responsable commercial, Direction, Atelier). Après mise à jour : `docker compose exec web python manage.py synchroniser_groupes`.

**Documents PDF** (fiche de la commande ; API `GET /api/v1/commandes/<n>/ar-pdf/` et `/bon-preparation-pdf/`)
- **AR de commande** : à envoyer au client — lignes, prix HT/TTC, TVA, **date de livraison prévue par ligne**, adresses de facturation et de livraison, sa référence de commande, l'offre d'origine (avec indice), conditions de règlement et mentions (nouveau champ « mentions sur les accusés de réception » de la fiche Société). Refusé si une ligne n'a pas de prix ; filigrane « ANNULÉE » si la commande l'est.
- **Bon de préparation** : document interne **sans prix** — quantités commandées et à livrer, date prévue, OF liés (ou « OF à créer »), stock disponible et n° de coulée pour les articles gérés en stock, case « Prêt » et zone de signature. Accessible au Magasinier.
- **Fiche de fabrication** (fiche de l'OF ; `GET /api/v1/ordres-fabrication/<n>/pdf/`) : quantité, livraison prévue, nomenclature et gamme avec cases « Fait ».
- **Impression de toutes les fiches en une fois** : après « Créer les ordres de fabrication », le message de confirmation propose le lien **« Imprimer les N fiche(s) de fabrication (PDF) »** (un seul PDF, une fiche par page, uniquement les OF qui viennent d'être créés). Le bouton **« Fiches de fabrication (PDF) »** de la fiche commande imprime ensuite tous les OF de la commande, et l'action de liste **« Imprimer les fiches de fabrication »** (liste des ordres de fabrication) ceux que vous sélectionnez. API : `GET /api/v1/commandes/<n>/fiches-fabrication-pdf/`.

**API** : `POST /api/v1/commandes/<n>/creer-ordres-fabrication/` (`{"regrouper": true}` facultatif) ; `POST /api/v1/devis/<n>/lancer-en-production/` ne crée plus que la commande.

## Documents de vente : facturation et livraison toujours indiquées

Tous les PDF de vente — **devis, AR de commande, bon de préparation, bon de livraison** et **fiche de fabrication** — portent désormais deux blocs côte à côte : **« Facturé à »** (nom de l'entreprise et adresse de facturation) et **« Livré à »** (nom de l'entreprise et adresse de livraison), **même quand les deux adresses sont identiques**. Sur un devis dont aucune adresse n'a été choisie, l'adresse principale du client (facturation / livraison) est utilisée ; si le client n'en a aucune, le bloc reste affiché avec la mention « Adresse non renseignée » plutôt que de disparaître. L'ordre est le même partout (facturation à gauche, livraison à droite).

## Modèles de documents PDF (éditeur visuel GrapesJS)

Les PDF de vente (**devis, AR de commande, bon de préparation, bon de livraison, fiche de fabrication**) peuvent être **mis en page librement** dans un éditeur visuel : menu **Paramétrage → Modèles de documents (PDF)**, réservé aux **superutilisateurs** (un modèle est du HTML rendu côté serveur : c'est un droit d'administration).

**Principe**
- Chaque document a **un modèle**, créé automatiquement avec la mise en page par défaut (identique aux PDF d'origine). Tant que la case **« Utiliser ce modèle pour les PDF »** n'est pas cochée, le PDF d'origine est utilisé : on peut éditer, tester et ne basculer qu'une fois satisfait.
- L'éditeur (GrapesJS, embarqué dans l'application, fonctionne hors ligne) permet de glisser-déposer des blocs (colonnes, titres, images, cadres, séparateurs), de styler chaque élément (police, couleurs, marges, bordures, positions…) et de réorganiser la page. Onglet **Blocs et variables** : les éléments du document (en-tête société, titre et références, « Facturé à / Livré à », tableau des lignes, totaux, mentions, signature, pied de page répété, filigrane, saut de page) et les **variables** (`{{ societe.nom }}`, `{{ facturation.adresse_html }}`, `{{ totaux.ttc }}`…) à déposer dans la page.
- **Aperçu PDF** : avec des données d'exemple, ou avec un **vrai devis / une vraie commande / livraison / OF** choisi dans la liste, sans rien enregistrer.
- **Enregistrer** (Ctrl+S) : le modèle est d'abord rendu avec des données d'exemple ; s'il ne peut pas l'être, il n'est pas enregistré. Les variables inconnues (faute de frappe) sont signalées. Chaque enregistrement est **historisé** (menu Historique). **« Revenir au modèle par défaut »** recharge la mise en page d'origine (à enregistrer pour la conserver).
- Repli de sécurité : si un modèle actif échoue au rendu pour une raison technique, **le PDF d'origine est produit** et l'erreur est journalisée ; les refus métier (ligne sans prix, devis vide…) restent identiques.

**Répéter et afficher conditionnellement** (onglet *Propriétés* d'un élément) : `Répéter sur` = `lignes` (une ligne de tableau par ligne de document ; `Nom de l'élément` = `ligne`), `Afficher si` = une variable (ex. `delai`, ou `!delai` pour l'inverse). Variables de ligne : `ligne.designation_html`, `ligne.quantite`, `ligne.pu_ht`, `ligne.total_ht`, `ligne.livraison_prevue`… (liste complète dans l'éditeur). Il n'y a volontairement **aucun langage de programmation** dans les modèles : seulement `{{ variable }}`, « répéter » et « afficher si ». Les scripts, gestionnaires d'événements, formulaires et ressources externes (fichiers locaux, adresses http) sont ignorés ou bloqués ; seules les images intégrées au document (`data:`) sont chargées.

**Limites** : les marges de page (A4, 15 / 18 / 26 mm) et la numérotation « Page x / y » sont fixes ; le texte de pagination se règle donc dans le code, pas dans l'éditeur. La mise en page de l'éditeur approche la page A4 : **le PDF d'aperçu fait foi** (retours à la ligne, sauts de page).

**Technique / déploiement** : rendu HTML → PDF par **WeasyPrint** (`requirements.txt`), qui exige Pango et des polices dans l'image Docker (`Dockerfile` mis à jour : `docker compose up -d --build`, c'est ce que fait `update-nas.sh`). GrapesJS 0.23 (licence BSD-3) est fourni dans `documents/static/documents/vendor/`. Pour mettre à jour cette bibliothèque : remplacer ces deux fichiers **en retirant la ligne `//# sourceMappingURL=…` en fin de `grapes.min.js`**, sinon `collectstatic` échoue.

Envoyez-moi vos modèles papier ou Word : je reproduis leur mise en page comme modèle par défaut.

## Menu latéral

Le menu suit le chemin d'une commande :
**Ventes** (Devis, Commandes clients, Livraisons (BL), Factures) → **Production** (Ordres de fabrication, Pièces à découper, Imbrications, Profils d'import) → **Achats** (Commandes fournisseur, Réceptions, Factures fournisseur, Envois et retours de sous-traitance, Fournisseurs d'article, Tarifs d'achat) → **Stock** (Lots, Mouvements, Alertes, Transferts, Inventaires, Emplacements) → **Comptabilité** → **Données de base** (Tiers, Adresses, Contacts, Articles, Matières, Nomenclatures, Gammes, Postes de travail, Tarifs de poste, Taux de TVA, Conditions de paiement, Délais proposés, Devises, Pays) → **Administration** (Société, Modèles de documents, Règles de codification, Utilisateurs, Groupes, Audit des droits, Journal des connexions).

- Comptabilité, Données de base et Administration sont **repliés** par défaut (ils s'ouvrent d'eux-mêmes quand on est dans l'un de leurs écrans).
- Chaque entrée n'apparaît que si l'utilisateur a le droit « voir » l'écran ; un groupe sans entrée visible disparaît. Le groupe Stock disparaît si `DJANGO_STOCK_ACTIF=false`.
- Pour déplacer ou ajouter un écran : `config/settings.py`, liste `NAVIGATION` (une ligne `_menu("Titre", "icône", "app", "modele")`).

## Adresses et contacts filtrés par client

Sur les fiches **Devis** et **Commande**, les listes « adresse de facturation », « adresse de livraison » et « contact » ne proposent que ce qui appartient au **client choisi** (recherche incluse). Changer de client vide ces champs avant de proposer l'adresse principale et le contact du nouveau client, pour ne pas garder par erreur l'adresse de l'ancien. Tant qu'aucun client n'est choisi, la liste reste complète. (Technique : `comptes/static/comptes/filtre_client.js` ajoute le client aux requêtes d'autocomplétion ; `AdresseAdmin` et `ContactAdmin` filtrent côté serveur.)

## Fiches devis, commande, facture, livraison, commande fournisseur, ordre de fabrication et article en deux colonnes

La fiche d'un devis place la **saisie à gauche** (numéro, client, adresses, contact, dates, statut, réponse du client, marge, délai) et un **récapitulatif à droite** (montants matière, opérations, total HT et TTC, indices et changements entre indices), visible dès l'ouverture sans descendre dans la page. Sous 1100 px de large, le récapitulatif repasse sous la saisie. Le tableau des lignes reste sur toute la largeur. La **fiche commande** suit la même disposition : à droite, le **total HT et TTC**, les **ordres de fabrication** (avec leur date de livraison prévue), les **livraisons** et les **factures** de la commande, chacun avec un lien vers sa fiche. La **fiche facture** place à droite l'**échéance** (avec « en retard »), les **montants calculés** depuis la commande, l'**écart** avec les lignes, les **relances de paiement**, la **facture d'origine / les avoirs** liés (liens) et l'**écriture comptable** (lien, ou « pas encore générée »). La **fiche livraison** place à droite le **client et l'adresse de livraison**, le **contenu** de la livraison, le **reliquat** restant à livrer sur la commande, ce qui est **livré mais pas encore facturé**, et l'**annulation** (date, auteur, motif) le cas échéant. La **fiche commande fournisseur** place à droite le **fournisseur** (nom et adresse), le **total HT et TTC**, le **reste à recevoir** par ligne, les **réceptions** et les **factures fournisseur** (liens, avec le montant facturé comparé au montant commandé). La **fiche ordre de fabrication** place à droite la **commande** d'origine (lien, client, livraison prévue), les **lignes de commande couvertes**, l'**avancement** (temps prévu / temps réel par opération, pièces bonnes et rebuts) et la **synchronisation avec le planning** (statut, tentatives, prochaine relance, dernière erreur) ; la nomenclature et la gamme restent en tableaux pleine largeur sous la fiche. La **fiche article** place à droite la **composition** (nombre de composants et d'étapes de gamme d'un article fabriqué, avec un rappel s'il est incomplet), le **prix d'achat actuel** par fournisseur (tarif en vigueur), le **stock** (quantité, seuil, alerte active — bloc absent si la gestion de stock est désactivée), les articles **dans lesquels il est utilisé** (liens) et son **activité** (lignes de devis, de commande, ordres de fabrication). Pour appliquer cette disposition à une autre fiche : fieldsets avec les classes `fiche-saisie` / `fiche-recap` (voir `DevisAdmin.get_fieldsets` et `CommandeAdmin.get_fieldsets`) ; les règles sont dans `comptes/static/comptes/fiche_deux_colonnes.css`.

## Bandeaux de confirmation cliquables

Quand une action crée ou modifie un document (commande créée depuis un devis, nouvel indice de devis, ordres de fabrication, ordre lancé depuis une ligne de commande, commande ou livraison annulée, mouvement de stock contre-passé, resynchronisation d'un OF), le **bandeau vert** contient un **lien vers le document** : un clic l'ouvre, sans le chercher dans la liste. Quand l'action ouvre déjà la fiche créée (facture préparée, avoir, duplication d'un article), il n'y a rien de plus à faire. Pour ajouter un lien dans un nouveau message : `comptes.liens.lien_admin(objet)` (à passer dans `format_html`).

## Bouton « Étape suivante » (devis → commande → fabrication → livraison → facture)

Sur la **fiche d'un devis, d'une commande et d'une livraison**, un bouton orange en haut propose **la prochaine action du cycle** et dit laquelle (« Étape suivante : Créer la commande »…). Il disparaît quand il n'y a plus rien à faire. Les étapes :

- **Devis validé** (ni refusé ni remplacé) → *Créer la commande* ; une fois créée, le bouton devient *Ouvrir la commande*. Depuis la liste des devis, « Créer la commande » sur **un seul devis** ouvre directement la commande.
- **Commande** → *Créer les ordres de fabrication* (écran d'aperçu, avec regroupement) tant qu'il reste des lignes fabriquées sans ordre, puis *Créer la livraison* (formulaire ouvert avec **les lignes à livrer pré-remplies** de leur reliquat, à ajuster avant d'enregistrer), puis *Préparer la facture* (écran filtré sur cette commande).
- **Livraison** → *Préparer la facture* s'il reste du livré non facturé.

Le bouton ne contourne aucun droit : sans la permission de créer l'objet suivant, un message l'indique. La décision de l'étape est dans `chiffrage/etapes.py`, l'exécution dans `EtapeSuivanteMixin` (`chiffrage/admin.py`). Les règles existantes (devis expiré, refusé, commande annulée…) restent appliquées.

## Thème visuel (lot A : pastilles, listes aérées, tuiles)

- **Pastilles de statut** dans les listes (devis : statut et réponse du client ; commandes ; livraisons ; factures : type et paiement ; ordres de fabrication : synchro planning ; inventaires ; alertes de stock ; pièces à découper). Le vocabulaire de couleurs est commun : bleu = en cours/brouillon, orange = à faire/en attente, vert = terminé/validé/payé, rouge = problème/annulé/refusé, ambre = information (avoir, remplacé). Il se déclare dans l'admin avec `pastilles = {"statut": {"valide": TERMINE, …}}` (`comptes/pastilles.py`, mixin `PastillesMixin`). Tri, filtres et export CSV sont inchangés.
- **Listes plus aérées** : lignes plus hautes, en-têtes en petites capitales grises, montants et quantités alignés à droite avec des chiffres de largeur fixe, numéro du document en gras de la couleur du thème, survol de ligne teinté.
- **Fiches** : cartes arrondies avec ombre légère, bandeau « Récapitulatif » avec icône et liseré ambre.
- **Accueil** : tuiles avec pastille d'icône et liseré coloré par thème — ambre (ventes), bleu acier (atelier), vert (trésorerie), rouge (à traiter). La correspondance tuile → thème est dans `comptes/dashboard.py` (`THEMES_TUILES`).
- Tout le style maison est dans `comptes/static/comptes/theme.css` (variables en tête de fichier : rayon, ombres, teintes) ; le mode sombre est repris du thème Unfold.

## Recherche globale (Ctrl+K) et menu « + Nouveau » (lot B)

- **Recherche globale** : `Ctrl + K` (`⌘ K` sur Mac) ou clic sur la barre en haut du menu latéral. Une seule barre qui cherche, au fil de la frappe, dans les **devis, commandes, livraisons, ordres de fabrication, factures, articles, clients/fournisseurs et commandes fournisseur** (numéro, client, référence… : mêmes champs de recherche que les listes), et propose aussi des **écrans** (« factures en retard », « devis sans réponse », « ordres de fabrication non transmis », « nouveau devis »…) sans tenir compte des accents. 5 résultats par type ; Entrée ouvre le résultat, Ctrl+Entrée l'ouvre dans un nouvel onglet ; l'historique des dernières recherches est conservé. Les résultats **respectent les droits** : un type de document que l'utilisateur n'a pas le droit de voir n'apparaît jamais. Code : `comptes/recherche.py` (types de documents et détails affichés dans `DOCUMENTS`).
- **Menu « + Nouveau »** en haut de chaque page : nouveau devis, nouvelle commande, nouvelle livraison, préparer une facture, nouvelle commande fournisseur, nouveau client/fournisseur, nouvel article — **seules les entrées autorisées** apparaissent (le menu disparaît s'il n'y en a aucune). Les entrées et les écrans de la recherche sont définis dans une seule liste : `comptes/raccourcis.py` (`CREATIONS`, `ECRANS`).

## Bouton « Enregistrer et valider » (et ouvrir le PDF)

Dans la barre d'enregistrement des fiches **devis, commande, livraison et ordre de fabrication**, un bouton enregistre la fiche **puis ouvre directement son PDF** :

- **Devis** : *Enregistrer, valider et ouvrir le PDF* — le statut passe à « Validé » (avec les mêmes contrôles que d'habitude : au moins une ligne, chiffrage complet, vente sous le coût réservée à qui en a le droit) puis le PDF du devis s'ouvre. Si la validation est refusée, la fiche est enregistrée, les raisons s'affichent et on reste sur la fiche (le devis reste en brouillon). Le bouton n'apparaît que pour qui a la permission « valider un devis ».
- **Commande** : enregistre et ouvre l'**AR de commande** ; **livraison** : le **bon de livraison** ; **ordre de fabrication** : la **fiche de fabrication**.
- Les factures (émises dans Tiime) et les commandes fournisseur n'ont pas de PDF dans l'ERP : pas de bouton.
- Le menu « + Nouveau » s'élargit à son contenu (jamais de retour à la ligne) et s'aligne sur le bord de l'écran s'il ne tient pas.
- Code : `EnregistrerEtValiderMixin` (`chiffrage/admin.py`) ; un admin déclare `url_pdf`, `libelle_valider` et, s'il se valide, `valeurs_validation` / `pret_pour_pdf`.

## Récapitulatif plus large et « Mes colonnes »

- **Récapitulatif plus large** sur toutes les fiches en deux colonnes (devis, commande, facture, livraison, commande fournisseur, ordre de fabrication, article) : 38 % de la largeur, entre 28 et 46 rem, au lieu de 28 rem fixes — les listes d'ordres de fabrication, de livraisons ou de factures tiennent sur une ligne.
- **Mes colonnes** (menu du compte en bas à gauche, ou Ctrl+K « colonnes ») : chaque utilisateur coche les colonnes qu'il veut voir. Réglage **personnel**, enregistré par utilisateur (`PreferenceColonnes`) ; « Rétablir les colonnes par défaut » efface ses choix. Écrans concernés : listes des devis, commandes, livraisons, factures, ordres de fabrication et articles ; tableau des lignes d'une commande (désignation, date de livraison possible, statut d'approvisionnement, livré, reliquat, entièrement livrée, facturé, livré non facturé).
- Jamais masquables : le lien vers la fiche (première colonne d'une liste) et, dans le tableau des lignes, les colonnes indispensables à la saisie et aux calculs en direct (article, quantité, prix, TVA, montants, date de livraison prévue).
- Pour rendre un écran personnalisable : ajouter `ColonnesPersonnalisablesMixin` (`comptes/colonnes.py`) devant `ModelAdmin` (liste) ou `TabularInline` (lignes, avec `colonnes_optionnelles = [...]`) ; `colonnes_masquees_par_defaut` fixe ce qui est masqué tant que l'utilisateur n'a rien choisi.

## Temps de découpe (jet d'eau) — phase 1

Le temps de découpe d'une pièce est **calculé** depuis sa géométrie (importée du DXF) et les **paramètres de coupe** de la machine, au lieu d'être saisi à la main dans la gamme.

- **Paramètres de coupe** (menu Production) : une fiche par **matière et épaisseur**, reprise du logiciel de la machine (IGEMS) — vitesses élevée/basse par **niveau de qualité** (1,5 extra brut → 5 extra fin) avec paliers, distances d'accélération/décélération et coefficients ; perçage (stationnaire HP/BP, circulaire, diamètre, pointage) et mode retenu ; marquage (vitesse, temporisation) ; percement linéaire, chevauchement ; **intervalle entre pièces** (réservé à l'imbrication, phase suivante) ; poste de travail (son tarif horaire valorise le temps dans la gamme).
- **Pièce à découper** : champ **Qualité de coupe** (3 « Moyen+ » par défaut) et ligne **Temps de découpe estimé** (minutes par pièce : coupe, perçage, marquage, nombre de coins et de perçages, paramètres utilisés). Si l'épaisseur exacte n'existe pas, la plus proche est utilisée avec un avertissement.
- **Alimenter la gamme de l'article** (bouton de la fiche pièce) : crée ou met à jour l'étape de découpe de la gamme de l'article fabriqué (temps variable = minutes par pièce, poste de la machine). Une étape déjà calculée est mise à jour sur place le jour même, historisée (date de fin + nouvelle étape) un autre jour ; une étape **saisie à la main n'est jamais écrasée**, et retoucher une étape calculée la fait passer en « saisie à la main » (colonne *Origine* de la gamme). Le réglage machine (temps fixe) reste à saisir.
- **Modèle de calcul** (`decoupe/services/temps.py`) : chaque contour est coupé à la vitesse élevée sur les droites et les grands arcs, avec une vitesse qui décroît jusqu'à la vitesse basse dans les courbes serrées (rayon de pleine vitesse, paliers) ; chaque coin ralentit sur A + R ; amorce et chevauchement par contour ; perçage par contour ; marquage ; le tout multiplié par un **coefficient d'ajustement** — c'est le réglage qui sert à **caler le calcul sur les temps réellement donnés par le logiciel de la machine** (comparer quelques pièces et ajuster).
- Pas encore pris en compte : le laser, les déplacements à vide entre contours, l'importation en masse du fichier `materials.lua` d'IGEMS (à venir, avec la matière par imbrication et la simulation de formats).

## Usinabilité : vitesses de coupe calculées pour les matières et épaisseurs non relevées

- **Usinabilité des matières** (fiche Matière, colonne dans la liste) : indice utilisé par le logiciel de la machine. Action de liste **« Renseigner l'usinabilité standard »** : remplit, d'après le nom de la matière, les valeurs standard — acier trempé 80,4 ; inox 81,9 ; acier 87,6 ; cuivre/laiton 110 ; titane 115 ; alliage de zinc 136 ; aluminium 213 ; granit 322 ; marbre 535 ; nylon 538 ; plexiglas 690 ; graphite 879 ; polypropylène 985 (les matières déjà renseignées ne bougent pas ; une matière non reconnue est signalée).
- **Vitesses calculées** (`decoupe/services/vitesses.py`) : la vitesse élevée suit **V = K × g(qualité) × U^1,173 / e^1,126** (U usinabilité, e épaisseur). Ce modèle retrouve à 0,1 % près les trois tables relevées (acier 10 mm, cuivre 8 mm, aluminium 20 mm ; 15 valeurs) ; les exposants ne reposent que sur ces trois points, à confirmer avec d'autres relevés. La vitesse basse est une approximation (rapport basse/élevée relevé à 8, 10 et 20 mm, interpolé). Paliers et distances d'accélération/décélération suivent les relevés (⌈0,3 e⌉ et 0,3 e).
- **Dupliquer vers d'autres épaisseurs** (bouton de la fiche *Paramètre de coupe*) : à partir d'un paramètre relevé sur la machine, crée les autres épaisseurs d'une matière (ex. « 3, 4, 5, 6, 8, 12, 15, 20 ») avec perçage, percement et chevauchement proportionnels à l'épaisseur et vitesses calculées. Action de liste **« Calculer les vitesses depuis l'usinabilité »** pour un paramètre sans vitesses.
- **Garde-fous** : un paramètre porte une *origine* — « relevées sur la machine » ou « calculées (estimation) ». Les vitesses relevées ne sont **jamais écrasées** par un calcul ; l'estimation du temps de découpe d'une pièce affiche un avertissement tant que ses vitesses sont calculées.

## Import de materials.lua et calage sur des temps réels

- **Importer materials.lua (IGEMS)** (bouton en haut de la liste *Paramètres de coupe*) : en trois temps — téléversement du fichier (`IGEMS_R9\shared\Material\materials.lua`), contrôle de la **correspondance des matières** (Steel → Acier, Stainless Steel → Inox, Copper → Cuivre, Brass → Laiton, Titanium → Titane, Glas → Verre, Granite → Granit, Marble → Marbre, Grafite → Graphite, Nilo → Nylon… modifiable ; une matière absente de l'ERP est créée, vide = ignorée), choix du **poste de travail**, puis création d'**un paramètre par matière et épaisseur** (153 pour votre fichier : 12 matières, de 1 à 80 mm). Repris du fichier : usinabilité, perçage stationnaire HP/BP, perçage circulaire, diamètre, pointage, marquage, percement linéaire, chevauchement, intervalle entre pièces, paliers, distances d'accélération/décélération et coefficients de chaque qualité. Les paramètres *relevés sur la machine* ne sont jamais touchés ; un second import met à jour les paramètres calculés.
- **Vitesses** : le fichier ne contient pas les vitesses du jet d'eau (le logiciel les calcule). Elles sont calculées par notre modèle (`decoupe/services/vitesses.py`) : V = K × g(qualité) × U^1,091 / e^1,099.
- **Calage sur 9 temps réels** (qualité 3) : une pièce arrondie de 4,5 m de contour (`piece_calibrage.dxf`), une plaque percée de 1101,7 mm de coupe, surtout en lignes droites, et un **plan de découpe de 54 cercles et 20 m de coupe** (`plan_decoupe_inox25.dxf`). Temps réels contre temps calculés : aluminium 30 mm 46,5 → 46,0 min ; aluminium 10 mm 11,75 → 11,85 ; inox 20 mm 78 → 78,6 ; cuivre 15 mm 39 → 39,2 ; acier 5 mm 14,1 → 14,2 ; acier 25 mm 27,75 → 27,75 ; acier 35 mm 43,5 → 43,6 ; aluminium 50 mm 27,75 → 27,5 ; **inox 25 mm (plan) 7 h 10 → 7 h 14**. **Écart maximal 1,2 %, moyen 0,7 %**, vérifié par des tests automatiques (maximum toléré 5 %, moyenne 3 %). Le plan et la plaque donnent aussi la décomposition du logiciel (coupe, perçage, transferts) : le modèle la retrouve à quelques % près pour la coupe et le perçage, un peu moins bien pour les transferts (2,5 min contre 3,35 min : ils dépendent des distances, comptées ici à 2,75 s par contour).
- **Ce que le calage a appris** : (1) le **rayon à partir duquel un arc se coupe à pleine vitesse est proportionnel à l'épaisseur** (≈ 2,2 × l'épaisseur) ; (2) la machine ne descend pas aussi bas que la vitesse basse affichée dans les courbes : **facteur de vitesse en courbe** 1,24 ; (3) la durée de perçage vaut environ la **moitié** du perçage stationnaire de chaque contour (*facteur de perçage* 0,5) ; (4) un **déplacement à vide** de 2,75 s par contour. Ces réglages sont des champs du paramètre de coupe (*Réglages du calcul*) ; la migration recalcule les paramètres déjà importés (les paramètres relevés sur la machine gardent leurs vitesses).
- **Plans de découpe (plusieurs pièces dans un même DXF)** : l'import garde toujours la plus grande silhouette comme « pièce » (aperçu, imbrication) mais **mémorise toutes les silhouettes** (`contour_json["autres"]`) : le temps de découpe compte tous les contours du fichier.
- Pour affiner encore : fournir d'autres temps réels (qualités différentes de 3, pièces à angles vifs en forte épaisseur) ou ajuster le *coefficient d'ajustement* d'une matière. Le temps ne dépend pas du nombre de pièces identiques : pour N pièces, multiplier (le logiciel ne compte pas d'économie d'échelle sur la coupe).
- Le laser n'est pas traité (une seule matière du fichier, inox, porte l'indicateur laser).

## Matière par imbrication (phase 2) : coût au prorata de la surface consommée, simulation de formats

- **Placement plus dense** (`decoupe/services/imbrication.py`, `imbriquer_meilleur`) : en plus des étagères, des variantes « MaxRects » (la pièce est posée dans le meilleur espace libre de toute la feuille, deux tris, deux règles de placement) ; on garde la meilleure (le moins de feuilles, puis la plus petite bande entamée sur la dernière). **Jamais pire que l'ancien calcul** ; exemple : 3 feuilles → 2 pour un mélange de pièces de 1100 × 430 et 260 × 190 mm. Les pièces restent représentées par leur rectangle englobant (placement sur les contours réels : phase suivante) ; l'**intervalle entre pièces** vient du paramètre de coupe de la matière et de l'épaisseur (4 mm à défaut).
- **Formats de tôle** (menu Production) : catalogue éditable (3000 × 1500, 2500 × 1250, 2000 × 1000, 4000 × 2000, 6000 × 2000, 3000 × 1250 fournis).
- **Simuler l'imbrication et le coût matière** (bouton de la fiche *Pièce à découper*) : choix de la **tôle** (article matière première, vendue au m², au kg — avec épaisseur et densité — ou à la feuille), de **quantités** (1 à 6 : 1, 10, 50, 100…) et du **taux de chute récupérable** ; le tableau compare tous les formats : nombre de feuilles, % d'utilisation, **coût total et coût par pièce** (le meilleur par quantité en vert), avec l'aperçu de la feuille 1. **« Retenir »** enregistre la tôle, le format et le taux sur la pièce et active le chiffrage par imbrication.
- **Calcul du coût matière** (`decoupe/services/matiere.py`) : *surface consommée* = feuilles entières + seulement la **bande entamée de la dernière feuille** (le reste est une chute réutilisable rendue au stock) ; *chutes* = surface consommée − surface des pièces ; *surface facturée* = pièces + chutes × (1 − chute récupérable) ; coût = surface facturée × prix de la tôle. Le coût par pièce **dépend de la quantité** (10 pièces coûtent plus cher l'unité que 500).
- **Dans le devis** : si la pièce (liée à l'article fabriqué) est en « chiffrer la matière par imbrication », le coût matière de l'article est calculé par imbrication pour la **quantité de la ligne** ; la ligne de nomenclature de cette tôle (rectangle) n'est plus comptée (les autres composants — vis, etc. — restent). Pièce trop grande pour la tôle : le chiffrage est refusé avec un message clair.
- Limites : l'imbrication travaille sur le rectangle englobant de la silhouette principale de la pièce ; pour les pièces très découpées ou en L le taux d'utilisation est donc prudent. Les résultats sont mis en cache (même pièce, même quantité, même format).

## Familles de matière et base de coupe (temps de perçage de materials.lua)

Une **famille de matière** est une matière « générique » (Acier, Inox, Aluminium, Cuivre, Laiton, Titane, Acier trempé…, menu *Familles de matière*) à laquelle se rattachent les **nuances précises** (S235, S355, 5754, 6082…). La nuance **hérite** de sa famille : usinabilité standard, temps de perçage, vitesses, intervalle entre pièces.

- **Rattachement automatique** : à la création d'une matière, la famille est déduite du nom grâce aux *mots-clés* de chaque famille (S235 / S355 → Acier ; 2017 / 5083 / 5754 / 6082 → Aluminium ; 304L / 316L / 1.4301 → Inox ; Hardox → Acier trempé). Les mots-clés sont modifiables ; la priorité (« inox » avant « acier ») aussi. Pour les matières existantes : liste des matières → action « Rattacher à une famille ». Un choix manuel n'est jamais écrasé.
- **Base de coupe fournie** : les 153 fiches du `materials.lua` (12 matières, 1 à 80 mm : perçage stationnaire/circulaire, pointage, marquage, percement, chevauchement, intervalle entre pièces, paliers et coefficients par qualité) sont chargées au démarrage (migration) au niveau des familles, avec des vitesses calculées (« estimation »). Les fiches relevées sur la machine ne sont jamais écrasées.
- **Un paramètre de coupe est soit à une famille (cas général), soit à une nuance (exception)**. À épaisseur égale la nuance prime sur la famille ; sinon c'est l'épaisseur la plus proche qui est retenue (avec avertissement).
- Le poste de travail n'est pas renseigné par la base fournie : liste des paramètres → sélectionner → « Affecter un poste de travail ».
- L'import de `materials.lua` associe désormais chaque matière du fichier à une **famille** (créée si absente), plus à une matière.
- Les paramètres déjà saisis pour une matière portant le nom de sa famille (« Acier »…) ont été repris au niveau de la famille ; les autres restent des exceptions de nuance.

## Découpe laser fibre (tableau du constructeur)

Le classeur du constructeur (BySmart Fiber 6 kW) est intégré comme **second procédé de découpe**, à côté du jet d'eau.

- **Base chargée au démarrage (migration)** : 132 fiches par famille de matière, gaz et épaisseur — Acier (O₂, N₂, air), Acier galvanisé et électrozingué (N₂), Inox (N₂, air), Aluminium (N₂, O₂, air), Laiton (N₂), Cuivre (O₂) — avec la **vitesse de production** (et la vitesse maximale), la consommation de gaz, la puissance absorbée et le **temps de perçage** (colonne 6 kW ; les colonnes 3 et 4 kW sont ignorées). L'inox avec film n'est pas repris. Les valeurs « limite » du constructeur (bavure possible, acier SSAB) sont signalées dans la remarque et dans l'estimation.
- **Épaisseurs : seules celles de la base sont réalisables.** Une pièce laser dont l'épaisseur est absente (ou dont le gaz n'existe pas à cette épaisseur) est refusée, avec la liste des épaisseurs possibles. Le **0,5 mm**, absent du tableau, est extrapolé depuis 0,8 et 1 mm (vitesse plafonnée à +15 % de celle de 0,8 mm) et marqué « estimation ».
- **Coefficient de pondération** (menu *Réglages de coupe*) : multiplie les vitesses du constructeur, jugées surestimées. Valeur initiale 0,85 (15 % plus lent) à ajuster sur vos temps réels ; le coefficient d'ajustement de chaque paramètre reste disponible pour un cas particulier.
- **Temps laser d'une pièce** = longueur coupée ÷ (vitesse de production × pondération) + un perçage par contour + déplacements entre contours (1 s par contour). Le marquage n'est pas compté au laser.
- **Sur la pièce à découper** : choisir le *procédé de coupe* (jet d'eau ou laser) et, au laser, le *gaz* (vide = gaz usuel de la famille : oxygène pour l'acier et le cuivre, azote pour l'inox, l'alu et le laiton ; modifiable sur chaque famille). Le temps, l'écart entre pièces et la gamme suivent le procédé ; changer de procédé met à jour l'étape de gamme existante (poste compris).
- **Écart entre pièces à l'imbrication** : jet d'eau 6 mm constant ; laser 10 mm au minimum, et **plus grand quand l'épaisseur augmente** (écart = épaisseur à partir de 10 mm). Plancher réglable dans *Réglages de coupe* ; l'intervalle de chaque paramètre l'emporte s'il est plus grand.
- Le **poste de travail** (et son tarif horaire) se renseigne comme pour le jet d'eau : Paramètres de coupe → sélectionner → « Affecter un poste de travail ».

## Export vers le comptable (format ISACOMPTA)

L'export mensuel que vous faisiez avec l'ancien logiciel est reproduit : **Écritures comptables → « Exporter vers le comptable (ISACOMPTA) »**. On choisit le mois, un aperçu donne le nombre d'écritures de chaque fichier, puis un ZIP contient les trois fichiers habituels : `vente_MM-AAAA.txt`, `achat_MM-AAAA.txt`, `banque_MM-AAAA.txt`. En ligne de commande : `manage.py export_comptable 2026-01 [dossier]`.

- **Format** : texte à largeur fixe (lignes `VER`, `DOS`, `EXO`, puis par écriture `ECR` / `MVT` / `ECHMVT`), fins de ligne CRLF, ASCII sans accents. Le format a été relevé sur vos trois fichiers d'export et vérifié en les relisant puis en les réécrivant avec notre code : 1 149 lignes sur 1 156 identiques à l'octet, les 7 autres ne différant que par des particularités des données d'origine (une espace en fin de libellé, un caractère de tabulation dans un compte).
- **Ventes et achats** : les écritures du mois des journaux de nature Ventes / Achats, rangées *Produits / TVA / Tiers* (ventes) et *Charges / TVA / Tiers* (achats). Le mouvement du tiers porte l'échéance (conditions de paiement du client ou du fournisseur ; à défaut la date de la pièce). Un compte général de moins de 6 chiffres est complété à 6 (44571 → 445710) ; un compte de tiers (411DUPON) reste tel quel, 8 caractères au plus.
- **Banque** : une écriture par règlement du mois, rangée *Banque / Tiers* — encaissement d'une facture client **payée** (banque au débit, client au crédit, avec échéance) ou règlement d'une facture fournisseur (fournisseur au débit, banque au crédit). Une facture client est reprise quand elle est « Payée » avec sa date de paiement ; la facture fournisseur a désormais une **date de règlement**.
- **Réglages** (menu *Export comptable*) : codes de journaux du fichier (VT, AC, B2), compte de banque (512100), composition des libellés (code facture + nom du tiers pour les ventes et les achats, nom du tiers pour la banque, 30 caractères au plus) et référence de pièce des achats (code facture ou référence fournisseur ; la référence du fournisseur est toujours reprise dans sa propre colonne). Les valeurs initiales sont celles de vos paramètres actuels.
- **Pièce** : les 8 derniers caractères du numéro de facture (comme `C25-3948` pour `FC25-3948`).
- Les champs « dossier » et « exercice » restent vides comme dans vos fichiers. Deux dates identiques figurent dans chaque écriture : la date de l'export.

## Autoliquidation de la TVA (achats à l'étranger)

La génération de l'écriture d'une **facture fournisseur** gère l'autoliquidation : quand le fournisseur est étranger et ne facture pas de TVA, l'écriture porte la TVA à la fois **déductible** (débit 445663) et **due** (crédit 445200) du même montant, calculée au taux français ; le fournisseur n'est crédité que du **HT** (comme dans vos exports : achat 436,50 / TVA 87,30 en débit et crédit / fournisseur 436,50).

- **Quand ?** Case « Autoliquidation de la TVA » de la facture fournisseur : vide = automatique selon le régime fiscal du fournisseur (**intracommunautaire ou hors UE : oui**, France : non) ; « Non » pour une importation de marchandises dont la TVA est payée à la douane ; « Oui » pour forcer.
- **Taux** : celui de la ligne de commande s'il est renseigné (5,5 % par exemple), sinon le taux de TVA par défaut (20 %).
- **Comptes** : 445663 et 445200, créés au premier usage ; modifiables dans *Paramètres comptables* (section Achats).
- **Export comptable** : les trois mouvements sont rangés charges, TVA, tiers ; l'échéance et le règlement bancaire portent le **HT** seul, puisque le fournisseur n'a pas facturé de TVA. La liste des factures fournisseur affiche la date de règlement et se filtre sur l'autoliquidation.
- Si une facture fournisseur a déjà son écriture, elle n'est pas régénérée : pour appliquer l'autoliquidation à une facture déjà comptabilisée, supprimez l'écriture puis régénérez-la.

## Séparateurs du menu « + Nouveau »

Les titres de groupe du menu (Ventes, Achats, Données) sont maintenant des bandeaux explicites : fond teinté, liseré orange à gauche, texte plus grand et en gras, avec un espacement net entre les groupes.

## Menu latéral : titres de groupe explicites

Les titres du menu latéral (Ventes, Production, Achats…) reprennent le style des séparateurs du menu « + Nouveau » : bandeau teinté, liseré orange, majuscules en gras.

## Maquette : import DXF/DWG et imbrication dans le devis

`docs/maquettes/maquette_devis_dxf.html` (et son image `.png`) : proposition d'écran pour importer des DXF/DWG, régler matière / épaisseur / procédé, imbriquer et ajouter les pièces au devis dans la même page. Maquette seule : rien n'est encore câblé.

## Devis : import de DXF/DWG et réglage des pièces dans la page (étape 1)

La fiche d'un devis (enregistré) a un panneau **« Pièces à découper »** sous les lignes :

- **Importer** : glisser un ou plusieurs DXF/DWG (ou parcourir). Chaque fichier crée une **pièce à découper** (géométrie lue, aperçu, nombre de trous, longueur de contour) liée au devis, et un **article fabriqué** : référence « DEV-…-P01 », « -P02 »… ou, si une règle de codification « Article fabriqué créé depuis un devis » est configurée, le code suivant de la règle. Le procédé par défaut (laser ou jet d'eau) et le profil d'import des calques se choisissent avant le dépôt.
- **Régler** chaque pièce sans quitter la page : nom, matière, épaisseur, procédé, gaz (laser ; vide = gaz usuel de la famille), quantité. Les réglages sont enregistrés à la volée et reportés sur l'article (matière, épaisseur, libellé).
- **Verdict immédiat** : « Réalisable · x min de coupe par pièce », ou la raison du refus — par exemple « Non réalisable au laser : 7 mm n'est pas dans la base » avec la liste des épaisseurs possibles. Le temps de coupe alimente la **gamme** de l'article dès que le paramètre de coupe a un poste de travail (sinon le panneau le signale). L'affichage de la fiche n'écrit jamais rien.
- **Retirer** une pièce supprime aussi son article s'il n'est utilisé nulle part (sinon l'article est conservé et le panneau le dit).
- Un devis **validé** est verrouillé : les pièces s'affichent mais ne se modifient plus. Il faut les droits d'ajout de pièces à découper et d'articles.



## Devis : imbrication des pièces dans la page (étape 2)

Sous les pièces, le panneau imbrique **ensemble** toutes les pièces d'une même matière, épaisseur et procédé (un bloc « Imbrication — S235 · 10 mm · laser » par groupe) :

- **Tôle** : liste des articles matière première de la matière (ou de sa famille) et de l'épaisseur du groupe ; une seule tôle est choisie d'office. Sans tôle en base, le panneau le dit et ne calcule pas le coût matière.
- **Formats** : tous les formats actifs (menu *Formats de tôle*) sont calculés et comparés (feuilles, utilisation, surface consommée, coût). Le moins cher est signalé ; un clic sur une ligne affiche ce format.
- **Réglages** : marge de bord et part de **chute récupérable** (0 % : toutes les chutes sont facturées). L'écart entre pièces suit le procédé : 6 mm au jet d'eau, au moins 10 mm au laser et croissant avec l'épaisseur.
- **Résultat** : feuilles, utilisation, surface consommée, coût matière du lot, aperçu coloré de la première feuille (une couleur par pièce) et **coût matière réparti entre les pièces** au prorata de leur surface, selon la quantité.
- Une pièce **non réalisable** (laser hors base) est exclue de l'imbrication avec sa raison ; une pièce sans matière ou épaisseur est listée « à régler ».
- **Retenir** enregistre tôle, format, marge et chute récupérable sur les pièces du groupe (il sert à l'étape suivante). Le calcul se met à jour tout seul à chaque import, réglage ou retrait de pièce. Sur un devis validé, le calcul reste consultable mais rien ne se retient.
- Limite connue : l'imbrication place des rectangles englobants (pas les formes réelles) ; l'aperçu dessine les contours.

## Devis : pièces ajoutées aux lignes avec leur prix (étape 3)

Sous l'imbrication, le tableau **« Chiffrage des pièces »** donne, pour chaque pièce réalisable : temps de coupe par pièce, matière HT, opérations HT, prix unitaire et total HT. Le bouton **« Ajouter au devis »** crée (ou met à jour) la ligne de devis de chaque pièce prête, avec sa quantité, et la chiffre ; la page se recharge pour montrer les lignes et le récapitulatif.

- **Matière** : la ligne reprend la part de matière que lui répartit l'imbrication **retenue** de son groupe, les autres pièces du groupe gardant leur quantité (une pièce n'est pas chiffrée comme si elle était seule sur la tôle). Si la quantité de la ligne change, l'imbrication est recalculée avec la nouvelle quantité.
- **Opérations** : le temps de coupe de la gamme de l'article, au tarif du poste de travail. Une pièce n'est ajoutée que si le **poste** du paramètre de coupe est renseigné (sinon le prix serait faux) ; elle reste listée avec la raison.
- **Marges** : l'article fabriqué reprend la marge de sa tôle au moment où l'on retient l'imbrication ; les marges du devis (taux global) et des postes s'appliquent comme sur toute ligne.
- **Prêt = ** pièce réalisable (laser dans la base), tôle et format retenus, gamme alimentée. Une pièce non prête est ignorée avec sa raison ; les autres sont ajoutées.
- Seul un devis **en brouillon** reçoit des lignes ; il faut le droit d'ajouter des lignes de devis. Ré-appuyer sur le bouton met à jour les lignes existantes sans doublon.
- La gamme de l'article est datée du devis (si celui-ci est antérieur à aujourd'hui) pour que le devis la retrouve. Les pièces ajoutées sont marquées « matière par imbrication » : les autres chiffrages de l'article (commande…) retrouvent la tôle imbriquée.

## Capacité des machines et affichage de toutes les feuilles

- **Capacité de coupe par machine** (menu *Réglages de coupe*) : jet d'eau **4000 × 2000 mm**, laser **3000 × 1500 mm** (0 = pas de limite). Un format de tôle plus grand que la machine du groupe est **écarté de l'imbrication** : il reste listé dans la comparaison avec « Dépasse la capacité du laser (3000 × 1500 mm) », ne peut pas être retenu et n'est jamais choisi d'office ; la tôle peut se présenter dans un sens ou dans l'autre. Si aucun format actif ne tient dans la machine, le panneau le dit. Un format déjà retenu qui ne convient plus empêche le chiffrage de la ligne avec un message clair. La simulation d'imbrication d'une pièce applique la même règle (formats non proposés signalés).
- **Toutes les feuilles sont dessinées** (et non plus seulement la première) : « Feuille 3 sur 6 · 10 pièces », en grille, la dernière étant signalée comme entamée (le reste est une chute récupérable). Au-delà de 40 feuilles, les premières sont affichées et le reste est indiqué.
- **Rotation** : chaque pièce a un réglage « Rotation » (aucune si le sens de laminage ou de grain est imposé, sinon 90°, 45° ou 5°). Les pièces importées depuis un devis démarrent à **90°** : l'imbrication peut les tourner, ce qui réduit le nombre de feuilles et donc le prix ; choisir « Aucune » si le sens de la matière compte.

## Imbrication selon la forme réelle des pièces

L'imbrication des pièces d'un devis ne se limite plus aux rectangles englobants : elle suit le **contour réel** des pièces, **trous compris** (case « Selon la forme » de chaque groupe, cochée par défaut). Exemple : 30 équerres de 440 × 380 mm tiennent sur **1 tôle 3000 × 1500** au lieu de 3 par les rectangles (224 € de matière au lieu de 357 €).

- **Méthode** : la feuille est découpée en cellules de quelques millimètres ; chaque pièce, tournée selon ses rotations autorisées (8 orientations au plus), est placée en bas à gauche à l'endroit libre qui descend le moins loin, les plus grandes d'abord ; elle est ensuite **tassée** vers le bas et la gauche, au dixième de millimètre près, jusqu'à l'écart demandé de ses voisines. Une petite pièce peut se loger dans le trou d'une grande.
- **Garantie** : chaque résultat est contrôlé avec les vrais contours — pièce dans la zone utile (marge de bord) et au moins l'écart de la machine (6 mm jet d'eau, 10 mm et plus au laser) entre deux pièces. Si le contrôle échoue ou si une pièce n'a pas de contour, le calcul retombe sur l'imbrication par rectangles. La forme n'est retenue que si elle fait mieux (moins de feuilles, puis moins de bande entamée).
- **Réglage** : décocher « Selon la forme » pour comparer ; « Retenir » mémorise le choix (champ « imbriquer selon la forme » des pièces) et le chiffrage de la ligne l'applique. Les coûts matière des pièces seules (articles chiffrés par imbrication) suivent le même réglage, activé par défaut : ils ne peuvent que baisser.
- **Limites** : les silhouettes multiples d'un plan de découpe ne sont pas imbriquées individuellement ; la précision de la grille est de quelques millimètres avant tassement ; le calcul prend de l'ordre de la seconde par format (résultats mis en cache tant que les pièces et quantités ne changent pas).

## Bibliothèque de formes paramétriques (devis)

Sous la zone de dépôt du panneau « Pièces à découper » d'un devis, la **Bibliothèque de formes** crée une pièce sans fichier DXF : on choisit une forme, on saisit les cotes, l'aperçu se met à jour en direct (avec le message d'erreur en clair si les cotes sont incohérentes : trou qui sort de la pièce, trous qui se chevauchent…), puis « Ajouter la pièce au devis ». La pièce suit ensuite le même chemin qu'un DXF importé : article fabriqué, matière/épaisseur, verdict, imbrication selon la forme, ajout aux lignes. Le contour calculé est écrit en DXF (polylignes, arcs discrétisés avec une flèche de 0,05 mm) : on peut le récupérer pour la machine.

- **Formes libres** : rectangle (angles vifs, arrondis ou chanfreinés), disque, anneau, oblong, équerre en L, U, trapèze, polygone régulier ; **platine** à perçages en grille ou sur un cercle ; **bride** avec cercle de perçage ; **flasque** (alésage + deux cercles de perçage).
- **Cotes normalisées** : bride EN 1092-1 (DN × PN, type 01 plate à souder ou 05 pleine) et rondelles ISO 7089 / 7091 / 7093 / 7094. Les valeurs sont dans la table *Production > Cotes normalisées* (modifiable, avec l'action « Marquer comme vérifié avec la norme »).
- **Les cotes livrées ne sont pas vérifiées** : brides PN10 / PN16 de DN10 à DN300 saisies de mémoire de la norme, rondelles reprises du paquet bd_warehouse (Apache-2.0). Un avertissement jaune reste affiché dans la bibliothèque tant qu'une ligne n'est pas marquée vérifiée : contrôlez-les avec la norme en vigueur avant toute production.
- **Modifier la forme** : une pièce paramétrique garde sa recette (`PieceDecoupe.parametres_forme`). Le bouton « Modifier la forme » de sa carte recharge les cotes dans la bibliothèque ; la pièce, son article et ses réglages sont conservés, le DXF et la géométrie sont recalculés, et le nom suit la forme tant qu'il n'a pas été personnalisé.

## Imbrication à plat, chute de bout et surface consommée

- Les tôles sont dessinées **à plat** (longueur à l'horizontale), pièces vues comme dans leur fichier, origine en **bas à gauche** comme sur la machine.
- **Remplissage** : dans le sens de la longueur (chute de bout à l'extrémité, sur toute la largeur) ou de la largeur (chute en haut, sur toute la longueur). **Départ** : bas gauche (défaut), haut gauche, bas droite, haut droite ; les deux coins « retournés » (haut gauche, bas droite) miroitent les pièces et sont refusés, avec un message, si une pièce ne peut pas être retournée (gravure) — le haut droite est un simple demi-tour de la tôle. Sens et départ sont retenus avec la tôle et le format.
- La **chute de bout** (dernière feuille) est hachurée en rouge, la chute entre les pièces reste en gris. Le bilan à droite détaille : tôles complètes − chute de bout = **surface consommée**, qui sert au prix de la matière (si une part de chute récupérable est réglée, elle se déduit ensuite pour donner la surface facturée).

## Profilés (cornière, UPN, tubes) dans la bibliothèque de formes

- Groupe « Profilés (barres) » de la bibliothèque : choix de la section, longueur hors tout, coupe à chaque extrémité (droite ou biais), quantité. Chaque débit crée son **article fabriqué** et apparaît en carte modifiable sous les pièces.
- Les débits d'une même section s'**imbriquent dans les barres** (longueur de barre de la section, trait de scie, chute de tête, chute récupérable), dessinées à l'horizontale avec la chute de bout en rouge ; bilan : barres complètes − chute de bout = **longueur consommée**, × prix au mètre. Le coût est réparti entre les débits au prorata de leur longueur et suit la quantité de la ligne du devis. Pas de temps de sciage pour l'instant (matière seule).
- Catalogue : *Production > Sections de profilés* (cornières à ailes égales, UPN, tubes carrés, rectangulaires et ronds), **livré non vérifié** ; les masses des tubes sont calculées (angles vifs), à remplacer par votre base. Le **prix d'achat** vient de l'article rattaché à la section (au mètre, au kilo avec poids linéique, ou à la barre) ; l'action « Créer les articles d'achat manquants » les génère, il ne reste qu'à saisir le coût.

## Corrections

- **Champs invisibles** : « Délai » des devis (widget sans le style Unfold : vide, le champ disparaissait), listes « Taux de TVA » des lignes et adresse associée des contacts. Un test parcourt toutes les fiches d'ajout et échoue si un champ saisissable n'a pas le style Unfold.
- **Taux de marge** : libellés « (%) » et aide (20 = prix de vente × 1,20).
- **Laser** : les vitesses ne sont jamais « calculées depuis l'usinabilité » ; les épaisseurs absentes du tableau du constructeur (0,5 mm) sont désormais « extrapolées du tableau du constructeur » (migration des 12 lignes concernées). La fiche d'un paramètre laser ne montre plus les réglages propres au jet d'eau (usinabilité, modes de perçage…), et inversement.
- **Chiffrage des pièces** : la gamme est alimentée dès que le paramètre a un poste de travail, au lieu d'afficher « poste de travail à renseigner » pour un poste déjà renseigné ; le message indique la vraie raison.

## Taille des vues d'imbrication

Au-dessus des imbrications, le sélecteur « Imbrications par ligne » (1, 2, 3 ou 4) règle le nombre de feuilles côte à côte sur la largeur de l'écran ; le choix est mémorisé dans le navigateur. À partir de 2 par ligne, le bilan passe sous les feuilles ; sur un écran étroit (moins de 900 px) l'affichage revient à une feuille par ligne.

## Adresses et contact du devis : jamais vidés par un faux changement de client

Le script de la fiche devis (et de la commande) vidait l'adresse de facturation, l'adresse de livraison et le contact à chaque événement « change » du champ Client, puis ne proposait que les adresses « principales » du tiers. Un « change » parasite (initialisation du sélecteur, extension du navigateur, gestionnaire de mots de passe) effaçait donc des adresses choisies à la main. Le script compare maintenant le client au dernier client connu et ne fait rien si le client n'a pas réellement changé.

## Référence de la pièce = nom du DXF, miniatures au PDF

- L'article fabriqué créé avec une pièce porte désormais comme **référence le nom du fichier DXF** (sans l'extension), avec le même nom en libellé. Les pièces de la bibliothèque de formes et les débits de profilés prennent leur nom (« Bride DN50 PN16 type 01 »). Les caractères interdits dans une URL (`/ ? # :`…) sont remplacés par « - » ; si la référence existe déjà, « -2 », « -3 »… est ajouté. La codification « Article » et l'ancien « <devis>-P01 » ne servent plus que si le nom est inutilisable. Les articles déjà créés ne sont pas renommés (menu Articles).
- Le **PDF du devis** affiche une miniature de chaque pièce (contour et trous ; vue de côté avec coupes pour un profilé) dans une colonne à gauche de la désignation, seulement si le devis contient des pièces.
- Le message « Aucune tôle correspondante » signifie qu'aucun article **matière première** n'a la matière (ou une nuance de sa famille) **et** l'épaisseur de la pièce ; il liste maintenant ce qu'il faut créer et les épaisseurs déjà en base pour la matière.

## Bord de tôle par procédé et épaisseur, boutons « Enregistrer » / « Enregistrer et fermer », navigation entre documents

- **Bord de tôle** (bande non découpée sur le pourtour) : plancher par procédé dans *Réglages de coupe* (jet d'eau 5 mm constant, laser 10 mm), et champ « Bord de tôle (mm) » sur chaque paramètre de coupe. Au laser le bord grandit avec l'épaisseur (jamais moins que l'épaisseur : 10 mm jusqu'à 10 mm d'épaisseur, 12 mm pour 12 mm…), comme l'écart entre pièces ; le plus grand de la règle et du paramètre est retenu. Dans l'imbrication du devis, « Bord de tôle » est **calculé automatiquement** (pastille « auto ») d'après la matière, l'épaisseur et le procédé ; saisir une valeur l'impose, vider le champ revient à l'automatique, et la valeur retenue avec la tôle est relue telle quelle. (Avant : marge fixe de 5 mm.)
- **Boutons** de toutes les fiches (gabarit commun `templates/admin/submit_line.html`) : **Enregistrer** (enregistre et reste sur le document, ancien « Enregistrer et continuer les modifications ») et **Enregistrer et fermer** (retour à la liste, ancien « Enregistrer »), à côté de « Enregistrer et ajouter un nouveau » et des actions propres à l'écran.
- **Navigation** : à droite du fil d'Ariane de chaque fiche, un menu déroulant avec champ de recherche (selon les champs de recherche de l'écran de liste : numéro, tiers…) liste les documents du même type, avec tiers, date et statut ; flèches ‹ › vers le précédent et le suivant ; ↑ ↓ Entrée au clavier ; confirmation si le document a des modifications non enregistrées. Seuls les documents que l'utilisateur peut voir, dans l'ordre de l'écran de liste ; les indices de devis remplacés ne sont pas proposés (méthode `queryset_navigation` d'un écran pour écarter d'autres lignes).

## Liens vers les éléments manquants (panneau « Pièces à découper »)

Chaque message « À compléter » ou d'élément absent renvoie vers l'endroit où le corriger : « retenez la tôle et le format » → lien vers le bloc d'imbrication du groupe (« Aller à l'imbrication ») ; poste de travail manquant → fiche du paramètre de coupe réellement utilisé par la pièce ; aucune tôle de cette matière et de cette épaisseur → « Créer la tôle » (fiche article préremplie : matière première, matière, épaisseur, unité surface) ; tôle sans coût → fiche de la tôle ; profilé sans article d'achat ou sans coût → fiche de la section ou de l'article ; pièces « à régler » ou exclues → lien vers leur carte. Les liens qui quittent la page s'ouvrent dans un nouvel onglet pour ne pas perdre le devis en cours.

## PDF dans un nouvel onglet

Tous les liens et boutons d'action dont l'adresse se termine par `…pdf/` (devis, AR et bon de préparation de commande, fiches de fabrication, bon de livraison, fiche d'ordre…) s'ouvrent dans un **nouvel onglet** : le document reste ouvert. Le bouton **« Enregistrer, valider et ouvrir le PDF »** (et ses équivalents AR, BL, fiche) réserve l'onglet au clic, enregistre et valide le document dans l'onglet courant, qui reste sur la fiche (statut à jour, messages visibles), puis le PDF s'affiche dans l'onglet réservé ; si la validation est refusée, l'onglet réservé se referme et les raisons s'affichent sur la fiche. Si le navigateur bloque l'ouverture d'onglet, l'ancien comportement (PDF dans l'onglet courant) s'applique. Script : `comptes/static/comptes/pdf_nouvel_onglet.js`.

## Factures, avoirs, relances et bons de commande fournisseur en PDF ; liens dans les messages d'erreur

- **Facture et avoir** (fiche de la facture > « PDF de la facture ») : numéro, date d'émission, date de livraison ou d'exécution, échéance (hors avoir), références commande et client, avoir sur la facture d'origine avec son motif, vendeur (en-tête et pied : forme, capital, RCS, SIRET, TVA intracommunautaire, IBAN), client (adresses de facturation et de livraison, SIRET, TVA), lignes (désignation, quantité, PU HT, taux de TVA, total HT), TVA par taux, total HT et TTC, conditions de règlement, mention d'exonération selon le régime fiscal du client (intracommunautaire : art. 262 ter I du CGI ; hors UE : art. 262 I ; exonéré), « facture acquittée le … » si payée, et les mentions de pied (escompte, pénalités de retard, indemnité de 40 €) saisies dans *Société > mentions sur les factures* (texte légal par défaut si vide). Le PDF est **refusé** si la facture n'a pas de ligne ou si son montant HT saisi diffère du total des lignes.
- **Lettre de relance** (« Lettre de relance (PDF) ») : rappel, relance ou dernière relance (niveau suivant automatique, ou `?niveau=2`), avec le texte des relances par e-mail.
- **Bon de commande fournisseur** (fiche de la commande fournisseur > « Bon de commande (PDF) ») : fournisseur, adresse de livraison, lignes (article ou désignation, quantité, PU HT, total HT), livraison souhaitée.
- **À savoir** : ces PDF portent les mentions légales d'une facture, mais **ne remplacent pas la transmission par une plateforme agréée** (réforme de la facturation électronique : facture au format structuré Factur-X/UBL/CII, transmission et e-reporting) ; l'émission, la numérotation chronologique sans trou et le verrouillage de la facture émise restent à brancher. Ces PDF ne sont pas encore personnalisables dans l'éditeur de modèles.
- **Messages d'erreur avec lien** : une erreur de chiffrage ou de production propose la page où la corriger (ex. « Aucune adresse de facturation principale pour le client… » → « Ajouter l'adresse de facturation du client » vers sa fiche ; offre expirée → la date de validité du devis ; article sans coût → sa fiche).

## Nomenclature (matière première) des pièces et débits du devis

L'article fabriqué créé depuis le devis (DXF, bibliothèque de formes ou profilé) reçoit maintenant sa **nomenclature** en plus de sa gamme : une pièce découpée consomme la **tôle** retenue à l'imbrication (ou l'unique tôle de sa matière et de son épaisseur dès que la matière et l'épaisseur sont choisies), avec son rectangle englobant ; un débit de profilé consomme l'**article d'achat de sa section**, pour sa longueur. Le verdict de la carte l'indique (« Nomenclature : 1 × TOLE-S235-3 (300 × 200 mm) », ou « plusieurs tôles possibles, retenez-en une », ou « aucune tôle… en base »). Les lignes sont créées et mises à jour sans doublon, une tôle changée à l'imbrication remplace l'ancienne, les lignes saisies à la main ne sont jamais touchées, et le chiffrage par imbrication ignore la ligne de la tôle retenue (matière jamais comptée deux fois). Les articles déjà créés reçoivent leur nomenclature au prochain recalcul du panneau.

## Facture électronique Factur-X (profil EN 16931)

- **Bouton** « Facture Factur-X (PDF + XML) » sur la fiche d'une facture ou d'un avoir : un PDF lisible (polices Bitstream Vera incorporées) qui contient, en pièce jointe `factur-x.xml`, la facture structurée au format CII — numéro, dates, vendeur et client (SIREN, TVA intracommunautaire, adresses), lignes (référence, désignation, quantité, prix unitaire HT, taux et catégorie de TVA), TVA par taux, totaux, échéance, IBAN/BIC, avoir de type 381 avec la facture d'origine et son motif. Bibliothèque `factur-x` (dans `requirements.txt`).
- **Montants** : la TVA est désormais calculée **par taux** sur la base HT totale (règle EN 16931) dans le PDF de la facture comme dans le XML ; la facture est refusée si son montant TTC saisi diffère du total ainsi calculé.
- **Catégories de TVA** : S (taux normal ou réduit), K (livraison intracommunautaire, avec mention de l'art. 262 ter I du CGI), G (exportation), E (exonéré), Z (taux zéro) d'après le régime fiscal du client.
- **Contrôles avant génération**, chacun avec un lien vers la page à corriger : raison sociale, adresse, **SIRET** et **TVA** de la société (contrôle de clé), adresse et pays de facturation du client, **SIRET du client français**, TVA du client pour une livraison intracommunautaire, facture d'origine d'un avoir. Le XML est validé contre le **schéma officiel (XSD)** avant d'être incorporé.
- **PDF/A-3** : polices incorporées, métadonnées XMP (`pdfaid` 3B + extension Factur-X), intention de sortie sRGB, XML en pièce jointe (relation « data »). **À valider avec un outil externe** (veraPDF pour le PDF/A, validateur FNFE-MPE ou Mustang pour les règles métier EN 16931 / Schematron) avant tout usage réel ; non vérifiable dans l'environnement de développement.
- **Hors périmètre** : transmission par une plateforme agréée et e-reporting, numérotation chronologique sans trou, verrouillage de la facture émise, remises/frais au niveau document, acomptes, autoliquidation en France (catégorie AE), plusieurs adresses de livraison.

## Menu court, page « Paramétrage » et accueil « Aujourd'hui » par profil

- **Menu latéral** ramené à une vingtaine d'entrées (Aujourd'hui · Ventes · Atelier · Achats et stock · Gestion · Paramétrage). Des pastilles rouges signalent ce qui demande une action (devis à relancer, factures en retard, ordres non transmis, alertes de stock, paramétrage à compléter) ; elles disparaissent à zéro.
- **Paramétrage** (`/admin/parametrage/`) : tout ce qui se règle une fois (découpe, formes et profilés, atelier, articles, stock, commercial, comptabilité, société et documents, utilisateurs) en cartes, chacune avec un état (« 285 sans poste de travail », « à jour »…). Les liens respectent les droits ; tous les écrans de réglage sont aussi trouvables par la recherche globale (Ctrl+K). Code : `comptes/parametrage.py` (les contrôles d'état `ETATS` serviront de base au futur centre « À compléter »).
- **Accueil « Aujourd'hui »** (`comptes/accueil.py`) : bande de l'avancement d'une affaire (Brouillon · Envoyé · Accepté · En fabrication · Facturé · Payé, chaque étape ouvre la liste filtrée) puis cartes d'action, les plus urgentes d'abord. Le **profil se déduit des groupes de droits** : *commercial* (Commercial, Responsable commercial), *atelier* (Atelier, Méthodes, Magasinier, Responsable stock), *comptabilité* (Facturation, Comptabilité, Achats), *direction* (Direction et administrateurs : tout). Une carte n'apparaît que si l'utilisateur a le droit d'ouvrir l'écran visé. Les anciennes tuiles de chiffres sont dans « Chiffres du mois » (repliées) et la liste des applications dans « Toutes les applications ».
- **Puces de filtre** au-dessus des listes Devis, Commandes clients et Factures (`comptes/puces.py`, `puces = [...]` sur le ModelAdmin) : un clic = un filtre usuel, avec le nombre de résultats.
- Si le stock est désactivé (`DJANGO_STOCK_ACTIF=false`), ses entrées de menu et ses cartes disparaissent.

## Fiche devis en onglets

La fiche d'un devis est découpée en trois onglets (un seul formulaire, un seul « Enregistrer ») : **Général** (client, adresses, statut, réponse du client, avec le récapitulatif des montants et des indices à droite), **Pièces et matière** (import DXF, bibliothèque de formes, profilés, imbrication) et **Lignes du devis** (avec leur nombre). L'onglet actif est mémorisé dans l'adresse (`#onglet=pieces`) et un champ en erreur ouvre automatiquement son onglet. Le panneau « Pièces et matière » rappelle les trois étapes : ajouter les pièces, choisir la matière et l'imbrication (« Retenir »), ajouter au devis. Code : `comptes/static/comptes/devis_onglets.js`, styles dans `accueil.css`.

## Centre « À compléter »

Page `/admin/a-completer/` (menu « Réglages », avec pastille) : tout ce qui manque dans les données et fausse un calcul, un document ou une facture électronique, en deux groupes — **à corriger** (société incomplète, paramètres de coupe sans poste de travail, postes sans tarif) et **à vérifier** (matières sans coût, articles fabriqués sans gamme, clients sans adresse de facturation ou sans SIRET, cotes normalisées non vérifiées). Chaque carte donne le nombre et les premiers éléments, reliés à leur fiche ; un contrôle disparaît dès que la donnée est corrigée. L'accueil affiche une carte « Données à compléter » quand il y a des éléments bloquants. Les contrôles ne sont montrés qu'à qui peut voir les données concernées. Pour en ajouter un : une fonction retournant `(total, [(libellé, url)])` et une ligne dans `CONTROLES` (`comptes/a_completer.py`).

Affecter un poste aux paramètres de coupe : sur la liste des paramètres de coupe, le filtre « Sans poste » (lien direct depuis la carte du centre « À compléter ») isole les paramètres chiffrés sans main-d'œuvre machine ; on les sélectionne (case d'en-tête puis « tout sélectionner ») et l'action « Affecter un poste de travail » les rattache en une fois. Cela change le temps machine chiffré dans les nouveaux devis et les recalculs de devis en brouillon.

## Opérations de fabrication dans les pièces du devis, gammes types

Chaque carte de pièce (onglet « Pièces et matière ») a un bloc **Opérations de fabrication** : la gamme de l'article fabriqué. L'étape de découpe est calculée depuis la pièce (étiquette « calculée », seul son temps de réglage se saisit) ; on ajoute les autres opérations (ébavurage, pliage, soudure, traitement en sous-traitance…) avec **＋ Ajouter une opération** : poste, réglage et temps par pièce (poste horaire) ou forfait par pièce (poste forfaitaire). Les flèches réordonnent, ✕ retire. Le coût par pièce est calculé avec le tarif en vigueur ; un poste sans tarif est signalé.

- **Gammes types** (Paramétrage > Atelier > Gammes types) : suites d'opérations réutilisables (« Pliage + traitement »…). Le menu « Gamme type… » les ajoute à la fin de la gamme de la pièce (copie : modifier le type ensuite ne change pas les gammes existantes). « Reprendre la gamme d'une autre pièce » copie les opérations saisies à la main d'une autre pièce du devis.
- Les opérations appartiennent à **l'article** : elles sont reprises dans les prochains devis de la pièce. **Historique par date** : modifier une opération ferme l'ancienne version (date de fin = veille) et en crée une nouvelle, donc les devis déjà établis se recalculent avec leurs anciens temps. Code : `technique/gamme_editeur.py` (service + vue JSON `admin/gamme-editeur/<article>/`) et `comptes/static/comptes/gamme_editeur.js`, conçus pour être réutilisés dans le constructeur de devis.
- Droits : voir les opérations = `technique.view_gamme` ; modifier = `technique.add_gamme` + `technique.change_gamme`.

## Tarifs dans la fiche du poste ; éditeur d'opérations dans le constructeur de devis

- **Postes de travail** : les tarifs (coût horaire, date de début, date de fin) se saisissent dans un tableau en bas de la fiche du poste ; la liste affiche le « Coût horaire actuel » (ou « aucun tarif » en rouge pour un poste horaire). L'historique est conservé (un nouveau tarif = une nouvelle ligne avec sa période ; deux périodes ne peuvent pas se chevaucher). L'ancien écran « Tarifs de poste » reste accessible par son adresse mais n'est plus dans le Paramétrage. Seuls les postes **horaires** sans tarif sont signalés dans « À compléter » (un poste forfaitaire n'a pas de coût horaire).
- **Constructeur de devis** : la saisie des étapes de gamme d'un nouvel article utilise le même éditeur d'opérations que les cartes de pièces (flèches, retrait, gammes types). Les étapes restent en mémoire et sont envoyées à la création de l'article, avec la date de création du devis comme date de début. Un temps de réglage à 0 est désormais accepté (il était pris pour « absent »).

## Chronologie d'affaire

Sur les fiches **devis** (onglet « Chronologie »), **commande** et **facture** : le fil de l'affaire, de l'offre à l'encaissement, daté et cliquable — devis et révisions (création, validation, acceptation / refus / remplacement, fin de validité), commande (créée, soldée, annulée), ordres de fabrication (lancement, échec de transmission au planning), livraisons (et annulations), factures et avoirs, relances de paiement, échéances et paiements. Le document ouvert est repéré (« vous êtes ici ») ; une ligne « Aujourd'hui » sépare le passé de ce qui est à venir (échéance de l'offre, règlement attendu, livraison prévue). Depuis n'importe quel document on retrouve la même affaire (toute la chaîne des indices du devis et ses commandes). Rien n'est stocké : la chronologie est recalculée depuis les documents et leur historique (`comptes/chronologie.py`, affichage via `ChronologieMixin`). Les dates de validation et d'acceptation d'un devis viennent de l'historique des modifications ; un devis dont l'historique n'a pas gardé ces changements n'affiche que sa création.

## Réglage machine par tôle (laser et jet d'eau)

Le temps de réglage d'une découpe est le temps de **mise en place d'une tôle** sur la machine : il s'applique **une fois par tôle** posée, quel que soit le nombre de pièces qu'elle porte, et autant de fois qu'il y a de tôles (matière, épaisseur, procédé ou machine différents = autres tôles). Avant, il était saisi sur l'étape de découpe de chaque pièce (donc compté pour chaque pièce).

- **Saisie** : « Mise en place d'une tôle (min) » sur la fiche du **poste de travail** (valeur de la machine) et, en exception, sur un **paramètre de coupe** (matière × épaisseur : tôle épaisse plus longue à poser). Vide sur le paramètre = valeur du poste.
- **Calcul** (`chiffrage/reglage.py`) : nombre de tôles de l'imbrication **retenue** de chaque groupe ; tant qu'aucune n'est retenue, de la **meilleure imbrication calculée**, avec un avertissement (« estimé »). Le coût (minutes × tarif horaire du poste) est réparti entre les pièces du groupe **au prorata de leur temps de coupe** et **inclus dans le prix des opérations** de chaque ligne de devis. Colonne informative **« dont réglage machine (HT) »** dans les lignes du devis (le détail « 2 tôles × 10 min, part 40 % » s'affiche au survol) et ligne « Mise en place machine » dans le bilan de chaque imbrication.
- L'étape de découpe de la gamme n'a plus de réglage propre (l'éditeur d'opérations l'indique : « par tôle : 10 min »). Les autres opérations (ajustage, pliage…) gardent leur réglage par lot.
- **Reprise des données** (migration) : le temps de réglage des étapes de découpe en cours devient le temps de mise en place de leur poste (valeur la plus fréquente, si le poste n'en avait pas), puis ces étapes sont remises à 0. L'historique des étapes closes n'est pas modifié.
- Limite : une ligne de commande saisie directement (sans devis) n'a pas de pièces à découper, donc pas de réglage par tôle.

## Date du jour à la création

À la création d'un devis, d'une commande ou d'une facture, la date (de création, de commande, de facturation) est pré-remplie avec la date du jour, modifiable (`comptes/date_du_jour.py`).

## Sauvegarde, vérification et transfert vers un autre NAS

Voir **`docs/SAUVEGARDE.md`** (mode d'emploi complet). En bref : `sauvegarder-nas.sh` (base + fichiers déposés, somme de contrôle, copie externe chiffrée AES-256 vers un disque USB, un dossier réseau ou un autre NAS, alerte en cas d'échec), `verifier-sauvegarde.sh` (restauration d'essai dans une base temporaire, contrôle d'âge — à planifier chaque semaine), `restaurer-nas.sh` (lit aussi les copies chiffrées, `--nouveau-nas`), `exporter-transfert.sh` / `importer-transfert.sh` (une archive chiffrée, `.env` compris, pour changer de NAS). Réglages dans `sauvegarde.conf` (modèle : `sauvegarde.conf.exemple`, conservé par `update-nas.sh`). Les scripts sont testés avec un faux Docker (`comptes/tests_sauvegarde.py`) mais **pas encore sur le vrai NAS** : faites un premier essai et une restauration d'essai.

## Constructeur de devis intégré à l'onglet « Lignes du devis »

Le constructeur n'est plus une page séparée : dans l'onglet **Lignes du devis**, le bouton **＋ Ajouter une ligne** ouvre l'assistant sous le tableau (devis en brouillon) — **Article existant** (recherche) ou **Nouvel article fabriqué** (référence, libellé, marge, **nomenclature**, **opérations** avec gammes types), **Aperçu du prix** (même calcul que le devis, rien n'est créé) puis **Ajouter la ligne** (article, nomenclature, gamme et ligne créés ensemble ou pas du tout). L'ancienne adresse `/constructeur/` redirige vers l'onglet.
- Le tableau des lignes ne sert plus à en ajouter (plus de ligne vide ni de lien « Ajouter ») : il modifie ou retire les lignes existantes. Sous le tableau, chaque **ligne fabriquée** se déplie pour modifier la **nomenclature** et les **opérations** de son article (partagées avec les autres devis qui l'utilisent ; tant que le devis n'est pas validé).
- **Devis pas encore enregistré** : cliquer sur « Pièces et matière » ou « Lignes du devis » enregistre le devis (en restant sur la fiche) puis ouvre l'onglet. Les boutons du bas ne changent pas (le bouton « Enregistrer et ouvrir le constructeur » a disparu).
- Code : `chiffrage/templates/admin/chiffrage/devis/_constructeur.html` et `_lignes_detail.html`, `chiffrage/static/chiffrage/devis_builder.js`, `comptes/static/comptes/nomenclature_editeur.js`, `technique/nomenclature_editeur.py`.

## Imbrication lourde : délai du serveur

Une imbrication de grosses quantités (ex. 120 pièces + 30 autres, vraie forme) peut durer plusieurs dizaines de secondes sur un NAS modeste. Gunicorn coupait auparavant toute requête au bout de **30 s** : le navigateur recevait alors une page HTML et affichait « Imbrication : Unexpected token '<' … is not valid JSON ». Le délai est désormais de **300 s** (réglable avec `GUNICORN_TIMEOUT` dans le `.env`), le panneau n'empile plus les calculs (le plus récent remplace le précédent) et un message clair s'affiche si le serveur ne répond pas. Pour accélérer : réduire les quantités le temps de la saisie, ou décocher l'imbrication à la forme réelle. En cas de doute, `docker compose logs web` montre « WORKER TIMEOUT » quand un calcul a été coupé.

Accélération de l'imbrication à la forme réelle : le tassement des pièces ne relit plus les rectangles englobants des voisines à chaque essai (calculés une fois, filtre vectorisé) ; même résultat, environ **3 fois plus rapide** (cas de 150 pièces sur 6 formats : 17 s → 6 s).

## Formats de tôle : priorités, formats par matière et seuil de chutes

Pour ne pas calculer inutilement des formats que vous n'utilisez que dans des cas particuliers :
- **Formats par matière** : dans « Formats de tôle », un format peut être limité à des **familles** ou **nuances** de matière ; sans restriction, il vaut pour toutes. Un format exclu pour la matière du devis n'est jamais calculé.
- **Priorité d'utilisation** (1 en stock / usuel, 2 sur commande, 3 exceptionnel) : l'imbrication calcule d'abord les formats de **priorité 1** ; elle n'essaie le niveau suivant que si le **taux de chutes entre pièces** du meilleur format du niveau dépasse le **seuil** (menu « Imbrication : seuil de chutes », valeur unique, 20 % par défaut). Les formats non calculés sont listés « non calculé » et le bouton **Calculer tous les formats** force la comparaison complète. Le format retenu est toujours calculé. Un niveau où aucun format ne convient (trop grand pour la machine…) fait passer au suivant.
- **Taux de chutes entre pièces** = (surface consommée − surface des pièces) ÷ surface consommée ; la chute de bout de la dernière tôle, récupérée, n'y compte pas.
- Par défaut tous les formats sont de priorité 1 : rien ne change tant que vous n'avez pas réglé vos priorités. Le même calcul par niveaux sert au nombre de tôles du réglage machine (`chiffrage/reglage.py`).

## Colonne « Prochaine action » des listes

Les listes **Devis**, **Commandes clients**, **Livraisons** et **Factures** ont une colonne « Prochaine action » : ce qu'il reste à faire sur le document, avec une couleur (rouge : en retard ; orange : à faire ; bleu : en attente ; grisé : rien). Exemples : devis en brouillon → « Ajouter des lignes » / « Terminer et valider le devis » ; devis envoyé → « Relancer le client avant le 14/10 », « Offre expirée : relancer ou refuser » ou « Attendre la réponse » ; devis accepté → « Créer la commande » ; commande → « Créer les ordres de fabrication », « Créer la livraison », « Préparer la facture » ; facture → « Envoyer un rappel / la relance / la dernière relance (échéance dépassée) » ou « Attendre le règlement (échéance 30/10) » ; payée, avoir, refusé, remplacé → « — ». La colonne se masque ou se déplace comme les autres (« Mes colonnes »). Règles : `comptes/prochaine_action.py` (fonctions `action_*`, en lecture seule).

## Paramètres de coupe en grille

Bouton **« Vue en grille »** en haut de la liste des paramètres de coupe (et lien dans le Paramétrage) : une ligne par famille ou nuance de matière, une colonne par épaisseur, pour un procédé (et un gaz au laser). Chaque case montre, au choix, le **poste de travail**, la **vitesse de production**, la **mise en place d'une tôle** ou l'**origine des vitesses**, avec une couleur : vert renseigné, bleu vitesses estimées, orange à compléter (mise en place absente), rouge **sans poste**, case pointillée « ＋ » = pas de paramètre (un clic ouvre le formulaire de création prérempli). Un clic sur une case ouvre la fiche du paramètre. En tête de page, le compteur « N sans poste » et le formulaire **Affecter un poste à** : toute la grille, une matière (ligne) ou une épaisseur (colonne) — une confirmation est demandée ; la sélection suit les filtres affichés (procédé, gaz). L'affectation demande le droit de modifier les paramètres ; en lecture seule, la grille s'affiche sans le formulaire. Code : `decoupe/grille_parametres.py`.

## Imbrication sur plusieurs cœurs

Quand plusieurs formats de tôle sont à comparer (« Calculer tous les formats », ou plusieurs formats de même priorité), ils se calculent **en parallèle**, chacun dans un processus, donc sur plusieurs cœurs du processeur (`decoupe/services/parallele.py`) : cas de 150 pièces sur 6 formats, 4 cœurs : **5,5 s → 1,6 s** (×3,4), résultats identiques au calcul séquentiel. Réglage : `ERP_IMBRICATION_PROCESSUS` dans le `.env` — 0 ou absent = automatique (cœurs − 1, plafonné à 6 : un cœur reste libre pour la base de données et le système) ; 1 = pas de parallélisme. Le calcul ne touche jamais à la base de données ; si les processus sont inutilisables (mémoire, processus tué), l'ERP retombe seul sur le calcul séquentiel. Avec un seul format à calculer, il n'y a rien à répartir : le gain vient alors des priorités de formats. Plusieurs utilisateurs qui calculent en même temps se partagent les cœurs. Prévoir environ 100 à 200 Mo de mémoire par processus de calcul.

## Relecture avant production

Voir `docs/RELECTURE_PRODUCTION.md` : corrections faites (droits de l'assistant « Ajouter une ligne », droits de la chronologie), réglages à faire dans le `.env`, limites connues (facture électronique non validée, estimations laser/jet d'eau, sauvegarde non essayée sur le NAS) et liste de contrôle du jour J.

## Montants TTC : TVA par taux, plus d'écart d'un centime

Le TTC d'une facture se calcule désormais **comme dans le PDF et le fichier Factur-X** (norme EN 16931) : la TVA est calculée **par taux sur la base HT totale** puis arrondie, et non ligne par ligne. Avant, la somme des TTC de lignes arrondies une à une pouvait différer d'un centime du total (exemple : 29,63 € HT → 35,55 € au lieu de 35,56 €), ce qui bloquait Factur-X (« le montant TTC saisi diffère du total des lignes »). Le calcul par taux sert maintenant au TTC calculé de la facture (avoirs signés), au pré-remplissage de la fiche facture et à l'**écriture comptable** (le TTC du compte client est celui de la facture ; le centime d'arrondi va au compte de vente le plus important du taux). Pour corriger une facture existante (non verrouillée), cocher la facture dans la liste et lancer l'action **« Recalculer les montants depuis les lignes »**. Les devis et commandes gardent leur calcul par ligne (un prix unitaire ligne par ligne y est normal) ; la facture est le document légal.

## Imbrication en quinconce et règles de création de tôle

**Quinconce** : pour un lot d'un seul modèle de pièce (3 pièces ou plus), l'imbrication selon la forme essaie aussi un **réseau régulier** : rangées au pas minimal, chaque rangée décalée d'une fraction de pas (une demi-pièce pour des ronds), rangées rapprochées au maximum, plusieurs orientations. Il est comparé au placement pièce à pièce et le meilleur est gardé ; le contrôle exact des contours (écart entre pièces, zone utile) s'applique aux deux. Exemple : 20 brides Ø165 sur 2500 × 1250 (bord 10, écart 10) : rangées de 7, 6 et 7, longueur entamée 489 mm au lieu de 547 mm (-11 %), sans surcoût de calcul. Un glissement en diagonale dans le tassement a aussi été essayé : moins de 1 % de gain pour un calcul 2 à 3 fois plus long, il n'est pas retenu.

**Règles de création de tôle** (Paramétrage → Articles et matières → *Règles de création de tôle*) : quand l'imbrication d'un devis ne trouve aucune tôle pour une matière et une épaisseur, le panneau propose **« Créer cette tôle »** si une règle s'applique. Une règle porte son périmètre (toutes les matières, une famille ou une nuance, avec une plage d'épaisseurs facultative), des modèles de référence (`TOLE-{matiere}-{epaisseur}`) et de libellé, l'unité et le coût d'achat (par exemple 1,20 €/kg), la TVA et les réglages de stock. La plus précise l'emporte (nuance, puis famille, puis toutes ; à égalité la plage d'épaisseurs la plus étroite). Une référence déjà prise reçoit un suffixe -2, -3… Sans règle, le message propose d'en définir une (pré-remplie avec la matière) ou de saisir la tôle à la main. Sans coût dans la règle, la tôle est créée sans prix et le coût matière reste à renseigner. Droits : « ajouter un article » est nécessaire.

## Préparer la mise en production (`preparer_production`)

Commande de gestion qui vide les documents de test (devis et révisions, commandes, livraisons, ordres de fabrication, factures et avoirs, écritures comptables, achats, sous-traitance, pièces à découper et imbrications) avec leur historique, et remet leurs compteurs de codification à zéro. Elle **conserve** les données de référence : tiers, adresses, contacts, articles, matières, postes et tarifs, gammes types, paramètres de coupe, formats et règles de tôle, plan comptable, société, modèles de documents, utilisateurs, et le stock (sauf `--stock`).

Sans option, elle ne fait que **lister** : `docker compose exec web python manage.py preparer_production`. Pour supprimer : faire `./sauvegarder-nas.sh`, puis `... preparer_production --confirmer --sauvegarde-faite` et taper SUPPRIMER (ou `--oui`). Options : `--stock`, `--articles-fabriques` (articles créés depuis les devis, avec nomenclature et gamme), `--journaux` (journal des connexions). Tout se fait dans une transaction : en cas d'erreur, rien n'est supprimé. Les fichiers déposés (`media`) ne sont pas touchés. Tests : `comptes/tests_preparer_production.py`.

## Coût d'achat d'une matière première : au kilo, au m² ou à l'unité

L'unité de coût d'un article acheté se choisit en clair : **Au kilo (€/kg)**, **Au m² (€/m²)**, **Au mètre (€/m)** ou **À l'unité (€/pièce)**. Le libellé du champ « Coût unitaire » suit le choix (« Coût unitaire (€/kg) »…). Pour une tôle : au kilo, elle est valorisée d'après sa densité et son épaisseur ; au m², directement ; à l'unité, c'est le prix de la feuille entière, réparti sur sa surface. Le calcul de l'équivalent (prix au m² ↔ au kg) reste affiché sous le coût. Les règles de création de tôle utilisent les mêmes unités. Aucune donnée n'est modifiée : seuls les libellés changent.

## Renommer un article

Sur la fiche d'un article, le bouton **« Renommer »** change sa référence. La référence est la clé de l'article : la modifier directement dans le formulaire créerait un second article, elle est donc **en lecture seule** une fois l'article créé. Le renommage recrée l'article sous la nouvelle référence, repointe tout ce qui s'y rattache (nomenclatures, gammes, lignes de devis et de commande, ordres de fabrication, stock, fournisseurs, comptes d'article, pièces à découper, profilés) ainsi que l'historique de ces objets, puis supprime l'ancien. Refus si la référence est vide, identique, trop longue ou déjà prise. Un article « -COPIE » (créé par « Dupliquer et modifier ») se renomme ainsi.

## Modification et création par lots

**Modifier par lots** : dans les listes **Articles, Matières, Postes de travail, Formats de tôle, Tiers, Comptes d'articles (vente et achat), Comptes de tiers**, sélectionnez des lignes puis l'action *« Modifier par lots… »* : cochez les champs à changer et leur nouvelle valeur (remplacer, augmenter de x %, ajouter x), un **aperçu avant → après** s'affiche pour chaque ligne (cas impossibles ou invalides signalés, rien n'est écrit), puis *Appliquer*.
- **Articles** : prix d'achat (dans l'unité de l'article, en €/kg ou en €/m², converti avec l'épaisseur et la densité), unité de coût (€/kg ↔ €/m² en gardant le prix), marge, TVA, géré en stock, stock minimum, réapprovisionnement. Actions voisines : *Ajouter une gamme type…* (aux articles fabriqués sélectionnés) et *Affecter un compte de vente ou d'achat…* (avec code analytique).
- **Matières** : densité, usinabilité, famille. **Postes** : marge, mise en place d'une tôle, nombre de machines. **Formats de tôle** : priorité, actif, familles et nuances. **Tiers** : conditions de paiement, devise, régime fiscal.
- **Avec effet à une date** (*« Modifier avec effet à une date… »*) pour ce qui a un historique : **tarifs d'achat fournisseur** (hausse de x %, frais de port), **coût horaire des postes** (depuis la liste des postes) et **gammes** (poste, temps de réglage, temps par pièce). L'ancienne ligne est close la veille, une nouvelle commence à la date d'effet : l'historique et les anciens devis restent intacts. Les étapes de découpe (calculées depuis la pièce) sont ignorées.
- **Journal et annulation** : chaque lot est tracé dans *Lots de modifications* (Paramétrage → Articles et matières) avec les anciennes valeurs ; l'action *Annuler les lots sélectionnés* rétablit ce qui n'a pas changé depuis et supprime les objets créés qui ne sont utilisés nulle part.

**Base matières rapide** (liste des Matières → *Base matières rapide*) : cochez des nuances du catalogue (aciers, inox, aluminium, cuivre, laiton, ou les vôtres en collant « nuance ; densité ; prix »), cochez des épaisseurs (ou une série : courantes, fines, fortes), validez : **tout se crée à la suite sans autre intervention** — la matière si elle n'existe pas, une tôle par épaisseur (référence et libellé d'après vos modèles, prix d'achat au kilo ou au m²), le **paramètre de coupe jet d'eau** estimé d'après l'usinabilité de la famille (copié de l'épaisseur la plus proche), et une règle de création de tôle par nuance. Ce qui existe déjà est ignoré et signalé. Au laser, une épaisseur absente du tableau du constructeur n'est pas réalisable : aucun paramètre n'est créé.

**Importer des tiers** (liste des Tiers → *Importer des tiers*) : collez un tableau ou choisissez un CSV (`code ; raison_sociale ; type ; siret ; numero_tva ; regime_fiscal ; conditions_paiement ; devise`) ; un code déjà pris est ignoré, le lot est annulable.

## Grille des paramètres de coupe : créer sans ouvrir la fiche

Au **jet d'eau**, cliquer sur un **＋** de la grille crée tout de suite le paramètre, sans ouvrir sa fiche : copie du paramètre le plus proche en épaisseur de la même matière (mêmes réglages, perçage proportionnel à l'épaisseur), **vitesses estimées d'après l'usinabilité**, et le **poste choisi dans « Affecter un poste à… → »** (à défaut, celui du modèle). Maj + clic ouvre la fiche comme avant. Le bouton **« Créer les manquants »** de la barre d'affectation fait la même chose pour une ligne, une colonne ou toute la grille. Les cases impossibles (matière sans usinabilité, par exemple) sont signalées avec leur motif et le reste est créé ; chaque création est tracée dans *Lots de modifications* (annulable). **Laser** : les épaisseurs absentes du tableau du constructeur ne sont pas réalisables, le ＋ ouvre donc toujours la fiche.

## Schéma des cotes d'une section de profilé

Sur la fiche d'une **section de profilé** (Socle technique → Sections de profilés), un schéma des cotes suit la famille choisie : cornière (a, b, e), UPN (h, b, tw, tf), tube carré (c, e), tube rectangulaire (h, b, e), tube rond (d, e), avec la légende des cotes à saisir et un exemple (`{"a": 20, "b": 20, "e": 3}`).

## Sections de profilés : dans le socle technique

Les **sections de profilés** (cornières, UPN, tubes) sont une forme particulière de matière première : elles ont quitté l'administration « Chiffrage découpe » pour le **Socle technique**, à côté des articles (menu *Gestion → Sections de profilés*, et Paramétrage → Articles et matières). Les données (cotes, masses, articles d'achat rattachés) et les droits déjà donnés aux groupes sont conservés : la table est renommée et le type de contenu d'administration suit (migrations `technique 0019-0020`, `decoupe 0034`). L'ancienne adresse `/admin/decoupe/profilesection/` n'existe plus : c'est `/admin/technique/profilesection/`.

## Catalogue de profilés d'un petit atelier (acier, alu, inox)

Le catalogue des **sections de profilés** (Socle technique → Sections de profilés) couvre : cornières à ailes égales et inégales, UPN, IPE, HEA, HEB, tubes carrés, rectangulaires et ronds, **plats**, **ronds pleins** et **carrés pleins** (710 sections).
- **Masses du catalogue ArcelorMittal 2020** : 626 sections reprennent la masse linéique du catalogue général (acier, kg/m), avec la page en « Source » et la coche « Vérifié ». Chaque ligne extraite a été **contrôlée par la géométrie** (aire × 7,85 kg/dm³ : 2 % pour les sections qui se calculent, 4 % pour les cornières à congés) : les coquilles du catalogue sont écartées (par exemple plat 35×18 : 2,94 kg/m au lieu de 4,95 ; carré 5 : 0,126 au lieu de 0,196 ; l'IPE 500 sans masse). L'outil d'extraction (`docs/outils/extraire_catalogue_arcelor.py`) et les données (`decoupe/profiles_catalogue_arcelor.py`) sont dans le dépôt.
- **Le reste** (dimensions absentes du catalogue) garde une masse **calculée** et reste « non vérifié » : tubes carrés et rectangulaires formés à froid (EN 10219, rayons d'angle 2 t / t, qui retrouvent le catalogue à moins de 2 %), tubes ronds (EN 10220), plats et pleins (aire exacte), cornières inégales sans congé. Les valeurs de départ saisies auparavant concordaient avec le catalogue à moins de 1 %.
- **Alu et inox** : la masse saisie est celle de l'acier ; l'aluminium (≈ 2,70) et l'inox (≈ 7,90) s'en déduisent par la densité (colonne « Alu / inox » ; le prix au mètre d'un débit utilise la densité de la matière de l'article d'achat). Le catalogue confirme cette règle : plat alu 40×5 = 0,54 kg/m pour 1,57 en acier. Les masses inox des catalogues peuvent s'écarter de quelques % (rayons de congé différents).
- Une migration complète le catalogue sans toucher à ce qui est **vérifié** ou saisi à la main. Chaque fiche exige ses cotes (schéma et liste affichés selon la famille).
