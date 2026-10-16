# Site client DÉCOUPE H2O

Configurateur de pièces en ligne : le client choisit une pièce plane (bibliothèque de formes), un profil plié, une boîte pliée, une forme libre ou envoie son fichier DXF, la matière, l'épaisseur et la quantité ; le prix HT s'affiche aussitôt avec l'aperçu à plat. Le panier part à l'ERP comme **demande web** (Paramétrage → Vente en ligne → Demandes web) : le commercial la vérifie et confirme commande, port et délai. Pas de paiement en ligne pour l'instant.

Pendant la saisie, un **schéma coté** dessine la pièce à l'échelle (`public/schemas.js`) : chaque cote porte une lettre (A, B, C…), la même que devant le champ à remplir. Forme plane : contour, perçages, diamètres, entraxes, angle du premier trou. Profil : la section avec ses faces et ses angles. Boîte : le flan vu de dessus, bords rabattus à plat. Ce schéma est calculé dans le navigateur, sans appel à l'ERP ; si la bibliothèque de formes gagne une forme, ajoutez son dessin dans `schemas.js` et rafraîchissez `test/catalogue-formes.json` (copie du catalogue de l'API).

Sur un écran large (1400 px et plus), la page occupe toute la largeur et la place gagnée va au schéma et à la vue 3D ; les cotes du schéma gardent la même taille à l'écran.

L'onglet **Vue 3D** montre la pièce en volume (on la fait tourner à la souris ou au doigt) : la pièce plane est le contour chiffré par l'ERP extrudé à l'épaisseur, le profil sa section extrudée à la longueur, la boîte son fond et ses bords relevés à l'angle saisi (`public/vue3d.js`). Elle utilise three.js 0.160 (licence MIT), copié dans `public/vendor/` pour rester sans dépendance ni CDN, et ne se télécharge qu'au premier clic sur l'onglet.

La **forme libre** (`public/editeur.js`) part d'une plaque rectangle ou disque ; le client y ajoute des trous ronds, oblongs, rectangles ou polygones, en découpe ou en ajout de matière, saisis au clavier (centre X / Y depuis le coin bas gauche) ou glissés sur le schéma (au millimètre, Maj pour le dixième), avec annuler / rétablir (Ctrl+Z / Ctrl+Y). Le navigateur ne fait que dessiner : l'ERP reconstruit la pièce, la contrôle et la chiffre. L'onglet **Fichier** envoie un DXF ou un SVG (et un DWG si l'ERP a un convertisseur : la liste vient du catalogue) à l'ERP (1,5 Mo au plus) ; ses cotes hors tout (A, B) se modifient pour mettre la pièce à l'échelle, qui le lit, le garde pour la fabrication et renvoie le contour affiché en schéma et en 3D.

Le même onglet accepte un **dessin ou une photo** (PNG, JPG…) dès que l'ERP lit le SVG : `public/vectoriseur.js` le vectorise dans le navigateur (seuil de contraste, inversion, lissage, trait grossi, largeur voulue de la pièce). La règle de découpe est appliquée d'office : ni jour ni trait plus fin que l'épaisseur de la tôle choisie (fermeture puis ouverture morphologiques), et seul le plus grand morceau d'un seul tenant est gardé (les îlots tomberaient à la découpe ; le client en est averti). Le contour part à l'ERP en SVG coté en mm, par le même dépôt qu'un fichier du client : l'ERP le contrôle, le garde pour la fabrication et le chiffre ; il est refait et renvoyé si l'épaisseur change. Aucun envoi de l'image elle-même.

L'ERP reste le seul moteur de prix : ce serveur ne calcule rien, il relaie ses appels vers l'API web de l'ERP (`/api/web/v1/catalogue/`, `prix/`, `dxf/`, `demandes/`) en y ajoutant la clé, qui ne part jamais dans le navigateur. Il limite les calculs par visiteur et met le catalogue en cache 5 minutes.

## Lancer

Node 20 ou plus, aucune dépendance.

```sh
cp .env.example .env   # renseigner ERP_URL et ERP_WEB_CLE_API (même clé que dans l'ERP)
set -a; . ./.env; set +a
npm start              # http://localhost:3000
npm test
```

Hébergement cPanel (o2switch, « Setup Node.js App ») : fichier de démarrage `app.cjs`, variables d'environnement dans l'écran de l'application. Pas à pas complet, passerelle du NAS comprise : `docs/DEPLOIEMENT_SITE_CLIENT.md`.

Ou avec Docker : `docker build -t site-client . && docker run --env-file .env -p 3000:3000 site-client`.

## Hébergement

Le site s'héberge hors du NAS (o2switch, sous-domaine de decoupe.eu). Il joint l'ERP par la passerelle Tailscale Funnel du NAS (`docker-compose.passerelle-web.yml`) : connexion sortante, aucun port ouvert sur la box, seul `/api/web/v1/` est publié (clé, débit limité). Côté ERP : définir `ERP_WEB_CLE_API`, cocher « vendu en ligne » sur les tôles proposées et renseigner les postes de travail (paramètres de coupe, plieuses).
