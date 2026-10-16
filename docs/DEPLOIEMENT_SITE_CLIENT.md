# Mettre en ligne le site client (o2switch + passerelle sur le NAS)

Le site client (`site-client/`) tourne chez o2switch, sur un sous-domaine de decoupe.eu. Il demande ses prix à l'ERP, qui reste sur le NAS. Entre les deux, une **passerelle Tailscale Funnel** sur le NAS :

```
Client ──► configurateur.decoupe.eu (o2switch, Node)
                 │  HTTPS + clé X-Cle-Web
                 ▼
           https://erp-web.<votre-tailnet>.ts.net/api/web/v1/   (Tailscale Funnel)
                 │  connexion sortante depuis le NAS, aucun port ouvert sur la box
                 ▼
           ERP (conteneur web, NAS)
```

Seul le chemin `/api/web/v1/` est publié : l'admin, l'API interne et les fichiers restent invisibles depuis Internet. Cette API demande la clé partagée, son débit est limité, et elle ne renvoie ni coûts ni marges.

Il faut compter trois parties : la passerelle (sur le NAS), l'ERP (paramétrage) et le site (chez o2switch).

## 1. Passerelle sur le NAS

1. Créez un compte gratuit sur tailscale.com.
2. Dans la console Tailscale, activez deux choses :
   - **DNS** : activez **HTTPS Certificates**.
   - **Access controls** : autorisez Funnel. Le fichier de règles doit contenir `"nodeAttrs": [{"target": ["autogroup:member"], "attr": ["funnel"]}]`. La console propose de l'ajouter quand on active Funnel.
3. Dans **Settings → Keys**, créez une clé d'authentification **réutilisable**.
4. Complétez le `.env` de l'ERP sur le NAS :
   ```
   ERP_WEB_CLE_API=<générer : python -c "import secrets; print(secrets.token_urlsafe(40))">
   TS_AUTHKEY=tskey-auth-...
   DJANGO_ALLOWED_HOSTS=<valeurs actuelles>,web,erp-web.<votre-tailnet>.ts.net
   ```
5. Démarrez l'ERP avec la passerelle :
   ```sh
   docker compose -f docker-compose.yml -f docker-compose.passerelle-web.yml up -d
   docker compose -f docker-compose.yml -f docker-compose.passerelle-web.yml exec passerelle-web tailscale funnel status
   ```
   La deuxième commande affiche l'adresse publique, `https://erp-web.<votre-tailnet>.ts.net`.
6. Vérifiez depuis n'importe quel ordinateur :
   - `curl -H "X-Cle-Web: <clé>" https://erp-web.<tailnet>.ts.net/api/web/v1/catalogue/` renvoie le catalogue.
   - Sans la clé, le même appel donne 403.
   - `https://erp-web.<tailnet>.ts.net/admin/` donne 404 : l'admin n'est pas publiée.

## 2. Paramétrage de l'ERP

- **Paramétrage → Vente en ligne** : réglez la marge web, le minimum de commande et la quantité max.
- Sur chaque tôle proposée en ligne (fiche article), cochez **vendu en ligne**. La tôle doit avoir sa matière, son épaisseur, son format et son coût.
- Renseignez un **poste de travail avec un tarif** :
  - sur les paramètres de coupe (laser et jet d'eau) ;
  - sur chaque plieuse (Paramétrage → Pliage).
  Sans poste, le site répond « demandez un devis ».
- **Essai du configurateur** montre le prix que verra le client.

## 3. Site chez o2switch

1. **cPanel → Domaines** : créez le sous-domaine, par exemple `configurateur.decoupe.eu`. Le certificat HTTPS (AutoSSL / Let's Encrypt) se pose tout seul après quelques minutes.
2. Déposez les fichiers du site dans un dossier hors de `public_html`, par exemple `~/site-client`. Utilisez l'archive `site-client.zip` jointe, ou `git archive HEAD:site-client -o site-client.zip`, puis le Gestionnaire de fichiers → Extraire. Il faut `package.json`, `server.js`, `app.cjs` et le dossier `public/`. Il n'y a rien à installer : le site n'a aucune dépendance.
3. **cPanel → Setup Node.js App → Create Application** :
   - Node.js version : 20 ou plus (22 si proposé) ;
   - Application mode : Production ;
   - Application root : `site-client` ;
   - Application URL : `configurateur.decoupe.eu` ;
   - Application startup file : `app.cjs` ;
   - Environment variables :
     - `ERP_URL` = `https://erp-web.<votre-tailnet>.ts.net`
     - `ERP_WEB_CLE_API` = la même clé que sur le NAS
     - `CONTACT_EMAIL` = l'adresse des demandes de devis
     - `DEBIT_PAR_MINUTE` = 60 (facultatif)
   - Enregistrez, puis cliquez sur **Start** (ou **Restart** après une mise à jour).
4. Ouvrez `https://configurateur.decoupe.eu`. Le catalogue se charge et un prix s'affiche. Envoyez une demande d'essai : elle apparaît dans l'ERP, dans Paramétrage → Vente en ligne → Demandes web.

Mise à jour du site : remplacez les fichiers, puis **Restart** dans Setup Node.js App.

## En cas de souci

| Symptôme sur le site | Cause probable |
|---|---|
| « Configurateur indisponible (ERP non configuré) » | `ERP_URL` ou `ERP_WEB_CLE_API` absent dans Setup Node.js App. |
| « Configurateur momentanément indisponible » | NAS ou passerelle arrêtés, clé différente des deux côtés, ou hôte absent de `DJANGO_ALLOWED_HOSTS` (l'ERP répond 400). |
| « Aucune matière n'est encore proposée en ligne » | Aucune tôle cochée « vendu en ligne ». |
| « Prix indisponible en ligne pour cette pièce » | Poste ou tarif manquant (paramètre de coupe ou plieuse). Le détail est dans Essai du configurateur. |


## Tiger Protect (anti-robots d'o2switch)

Tiger Protect peut répondre aux calculs de prix du navigateur (`POST /api/prix`, `/api/dxf`, `/api/demandes`) par sa page
« Test de sécurité » au lieu de laisser passer la requête : le site affiche alors « Le contrôle de sécurité de l'hébergeur
a bloqué le calcul ». Dans cPanel → **Tiger Protect**, désactivez la protection pour le sous-domaine du configurateur
(le site limite déjà les calculs par visiteur, l'ERP exige sa clé et limite son débit total).
