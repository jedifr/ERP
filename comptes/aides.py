"""Mode d'emploi court affiché en haut des listes de paramétrage (un encadré repliable par section) : à quoi sert la section, comment s'en servir
en quelques étapes, ce qu'il faut savoir. Clé : « app.modèle » (même identifiant que la page Paramétrage). Voir templates/admin/change_list.html."""

AIDES = {
    # ------------------------------------------------------------------ découpe
    "decoupe.parametrecoupe": {
        "resume": "Un paramètre de coupe donne la vitesse de coupe d'une matière à une épaisseur, pour un procédé (laser ou jet d'eau). Il sert à calculer le temps de coupe de chaque pièce du devis.",
        "etapes": [
            "Pour voir tout d'un coup : bouton « Vue en grille » (matières en lignes, épaisseurs en colonnes).",
            "Jet d'eau : cliquez sur un ＋ de la grille, le paramètre se crée tout de suite (vitesses estimées d'après la matière).",
            "Laser : importez le fichier materials.lua (« Importer materials.lua ») ; une épaisseur absente du tableau du constructeur n'est pas réalisable.",
            "Un paramètre « sans poste » n'apporte pas de main-d'œuvre machine au devis : affectez-lui son poste (action « Affecter un poste de travail »).",
        ],
        "bon_a_savoir": ["Plusieurs matières ou épaisseurs d'un coup : « Base matières rapide » (liste des Matières) crée aussi les paramètres jet d'eau."],
    },
    "decoupe.reglageprocede": {
        "resume": "Réglages propres à chaque procédé : pondération des vitesses du laser et écart minimal entre deux pièces à l'imbrication.",
        "etapes": ["Une seule ligne par procédé : ouvrez-la et modifiez la valeur.", "L'écart entre pièces s'applique à toutes les imbrications du procédé."],
        "bon_a_savoir": ["Une pondération de 0,85 signifie que le laser coupe à 85 % de la vitesse du constructeur dans les calculs."],
    },
    "decoupe.formattole": {
        "resume": "Les formats de tôle (largeur × longueur) que vous achetez. L'imbrication essaie ces formats pour trouver le moins coûteux.",
        "etapes": [
            "Ajoutez un format (par exemple 1500 × 3000) avec sa priorité : 1 = en stock ou usuel, 2 = sur commande, 3 = exceptionnel.",
            "Au besoin, limitez un format à certaines familles ou nuances (ex. 1250 × 2500 pour l'inox). Vide = toutes les matières.",
            "Plusieurs formats à changer d'un coup : cochez-les puis « Modifier par lots… ».",
        ],
        "bon_a_savoir": ["L'imbrication calcule d'abord les formats de priorité 1 et n'essaie les autres que si les chutes dépassent le seuil des réglages d'imbrication."],
    },
    "decoupe.reglageimbrication": {
        "resume": "Le seuil de chutes (20 % par défaut) : tant que les chutes entre pièces restent dessous, l'imbrication s'arrête aux formats les plus usuels.",
        "etapes": ["Ouvrez l'unique ligne et changez le seuil si vous voulez que d'autres formats soient essayés plus souvent."],
        "bon_a_savoir": [],
    },
    "decoupe.imbricationjob": {
        "resume": "L'historique des simulations d'imbrication lancées depuis les pièces. Consultation seulement.",
        "etapes": ["Ouvrez une simulation pour revoir les feuilles et les pourcentages de chutes."],
        "bon_a_savoir": ["Le chiffrage d'un devis se fait dans l'onglet « Pièces et matière » du devis, pas ici."],
    },
    # ------------------------------------------------------------------ formes
    "decoupe.normecote": {
        "resume": "Les cotes normalisées de la bibliothèque de formes (brides, platines…). Elles alimentent les formes paramétriques des devis.",
        "etapes": ["Contrôlez une ligne avec la norme ou le catalogue, puis cochez « Vérifié » (ou action « Marquer comme vérifié avec la norme »)."],
        "bon_a_savoir": ["Les lignes non vérifiées sont signalées dans « À compléter »."],
    },
    "decoupe.profilimportdecoupe": {
        "resume": "Un profil d'import dit comment lire vos fichiers DXF : quels calques sont des contours, des gravures ou des plis.",
        "etapes": ["Créez un profil par habitude de dessin (bureau d'études ou client).", "Ajoutez ses règles : un calque ou un motif de nom de calque, et son rôle.", "Choisissez ce profil à l'import d'une pièce dans le devis."],
        "bon_a_savoir": [],
    },
    # ------------------------------------------------------------------ atelier
    "technique.postetravail": {
        "resume": "Un poste de travail est un centre de charge : laser, jet d'eau, ajustage, fraisage… Son coût horaire valorise les opérations de fabrication.",
        "etapes": [
            "Créez le poste : mode « horaire » (temps × coût horaire) ou « forfaitaire » (prix par pièce, pour la sous-traitance).",
            "Ajoutez son tarif dans le tableau « Tarifs » (coût horaire et date de début) : l'historique permet de recalculer un ancien devis avec les prix de l'époque.",
            "Une hausse du coût horaire : cochez les postes, action « Modifier le coût horaire avec effet à une date (hausse) ».",
        ],
        "bon_a_savoir": ["Un poste horaire sans tarif chiffre ses opérations à zéro : il est signalé dans « À compléter »."],
    },
    "technique.gamme": {
        "resume": "La gamme est la suite d'opérations de fabrication d'un article (découpe, ajustage, fraisage…), avec leurs temps. Chaque modification garde l'ancienne version datée.",
        "etapes": [
            "Le plus simple : modifiez la gamme depuis le devis (ligne dépliable « Opérations de fabrication ») ou la fiche de l'article.",
            "Ici, pour corriger plusieurs étapes d'un coup : cochez-les puis « Modifier avec effet à une date ».",
        ],
        "bon_a_savoir": ["Les étapes de découpe sont calculées depuis la pièce : elles ne se modifient pas à la main."],
    },
    "technique.gammetype": {
        "resume": "Une gamme type est une suite d'opérations réutilisable (« Pliage + soudure », « Ébavurage + traitement »…) que l'on ajoute en un clic à un article.",
        "etapes": ["Créez la gamme type et ses étapes (poste, temps de réglage, temps par pièce).", "Dans un devis, ajoutez-la à la gamme d'un article (menu « Gamme type… » de l'éditeur d'opérations), ou à plusieurs articles : liste des Articles, action « Ajouter une gamme type »."],
        "bon_a_savoir": ["Les étapes sont copiées : modifier ensuite la gamme type ne change pas les gammes déjà créées."],
    },
    # ------------------------------------------------------------------ articles et matières
    "technique.matiere": {
        "resume": "Les matières (nuances) que vous travaillez : S235, 1.4307, 5754… avec leur densité et leur famille.",
        "etapes": [
            "Plusieurs matières, plusieurs épaisseurs, à la volée : bouton « Base matières rapide » (en haut de la liste).",
            "Une seule matière : « Ajouter ». La famille (Acier, Inox, Aluminium…) se déduit du nom si vous la laissez vide.",
            "Plusieurs densités ou familles à changer : cochez-les puis « Modifier par lots… ».",
        ],
        "bon_a_savoir": ["La densité sert au calcul des poids et des prix au kilo ; la famille donne les paramètres de coupe."],
    },
    "technique.famillematiere": {
        "resume": "Une famille regroupe des nuances (Acier, Inox, Aluminium…) : elles partagent leurs paramètres de coupe et leur usinabilité.",
        "etapes": ["Créez la famille avec son usinabilité (sert à estimer les vitesses de coupe du jet d'eau).", "Dans « Mots-clés des nuances », listez les débuts de noms rattachés (s235, s355, acier…) : une nouvelle nuance rejoint sa famille toute seule."],
        "bon_a_savoir": ["L'action « Rattacher les matières sans famille » range les matières existantes."],
    },
    "technique.profilesection": {
        "resume": "Le catalogue des profilés (cornières, UPN, IPE, tubes, plats, ronds, carrés) avec leurs cotes et leur masse au mètre. Il sert aux débits de profilés des devis.",
        "etapes": [
            "Chaque ligne reprend le catalogue ArcelorMittal 2020 (« Vérifié » coché, page en « Source »).",
            "Pour un profilé absent : « Ajouter », choisissez la famille (un schéma indique les cotes à saisir) et la masse linéique de l'acier.",
            "Rattachez un article d'achat à la section (action « Créer les articles d'achat manquants ») puis saisissez son prix au kilo ou au mètre.",
        ],
        "bon_a_savoir": ["La masse saisie est celle de l'acier ; alu et inox s'en déduisent par la densité (colonne « Alu / inox »)."],
    },
    "technique.reglecreationtole": {
        "resume": "Une règle de création de tôle dit comment fabriquer automatiquement l'article « tôle » : son nom, l'unité du prix (kg, m²…), le prix d'achat, la TVA.",
        "etapes": [
            "Créer plusieurs tôles d'un coup : ne passez pas par ici. Ouvrez la liste des Matières puis « Base matières rapide » : cochez les nuances, les épaisseurs et les formats, validez. Tôles, matières, paramètres de coupe jet d'eau et règles se créent à la suite.",
            "Une tôle manquante pendant un devis : dans l'imbrication, le bouton « Créer cette tôle » applique la règle qui convient.",
            "Pour écrire une règle vous-même : choisissez à qui elle s'applique (toutes les matières, une famille ou une nuance, une plage d'épaisseurs), le modèle de nom, l'unité et le prix.",
        ],
        "bon_a_savoir": [
            "Le nom suit votre convention « Nuance - largeur x longueur x épaisseur » (S235 - 1500 x 3000 x 3) grâce aux variables {matiere}, {largeur}, {longueur}, {epaisseur}.",
            "La règle la plus précise l'emporte : nuance, puis famille, puis toutes les matières. Sans prix dans la règle, la tôle est créée sans prix : il reste à le saisir.",
        ],
    },
    "technique.nomenclature": {
        "resume": "La nomenclature liste les composants d'un article fabriqué (tôle découpée, visserie…) avec leurs dimensions et quantités. Elle donne le coût matière.",
        "etapes": ["Le plus simple : modifiez-la depuis le devis (ligne dépliable « Nomenclature ») ou la fiche de l'article.", "Chaque ligne : le composant, ses longueur et largeur (pour une tôle ou un profilé) et la quantité par pièce."],
        "bon_a_savoir": ["Un composant sans coût d'achat bloque le chiffrage : il est signalé dans « À compléter »."],
    },
    "achats.articlefournisseur": {
        "resume": "Le lien entre un de vos articles et un fournisseur : sa référence et sa désignation chez lui.",
        "etapes": ["Choisissez l'article et le fournisseur, saisissez sa référence.", "Ajoutez ensuite ses tarifs (menu Tarifs d'achat) : le prix en vigueur sert aux commandes fournisseur."],
        "bon_a_savoir": [],
    },
    "achats.tarifachatarticle": {
        "resume": "Le prix d'achat d'un article chez un fournisseur, avec sa période de validité.",
        "etapes": ["Un nouveau tarif : ajoutez-le avec sa date de début (l'ancien se clôt avec une date de fin).", "Une hausse d'un fournisseur : cochez ses tarifs puis « Modifier avec effet à une date » (par exemple +3 % au 1er du mois), l'historique est conservé."],
        "bon_a_savoir": [],
    },
    "comptes.lotmodification": {
        "resume": "Le journal de toutes les modifications et créations faites « par lots » : qui, quand, combien d'objets.",
        "etapes": ["Pour défaire un lot : cochez-le puis l'action « Annuler les lots sélectionnés »."],
        "bon_a_savoir": ["L'annulation ne rétablit que ce qui n'a pas changé depuis ; les objets créés encore utilisés ailleurs sont conservés."],
    },
    # ------------------------------------------------------------------ stock
    "stock.emplacement": {
        "resume": "Les endroits où se trouve le stock (rayonnage, atelier, extérieur).",
        "etapes": ["Créez un emplacement par zone de rangement.", "Les mouvements et les transferts s'y rattachent."],
        "bon_a_savoir": [],
    },
    "stock.mouvementstock": {
        "resume": "Chaque entrée, sortie ou ajustement de stock. Consultation : le stock se calcule par la somme des mouvements.",
        "etapes": ["Les réceptions et les sorties les créent automatiquement ; pour une correction, utilisez un inventaire."],
        "bon_a_savoir": [],
    },
    "stock.transfert": {
        "resume": "Un transfert déplace du stock d'un emplacement à un autre.",
        "etapes": ["Choisissez le lot, l'emplacement d'arrivée et la quantité."],
        "bon_a_savoir": [],
    },
    # ------------------------------------------------------------------ commercial
    "commercial.tauxtva": {
        "resume": "Les taux de TVA proposés sur les articles, devis et factures (20 %, 10 %, 5,5 %).",
        "etapes": ["Créez chaque taux une seule fois et cochez « par défaut » pour celui des nouvelles lignes."],
        "bon_a_savoir": ["Un client exonéré, intracommunautaire ou hors UE applique automatiquement 0 %, quel que soit le taux de l'article."],
    },
    "commercial.conditionpaiement": {
        "resume": "Les conditions de paiement proposées aux clients (« 30 jours net », « 45 jours fin de mois »…). Elles donnent la date d'échéance des factures.",
        "etapes": ["Créez la condition avec son libellé et son nombre de jours.", "Rattachez-la à un client (fiche du client) ou à plusieurs : liste des Tiers, « Modifier par lots… »."],
        "bon_a_savoir": [],
    },
    "commercial.delaipropose": {
        "resume": "Les délais de livraison que l'on peut proposer sur un devis (« 2 semaines »…).",
        "etapes": ["Ajoutez un délai par habitude ; il est proposé dans la liste du devis."],
        "bon_a_savoir": [],
    },
    "commercial.devise": {
        "resume": "Les devises pour les clients ou fournisseurs hors zone euro.",
        "etapes": ["Ajoutez la devise avec son code ISO (USD, GBP…) et son symbole."],
        "bon_a_savoir": ["L'euro existe déjà."],
    },
    "commercial.pays": {
        "resume": "La liste des pays des adresses. Le pays de facturation du client est obligatoire pour la facture électronique.",
        "etapes": ["Ajoutez un pays absent avec son code ISO à 2 lettres, et cochez « UE » s'il en fait partie."],
        "bon_a_savoir": [],
    },
    "commercial.adresse": {
        "resume": "Les adresses de facturation et de livraison des clients et fournisseurs.",
        "etapes": ["Le plus simple : ajoutez-les depuis la fiche du tiers.", "Cochez facturation et/ou livraison, et « principale » pour celle proposée d'office."],
        "bon_a_savoir": [],
    },
    "commercial.contact": {
        "resume": "Les personnes à contacter chez un client ou un fournisseur.",
        "etapes": ["Ajoutez un contact avec son tiers, ses coordonnées et ses téléphones."],
        "bon_a_savoir": [],
    },
    # ------------------------------------------------------------------ comptabilité
    "comptabilite.journalcomptable": {
        "resume": "Les journaux regroupent les écritures par nature : ventes, achats, banque, opérations diverses.",
        "etapes": ["Les journaux courants existent déjà ; ajoutez-en un par compte bancaire si besoin."],
        "bon_a_savoir": ["Le journal des ventes reçoit les écritures générées depuis les factures."],
    },
    "comptabilite.comptecomptable": {
        "resume": "Le plan comptable : tous les comptes utilisables dans les écritures.",
        "etapes": ["Importez le plan comptable officiel (commande « importer_pcg ») plutôt que de les saisir un à un.", "Ajoutez seulement vos comptes particuliers."],
        "bon_a_savoir": [],
    },
    "comptabilite.postegestion": {
        "resume": "Un poste de gestion choisit le compte de vente selon le régime fiscal du client (France, export, intracommunautaire).",
        "etapes": ["Créez le poste, indiquez le compte pour chaque régime, puis rattachez-le à des articles (Comptes de vente d'article)."],
        "bon_a_savoir": [],
    },
    "comptabilite.codeanalytique": {
        "resume": "Un code analytique suit une activité ou une affaire dans la comptabilité (laser, jet d'eau, sous-traitance…).",
        "etapes": ["Créez le code, puis affectez-le aux articles : liste des Articles, action « Affecter un compte de vente ou d'achat »."],
        "bon_a_savoir": [],
    },
    "comptabilite.articlecomptevente": {
        "resume": "Le compte de vente (et le code analytique) d'un article : où ses ventes sont comptabilisées.",
        "etapes": ["Un article sans ligne ici utilise le compte de vente par défaut.", "Plusieurs articles d'un coup : liste des Articles, action « Affecter un compte de vente ou d'achat »."],
        "bon_a_savoir": [],
    },
    "comptabilite.articlecompteachat": {
        "resume": "Le compte d'achat (et le code analytique) d'un article : où ses achats sont comptabilisés.",
        "etapes": ["Plusieurs articles d'un coup : liste des Articles, action « Affecter un compte de vente ou d'achat » (choisir « Achat »)."],
        "bon_a_savoir": [],
    },
    "comptabilite.tierscomptecomptable": {
        "resume": "Le compte comptable d'un client (411…) ou d'un fournisseur (401…) quand il a le sien.",
        "etapes": ["Sans ligne ici, le compte client ou fournisseur par défaut est utilisé."],
        "bon_a_savoir": [],
    },
    "comptabilite.parametrescomptables": {
        "resume": "Les comptes et journaux utilisés par défaut pour générer les écritures de ventes.",
        "etapes": ["Ouvrez l'unique fiche et choisissez le journal des ventes et les comptes par défaut (client, vente, TVA collectée)."],
        "bon_a_savoir": ["Sans ces choix, l'action « Générer l'écriture » d'une facture est impossible."],
    },
    "comptabilite.parametresexportcomptable": {
        "resume": "Le format du fichier d'export des écritures pour votre comptable.",
        "etapes": ["Ouvrez l'unique fiche, choisissez le format et le séparateur demandés par votre comptable."],
        "bon_a_savoir": [],
    },
    # ------------------------------------------------------------------ société et documents
    "comptes.societe": {
        "resume": "Les informations de votre entreprise : elles figurent en en-tête de tous les PDF et dans la facture électronique.",
        "etapes": ["Ouvrez l'unique fiche et complétez raison sociale, adresse, SIRET, TVA intracommunautaire, IBAN.", "Elle doit être complète avant d'émettre de vraies factures."],
        "bon_a_savoir": ["Un SIRET ou une TVA invalide bloque la facture électronique."],
    },
    "documents.modeledocument": {
        "resume": "Les modèles de documents PDF (devis, commande, facture…) que l'on peut personnaliser.",
        "etapes": ["Ouvrez un modèle pour l'éditer visuellement ; les champs entre accolades sont remplis par l'ERP."],
        "bon_a_savoir": ["Réservé aux administrateurs."],
    },
    "codification.reglecodification": {
        "resume": "Les règles de numérotation des documents : préfixe, nombre de chiffres, remise à zéro annuelle (DEV-00001, FAC-00001…).",
        "etapes": ["Modifiez le préfixe ou la largeur du numéro : le prochain code proposé est affiché en exemple."],
        "bon_a_savoir": ["Changer le compteur en cours de route peut créer des trous ou des doublons de numéros."],
    },
    # ------------------------------------------------------------------ utilisateurs
    "auth.user": {
        "resume": "Les comptes des personnes qui utilisent l'ERP.",
        "etapes": ["Créez le compte, puis cochez son ou ses groupes : ils donnent ses droits (commercial, atelier, comptabilité, direction)."],
        "bon_a_savoir": ["« Audit des droits » (en haut de la liste) montre ce que chaque personne peut faire."],
    },
    "auth.group": {
        "resume": "Un groupe rassemble des droits (voir, ajouter, modifier, supprimer) pour un métier.",
        "etapes": ["Les groupes par défaut existent déjà : adaptez leurs droits plutôt que d'en créer de nouveaux."],
        "bon_a_savoir": ["Réservé aux administrateurs."],
    },
    "comptes.evenementconnexion": {
        "resume": "Le journal des connexions : réussites, échecs, verrouillages. Consultation seulement.",
        "etapes": ["Un compte verrouillé après des échecs : cochez sa ligne puis « Débloquer les comptes sélectionnés »."],
        "bon_a_savoir": [],
    },
}


def aide_pour(opts):
    """Aide de la section (modèle `opts`) ou None."""
    return AIDES.get(f"{opts.app_label}.{opts.model_name}")
