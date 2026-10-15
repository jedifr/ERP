# Site client DÉCOUPE H2O

Configurateur de pièces en ligne : le client choisit une pièce plane (bibliothèque de formes), un profil plié ou une boîte pliée, la matière, l'épaisseur et la quantité ; le prix HT s'affiche aussitôt avec l'aperçu à plat. Le panier part à l'ERP comme **demande web** (Paramétrage → Vente en ligne → Demandes web) : le commercial la vérifie et confirme commande, port et délai. Pas de paiement en ligne pour l'instant.

L'ERP reste le seul moteur de prix : ce serveur ne calcule rien, il relaie trois appels vers l'API web de l'ERP (`/api/web/v1/catalogue/`, `prix/`, `demandes/`) en y ajoutant la clé, qui ne part jamais dans le navigateur. Il limite les calculs par visiteur et met le catalogue en cache 5 minutes.

## Lancer

Node 20 ou plus, aucune dépendance.

```sh
cp .env.example .env   # renseigner ERP_URL et ERP_WEB_CLE_API (même clé que dans l'ERP)
set -a; . ./.env; set +a
npm start              # http://localhost:3000
npm test
```

Ou avec Docker : `docker build -t site-client . && docker run --env-file .env -p 3000:3000 site-client`.

## Hébergement

Le site s'héberge hors du NAS (VPS, PaaS) derrière HTTPS. Il joint l'ERP par un tunnel sortant depuis l'atelier (Cloudflare Tunnel, Tailscale…) : aucun port n'est ouvert vers le NAS, et seule l'API web (clé, débit limité) est utilisable. Côté ERP : définir `ERP_WEB_CLE_API`, cocher « vendu en ligne » sur les tôles proposées et renseigner les postes de travail (paramètres de coupe, plieuses).
