# ERP maison — Spécification fonctionnelle complète (4 phases)

Document de référence consolidant l'ensemble de la conception : socle technique, chiffrage, planning, commercial, stock, achats, sous-traitance et pilotage. Destiné à servir de cahier des charges pour le développement, phase par phase.

## Contexte et objectif

Le projet vise à construire un ERP maison, compatible avec l'outil de planification d'atelier existant (fraisage/tournage, déployé sur NAS Synology), en remplacement progressif d'Herakles. La facturation légale reste externalisée vers une plateforme agréée (Tiime), conformément à la réforme de facturation électronique.

Le périmètre se découpe en 4 phases construites par dépendances :

1. **Socle technique** — postes, articles, nomenclatures, gammes
2. **Chiffrage et planning** — devis, ordonnancement, lien avec le planning atelier
3. **Commercial et stock** — tiers, stock, pont de facturation
4. **Achats et pilotage** — fournisseurs, sous-traitance, indicateurs

---

# Phase 1 — Socle technique

### MATIERE
Référentiel des matières (acier, aluminium, inox...), centralise la densité.

| Champ | Type | Description |
|---|---|---|
| nom | string (PK) | Nom de la matière |
| densite | float | kg/dm³, utilisée pour le calcul au poids |

### ARTICLE
Table unique portant deux natures, différenciées par `nature`.

| Champ | Type | Description |
|---|---|---|
| reference | string (PK) | |
| nature | string | `matiere_premiere` ou `fabrique` |
| matiere | FK → MATIERE | Pertinent pour tôles/profilés |
| unite_cout | string | `surface`, `longueur`, `poids` ou `piece` |
| epaisseur | float | Tôle |
| type_profil | string | Tube carré, rectangulaire, cornière, I, U |
| poids_lineique | float | kg/mètre (profilés vendus au poids) |
| cout_unitaire | float | Coût d'achat (matière première) |
| taux_marge_defaut | float | Marge par défaut sur le coût matière (articles fabriqués) |
| gere_en_stock | bool | Indépendant de `nature` — vrai par défaut pour une matière première, faux par défaut pour un fabriqué, modifiable au cas par cas |
| stock_mini | float | Seuil d'alerte de réapprovisionnement |
| quantite_reappro | float | Quantité suggérée à commander |

**Distinction clé :** une matière première (achetée) n'a pas de gamme et n'est jamais margée pour elle-même. Un produit fabriqué porte sa propre `NOMENCLATURE` et sa propre `GAMME` ; son coût n'est pas stocké mais recalculé à chaque devis.

### POSTE_TRAVAIL
Un centre de charge logique (ex. "Mazak"), même si plusieurs machines identiques le composent.

| Champ | Type | Description |
|---|---|---|
| nom | string (PK) | Mazak, Priminer, Tour, Fil, Ajustage, Laser, Jet d'eau, Chaudronnerie, Sous-Traitance... |
| type_operation | string | |
| mode_calcul | string | `horaire` ou `forfaitaire` (sous-traitance) |
| nombre_machines | int | Capacité agrégée (usage planning) |
| taux_marge_defaut | float | Marge par défaut sur les opérations de ce poste |

### TARIF_POSTE
Historise le coût horaire — permet de recalculer un ancien devis avec les taux d'époque.

| Champ | Type | Description |
|---|---|---|
| poste | FK → POSTE_TRAVAIL | |
| cout_horaire | float | €/heure |
| date_debut / date_fin | date | |

### NOMENCLATURE
Ce qu'un article fabriqué consomme.

| Champ | Type | Description |
|---|---|---|
| article_parent | FK → ARTICLE | |
| article_composant | FK → ARTICLE | |
| longueur_mm | float | Tôle (avec largeur) ou profilé (seule) |
| largeur_mm | float | Tôle uniquement |
| quantite | float | Nombre de pièces/découpes identiques |

### GAMME
Suite d'opérations (postes) suivie par un article fabriqué, historisée.

| Champ | Type | Description |
|---|---|---|
| article | FK → ARTICLE | |
| poste | FK → POSTE_TRAVAIL | |
| ordre | int | |
| temps_fixe | float | Réglage (mode horaire) |
| temps_variable | float | Temps unitaire (mode horaire) |
| cout_forfaitaire | float | Mode forfaitaire (sous-traitance) |
| date_debut / date_fin | date | Historisation de la révision |

---

# Phase 2 — Chiffrage et planning

### Moteur de chiffrage — pipeline
Ligne de devis → coût matière (article + composants via nomenclature) → coût des opérations (somme des étapes de gamme) → coût de revient → marge → prix de vente.

**Calcul d'une étape de gamme**
- Horaire : `(temps_fixe + temps_variable × quantité) × tarif du poste` (valide à la date, via TARIF_POSTE)
- Forfaitaire : `cout_forfaitaire × quantité`

**Calcul du coût matière selon `unite_cout`**
- Surface (tôle) : `longueur_mm × largeur_mm` → m² × `cout_unitaire`
- Longueur (profilé), vendu au mètre : `longueur_mm` → ml × `cout_unitaire`
- Longueur (profilé), vendu au kilo : `longueur_mm × poids_lineique` → poids × `cout_unitaire`
- Poids (tôle au kg) : `longueur × largeur × épaisseur` → volume × `densite` → poids × `cout_unitaire`
- Pièce : `quantite × cout_unitaire`

*Chutes de tôle non gérées dans cette version.*

**Marge — hiérarchie de priorité**
1. Marge globale du devis (`DEVIS.taux_marge_globale`), si renseignée
2. Sinon marge par défaut (poste pour les opérations, article pour la matière)
3. Ajustable ligne à ligne jusqu'à validation du devis

### DEVIS
| Champ | Type | Description |
|---|---|---|
| numero | string (PK) | |
| client | FK → TIERS | |
| date_creation | date | |
| statut | string | brouillon / validé |
| taux_marge_globale | float | Optionnel, écrase les marges par défaut |

### DEVIS_LIGNE
| Champ | Type | Description |
|---|---|---|
| devis | FK → DEVIS | |
| article | FK → ARTICLE | |
| quantite | float | |
| cout_matiere_calcule | float | |
| taux_marge_matiere_applique | float | Pré-rempli depuis l'article, éditable |
| prix_vente_matiere | float | |

### DEVIS_LIGNE_OPERATION
| Champ | Type | Description |
|---|---|---|
| devis_ligne | FK → DEVIS_LIGNE | |
| poste | FK → POSTE_TRAVAIL | |
| ordre | int | |
| cout_calcule | float | |
| taux_marge_applique | float | Pré-rempli depuis le poste, éditable |
| prix_vente | float | |

### Chaîne devis → production → planning
Un devis validé se transforme en commande via le bouton **"Lancer en production"**, qui crée toujours localement la commande et l'ordre de fabrication (gamme et tarifs figés), puis tente la synchronisation avec le planning atelier.

### COMMANDE
| Champ | Type | Description |
|---|---|---|
| numero | string (PK) | |
| devis | FK → DEVIS | |
| date_commande | date | |
| statut | string | |
| adresse_facturation | FK → ADRESSE | |
| adresse_livraison | FK → ADRESSE | |

### ORDRE_FABRICATION
| Champ | Type | Description |
|---|---|---|
| numero | string (PK) | |
| commande | FK → COMMANDE | |
| article | FK → ARTICLE | |
| quantite | float | |
| date_lancement | date | |
| statut | string | Statut de production |
| statut_synchro | string | `synchronise`, `en_attente`, `echec_persistant` |
| nombre_tentatives | int | |
| date_derniere_tentative | date | |

### OPERATION_OF
| Champ | Type | Description |
|---|---|---|
| ordre_fabrication | FK → ORDRE_FABRICATION | |
| poste | FK → POSTE_TRAVAIL | Point de jonction avec le planning atelier |
| ordre | int | |
| temps_prevu | float | Copié de la gamme au lancement |
| temps_reel | float | Alimenté par le planning atelier via synchronisation |
| quantite_bonne | float | Alimenté par le planning atelier |
| quantite_rebut | float | Alimenté par le planning atelier |
| statut | string | |

### Architecture de synchronisation avec le planning atelier
**Choix retenu :** deux bases distinctes, synchronisées via API — approche prudente qui préserve le planning en production. Fusion ultérieure possible mais constitue un projet de migration à part entière. L'ERP est la **source de vérité unique** pour les postes et leurs tarifs.

**Flux :** ERP → Planning (ordres de fabrication) ; Planning → ERP (avancement, temps réel, quantités).

**Gestion des échecs :** la création de l'OF ne dépend jamais de la disponibilité du planning.
- Échec → `statut_synchro = en_attente`, alerte visible
- Tentative automatique en arrière-plan toutes les 15 min, jusqu'à 5 tentatives (valeurs par défaut ajustables)
- Au-delà : `statut_synchro = echec_persistant`
- Bouton "Resynchroniser" manuel toujours disponible, remet le compteur à zéro
- **Prérequis technique impératif :** l'appel API doit être idempotent (pas de doublon en cas de rejeu)

---

# Phase 3 — Commercial et stock

### TIERS
Entité unique pour client et/ou fournisseur (un même acteur peut être les deux).

| Champ | Type | Description |
|---|---|---|
| code | string (PK) | |
| raison_sociale | string | |
| type_tiers | string | client / fournisseur / les_deux |
| siret | string | Obligatoire pour la facturation électronique |
| numero_tva | string | TVA intracommunautaire |
| conditions_paiement | string | Valeur par défaut, reprise sur devis/commande |

### ADRESSE
Un tiers peut avoir plusieurs adresses de facturation et plusieurs adresses de livraison.

| Champ | Type | Description |
|---|---|---|
| id | string (PK) | |
| tiers | FK → TIERS | |
| type_adresse | string | facturation / livraison |
| libelle | string | Ex. "Siège", "Entrepôt Nord" |
| adresse / code_postal / ville | string | |
| est_principale | bool | Adresse par défaut proposée |

`COMMANDE` référence directement les adresses retenues (`adresse_facturation`, `adresse_livraison`) pour chaque transaction.

### CONTACT
| Champ | Type | Description |
|---|---|---|
| tiers | FK → TIERS | |
| nom / prenom | string | |
| email / telephone | string | |
| fonction | string | |

### Stock — LOT et mouvements
Principe évolutif : chaque article a un lot unique aujourd'hui (par article entier) ; passer à plusieurs lots par article (chutes, longueurs restantes) ne demandera aucune refonte du modèle, seulement la création de lots supplémentaires. Seuls les articles avec `gere_en_stock = vrai` sont concernés (matières premières par défaut, produits fabriqués sur option).

**EMPLACEMENT**

| Champ | Type | Description |
|---|---|---|
| code | string (PK) | |
| libelle | string | |

**LOT**

| Champ | Type | Description |
|---|---|---|
| id | string (PK) | |
| article | FK → ARTICLE | |
| emplacement | FK → EMPLACEMENT | |
| quantite | float | |
| longueur_restante | float | Inutilisé en v1, prêt pour l'évolution |
| statut | string | |

**MOUVEMENT_STOCK**

| Champ | Type | Description |
|---|---|---|
| id | string (PK) | |
| lot | FK → LOT | |
| type_mouvement | string | |
| quantite | float | |
| date_mouvement | date | |
| reference_origine | string | Pointe vers l'OF, la commande fournisseur, etc. |

### ALERTE_STOCK
Historique des franchissements de seuil, une seule alerte active à la fois par article.

| Champ | Type | Description |
|---|---|---|
| id | string (PK) | |
| article | FK → ARTICLE | |
| date_declenchement | date | |
| statut | string | active / traitee |
| date_traitement | date | Clôture auto (stock remonté) ou manuelle (commande fournisseur passée) |

### Pont de facturation vers Tiime
La facture légale vit dans Tiime (plateforme agréée, conforme à la réforme). `FACTURE` reste une trace côté ERP.

| Champ | Type | Description |
|---|---|---|
| numero | string (PK) | |
| commande | FK → COMMANDE | Une commande peut générer plusieurs factures |
| reference_tiime | string | |
| montant_ht / montant_ttc | float | |
| date_facturation | date | |
| statut_paiement | string | |
| mode_creation | string | `manuel` aujourd'hui, `automatique` si une API Tiime est confirmée plus tard |

Flux retenu pour démarrer : facturation créée manuellement dans Tiime, référence renseignée ensuite dans l'ERP.

---

# Phase 4 — Achats et pilotage

### Achats fournisseurs
Le fournisseur vient de `TIERS`. Livraisons partielles gérées via une entité de réception distincte.

**COMMANDE_FOURNISSEUR**

| Champ | Type | Description |
|---|---|---|
| numero | string (PK) | |
| fournisseur | FK → TIERS | |
| date_commande / date_livraison_prevue | date | |
| statut | string | |

**LIGNE_COMMANDE_FOURNISSEUR**

| Champ | Type | Description |
|---|---|---|
| commande_fournisseur | FK | |
| article | FK → ARTICLE | |
| alerte_stock_origine | FK → ALERTE_STOCK | Nullable — clôture l'alerte à la commande |
| quantite_commandee | float | |
| prix_unitaire_achat | float | |
| quantite_recue | float | Cumul recalculé depuis les réceptions |

**RECEPTION**

| Champ | Type | Description |
|---|---|---|
| numero | string (PK) | |
| commande_fournisseur | FK | |
| date_reception | date | |

**RECEPTION_LIGNE**

| Champ | Type | Description |
|---|---|---|
| reception | FK | |
| ligne_commande_fournisseur | FK | |
| quantite_recue | float | Génère un MOUVEMENT_STOCK en entrée |

### Sous-traitance — logistique
Distincte du chiffrage (poste "Sous-Traitance" en mode forfaitaire, déjà modélisé en phase 1). Ici, on trace physiquement l'envoi et le retour des pièces.

**ENVOI_SOUS_TRAITANCE**

| Champ | Type | Description |
|---|---|---|
| numero | string (PK) | |
| operation_of | FK → OPERATION_OF | |
| sous_traitant | FK → TIERS | |
| date_envoi | date | |
| quantite_envoyee | float | |
| statut | string | |

**RETOUR_SOUS_TRAITANCE**

| Champ | Type | Description |
|---|---|---|
| numero | string (PK) | |
| envoi | FK → ENVOI_SOUS_TRAITANCE | Retours partiels possibles |
| date_retour | date | |
| quantite_retournee | float | |
| conforme | bool | Contrôle qualité simple |

Le retour clôture l'opération de gamme correspondante, permettant à l'OF de passer à l'étape suivante.

### Pilotage
**Marge réelle vs prévue** : le prix de vente du devis reste figé. On recalcule le coût réel avec les mêmes formules que le chiffrage, à partir des données réelles remontées par le planning atelier (`temps_reel`, `quantite_bonne`, `quantite_rebut` sur `OPERATION_OF`), puis on compare à la marge prévue au devis.

**Taux de charge des postes** : ne nécessite pas de nouvelle table — agrégation du temps réel cumulé par poste sur une période, rapporté à la capacité disponible (`POSTE_TRAVAIL.nombre_machines`).

---

# Points encore ouverts

- **Chutes de tôle** : non gérées. Évolution possible via `LOT.longueur_restante`, déjà prévu dans le modèle.
- **Facturation automatisée (option B)** : à confirmer directement auprès de Tiime (aucune API publique documentée trouvée à ce jour).
- **Non-conformités en sous-traitance** : traitement simple (`conforme` booléen) pour démarrer, gestion plus fine envisageable plus tard.
- **Clôture manuelle des alertes de stock** hors commande fournisseur (ex. rupture acceptée) : non traitée pour l'instant.
