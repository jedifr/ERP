# Site client DÉCOUPE H2O

Configurateur de pièces en ligne : le client choisit une pièce plane (bibliothèque de formes), un profil plié ou une boîte pliée, la matière, l'épaisseur et la quantité ; le prix HT s'affiche aussitôt avec l'aperçu à plat. Le panier part à l'ERP comme **demande web** (Paramétrage → Vente en ligne → Demandes web) : le commercial la vérifie et confirme commande, port et délai. Pas de paiement en ligne pour l'instant.

Pendant la saisie, un **schéma coté** dessine la pièce à l'échelle (`public/schemas.js`) : chaque cote porte une lettre (A, B, C…), la même que devant le champ à remplir. Forme plane : contour, perçages, diamètres, entraxes, angle du premier trou. Profil : la section avec ses faces et ses angles. Boîte : le flan vu de dessus, bords rabattus à plat. Ce schéma est calculé dans le navigateur, sans appel à l'ERP ; si la bibliothèque de formes gagne une forme, ajoutez son dessin dans `schemas.js` et rafraîchissez `test/catalogue-formes.json` (copie du catalogue de l'API).

L'onglet **Vue 3D** montre la pièce en volume (on la fait tourner à la souris ou au doigt) : la pièce plane est le contour chiffré par l'ERP extrudé à l'épaisseur, le profil sa section extrudée à la longueur, la boîte son fond et ses bords relevés à l'angle saisi (`public/vue3d.js`). Elle utilise three.js 0.160 (licence MIT), copié dans `public/vendor/` pour rester sans dépendance ni CDN, et ne se télécharge qu'au premier clic sur l'onglet.

L'ERP reste le seul moteur de prix : ce serveur ne calcule rien, il relaie trois appels vers l'API web de l'ERP (`/api/web/v1/catalogue/`, `prix/`, `demandes/`) en y ajoutant la clé, qui ne part jamais dans le navigateur. Il limite les calculs par visiteur et met le catalogue en cache 5 minutes.

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
