# Relecture d'ensemble avant la mise en production

Relecture faite à la version 2026.10.12.3 (suite : 1252 tests verts). Rien n'a été essayé sur le NAS UGREEN.

## 1. Corrigé pendant la relecture
| Point | Gravité | Correction |
|---|---|---|
| L'assistant « Ajouter une ligne » (ex-constructeur) n'exigeait que d'être connecté au back-office : tout utilisateur « staff » pouvait créer des articles, nomenclatures, gammes et lignes | **Élevée** | Droits exigés : modifier le devis + ajouter une ligne ; créer un article fabriqué exige aussi ajouter article, nomenclature et gamme ; devis validé = refus (409). Tests ajoutés. |
| La chronologie d'une fiche devis montrait les commandes, livraisons, **montants de factures** et relances à des utilisateurs sans droit sur ces documents | Moyenne | Chaque événement exige la permission « voir » de son document. Test ajouté. |
| Imports inutilisés dans les modules récents | Faible | Retirés. |

## 2. Vérifié, rien à signaler
- Migrations à jour (`makemigrations --check` : aucune différence), `manage.py check` sans erreur.
- Aucune vulnérabilité connue dans `requirements.txt` (`pip-audit`), aucun nom indéfini (`pyflakes`).
- Aucun secret ni `.env` dans le dépôt (`.env` et `sauvegarde.conf` sont ignorés par git).
- L'API REST est protégée par défaut (`ModelPermissionsAvecLecture`) ; les actions `IsAuthenticated` vérifient la permission dans leur code.
- Les nouvelles pages (Paramétrage, À compléter, grille, éditeurs d'opérations et de nomenclature) filtrent par droits ; les écritures exigent les permissions du modèle.

## 3. À régler AVANT la mise en production (réglages, dans le `.env` du NAS)
1. `DJANGO_SECRET_KEY` : générer une vraie clé (`python -c "import secrets; print(secrets.token_urlsafe(50))"`). Sans cela, quiconque connaît la clé par défaut peut forger une session (alerte « critique » affichée sur l'accueil aux administrateurs).
2. `DB_PASSWORD` : remplacer `erp_dev_password` (à changer aussi dans la base existante, ou installer sur une base neuve).
3. `DJANGO_ALLOWED_HOSTS` : l'adresse IP et le nom du NAS ; `DJANGO_CSRF_TRUSTED_ORIGINS` si HTTPS derrière un reverse proxy.
4. Si l'ERP est servi en HTTPS : `DJANGO_COOKIES_SECURISES=true`, puis `DJANGO_HSTS_SECONDS` quand le HTTPS est validé.
5. `GUNICORN_WORKERS=4`, `ERP_IMBRICATION_PROCESSUS` en automatique (voir README).
6. Créer les **groupes de droits** et les utilisateurs réels (commande `synchroniser_groupes` pour les groupes par défaut, puis écran Groupes ; `audit_droits` pour contrôler), changer le mot de passe de tout compte de test, supprimer les comptes de démonstration.

## 4. Limites connues (à décider ou à valider par vous)
- **Facture électronique** : le fichier Factur-X est produit (profil EN 16931) ; FAC-00001 a été **validée le 10/10/2026 par deux validateurs en ligne** (B2Brouter : XSD et règles EN 16931 CII 0/0 ; Factur-X Validator : profil EN16931, totaux 29,63 / 5,93 / 35,56). Une validation Mustang ou FNFE reste à faire sur des cas plus variés (avoir, plusieurs taux, acompte). L'**émission légale** (numérotation continue, plateforme agréée, transmission) n'est pas branchée : aujourd'hui l'ERP garde une trace, la facture légale reste faite à côté. À traiter avant d'émettre de vraies factures.
- **Laser** : le coefficient de pondération des vitesses (0,85) est une estimation à recaler avec des temps réels ; les vitesses du jet d'eau sont calculées depuis l'usinabilité (estimation) tant qu'elles ne sont pas relevées sur la machine.
- **Tables normalisées** (brides EN 1092-1, rondelles, sections de profilés) : livrées « non vérifiées » ; à contrôler avec la norme avant de produire (le centre « À compléter » les liste).
- **Sauvegarde** : scripts testés avec un faux Docker, **jamais sur le NAS** : premier passage, planification, copie externe et restauration d'essai à faire (docs/SAUVEGARDE.md).
- **Imbrication** : jusqu'à plusieurs secondes sur de grosses quantités ; priorités de formats à régler (tous en priorité 1 par défaut).
- **Performance** : les pastilles du menu et les cartes de l'accueil comptent les documents à chaque page ; sans souci à l'échelle d'un atelier, à surveiller si les factures non payées dépassent quelques centaines (le calcul des retards se fait en Python).
- **Conteneur** : l'application tourne en `root` dans Docker et sans `HEALTHCHECK` ; acceptable sur un réseau local, à durcir si l'accès est ouvert à Internet.
- Les articles saisis par l'assistant ou créés depuis une pièce DXF peuvent être modifiés par tous les devis qui les utilisent (voulu, signalé dans l'interface).

## 5. Migration vers la production (quand vous direz)
Reprendre les données de référence (clients, fournisseurs, articles, matières, postes et tarifs, gammes, paramètres de coupe, formats, plan comptable, société), supprimer les documents commerciaux de test (devis, commandes, livraisons, factures, achats, mouvements de stock) avec leur historique, remettre à zéro les compteurs de codification. Prévu : une commande `preparer_production` qui liste ce qui sera supprimé, demande confirmation et fait une sauvegarde avant. **Non développée** (en attente de votre feu vert).

## 6. Liste de contrôle du jour J
- [ ] Sauvegarde + restauration d'essai sur le nouveau NAS (`verifier-sauvegarde.sh`)
- [ ] `.env` complété (section 3), alerte « sécurité » de l'accueil vide
- [ ] Groupes de droits et utilisateurs créés ; un compte de chaque profil essayé (commercial, atelier, comptabilité, direction)
- [ ] Société complète (SIRET, TVA, IBAN) ; centre « À compléter » vide ou compris
- [ ] Un devis → commande → livraison → facture → PDF de bout en bout
- [ ] Formats de tôle : priorités et nuances réglées ; poste et mise en place des machines de découpe renseignés
- [ ] Planification des sauvegardes (DSM/UGOS ou cron) et de leur vérification hebdomadaire
