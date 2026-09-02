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
