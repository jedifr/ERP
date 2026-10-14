/* Tableaux de l'ERP (listes et tableaux de lignes des fiches) : colonnes à afficher et largeur des colonnes.
   - Une petite icône au-dessus de chaque tableau ouvre un menu : une case par colonne, décochée = colonne masquée.
   - Chaque en-tête reçoit une poignée sur son bord droit : on la tire pour élargir ou rétrécir la colonne, un double-clic
     rend à la colonne sa largeur par défaut.
   Les choix sont personnels et enregistrés sur le serveur (comptes/colonnes.py, preferences_tableau) : ils suivent
   l'utilisateur d'un poste à l'autre. La configuration de la page arrive dans #tableaux-config (templates/admin/base_site.html).
   Une colonne masquée ici est seulement cachée à l'écran : ses champs restent dans le formulaire et sont enregistrés. */
(function () {
    "use strict";

    const MIN = 50;
    const ECRAN_LARGE = "(min-width: 1024px)"; // en dessous, Unfold affiche les lignes en fiches : pas de colonnes

    function nomColonne(th) {
        const classe = Array.from(th.classList).find((c) => c.startsWith("column-"));
        return classe ? classe.slice("column-".length) : null;
    }

    function libelle(th) {
        const copie = th.cloneNode(true);
        copie.querySelectorAll(".material-symbols-outlined, .sortoptions, .colonne-poignee, .sortpriority").forEach((e) => e.remove());
        return copie.textContent.replace(/\*/g, "").replace(/\s+/g, " ").trim();
    }

    function envoyer(config, donnees) {
        return fetch(config.url, {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json", "X-CSRFToken": config.csrf },
            body: JSON.stringify(donnees),
        }).catch(() => {
            /* hors ligne : le réglage vaut pour cette page seulement */
        });
    }

    function initTableau(config, table, reglage) {
        const id = table.id;
        const sel = (nom) => `#${CSS.escape(id)} .column-${CSS.escape(nom)}, #${CSS.escape(id)} .field-${CSS.escape(nom)}`;
        const masquees = new Set(reglage.masquees || []);
        const largeurs = Object.assign({}, reglage.largeurs || {});
        const style = document.createElement("style");
        document.head.appendChild(style);

        function rendre() {
            const regles = [];
            masquees.forEach((nom) => regles.push(`${sel(nom)} { display: none !important; }`));
            const larges = [];
            Object.entries(largeurs).forEach(([nom, px]) => {
                const th = `#${CSS.escape(id)} th.column-${CSS.escape(nom)}`;
                const td = `#${CSS.escape(id)} .field-${CSS.escape(nom)}`;
                larges.push(`${th} { width: ${px}px; min-width: ${px}px; max-width: ${px}px; white-space: normal; }`);
                // Colonne étroite : le titre passe à la ligne (3 lignes au plus, puis « … ») au lieu de déborder sur
                // la colonne voisine ; le titre complet reste en info-bulle.
                larges.push(`${th} .colonne-libelle { display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 3; overflow: hidden; overflow-wrap: anywhere; hyphens: auto; white-space: normal; min-width: 0; flex: 1 1 100%; }`);
                larges.push(`${th} > * { min-width: 0; flex-wrap: wrap; }`); // les icônes de tri passent sous le titre
                larges.push(`${td} { max-width: ${px}px; overflow: hidden; text-overflow: ellipsis; }`);
                larges.push(`${td} input, ${td} select, ${td} textarea, ${td} .select2-container { min-width: 0 !important; width: 100% !important; }`);
            });
            if (larges.length) regles.push(`@media ${ECRAN_LARGE} { ${larges.join(" ")} }`);
            style.textContent = regles.join("\n");
        }

        // Colonnes présentes dans l'en-tête, sauf celles qu'Unfold cache toujours (colonne d'ordre) ; les en-têtes de liste
        // portent « hidden lg:table-cell » (cachées seulement sur petit écran) et comptent.
        const cachee = (th) => th.classList.contains("hidden") && !th.classList.contains("lg:table-cell");
        const entetes = Array.from(table.querySelectorAll(":scope > thead th")).filter((th) => nomColonne(th) && !cachee(th));
        if (!entetes.length) return;
        const estListe = id === "result_list";

        // Colonnes proposées dans le menu.
        let choix;
        if (reglage.masquables) {
            const presentes = new Map(entetes.map((th) => [nomColonne(th), th]));
            const absentes = new Map((reglage.absentes || []).map((c) => [c.nom, c.libelle]));
            choix = reglage.masquables
                .filter((nom) => presentes.has(nom) || absentes.has(nom))
                .map((nom) => ({ nom, libelle: presentes.has(nom) ? libelle(presentes.get(nom)) : absentes.get(nom), absente: !presentes.has(nom) }));
        } else {
            choix = entetes
                .filter((th, i) => !(estListe && i === 0) && !th.classList.contains("required"))
                .map((th) => ({ nom: nomColonne(th), libelle: libelle(th), absente: false }));
        }
        choix = choix.filter((c) => c.libelle);

        // --- Largeurs ---------------------------------------------------------------------------------------------------
        function memoriserLargeurs() {
            envoyer(config, { ecran: reglage.ecran, largeurs });
        }
        entetes.forEach((th) => {
            const nom = nomColonne(th);
            th.classList.add("colonne-reglable");
            const texte = th.querySelector(".text") || th.querySelector(":scope > span > span:first-child");
            if (texte) texte.classList.add("colonne-libelle");
            if (!th.title) th.title = libelle(th);
            const poignee = document.createElement("span");
            poignee.className = "colonne-poignee";
            poignee.title = "Tirer pour régler la largeur (double-clic : largeur par défaut)";
            th.appendChild(poignee);
            poignee.addEventListener("click", (event) => event.stopPropagation()); // ne pas déclencher le tri de la liste
            poignee.addEventListener("pointerdown", (event) => {
                event.preventDefault();
                event.stopPropagation();
                const depart = event.clientX;
                const largeurDepart = th.getBoundingClientRect().width;
                poignee.setPointerCapture(event.pointerId);
                document.body.classList.add("colonne-en-reglage");
                const bouger = (e) => {
                    largeurs[nom] = Math.max(MIN, Math.round(largeurDepart + e.clientX - depart));
                    rendre();
                };
                const lacher = () => {
                    poignee.removeEventListener("pointermove", bouger);
                    poignee.removeEventListener("pointerup", lacher);
                    poignee.removeEventListener("pointercancel", lacher);
                    document.body.classList.remove("colonne-en-reglage");
                    if (largeurs[nom]) memoriserLargeurs();
                };
                poignee.addEventListener("pointermove", bouger);
                poignee.addEventListener("pointerup", lacher);
                poignee.addEventListener("pointercancel", lacher);
            });
            poignee.addEventListener("dblclick", (event) => {
                event.stopPropagation();
                delete largeurs[nom];
                rendre();
                memoriserLargeurs();
            });
        });

        // Reprise des largeurs réglées avant que le réglage ne soit enregistré sur le serveur (lignes de devis, PR #13).
        if (reglage.ecran.startsWith("chiffrage.devis:") && !Object.keys(largeurs).length) {
            try {
                const anciennes = JSON.parse(window.localStorage.getItem("devis-lignes-largeurs")) || {};
                if (Object.keys(anciennes).length) {
                    Object.assign(largeurs, anciennes);
                    memoriserLargeurs();
                }
                window.localStorage.removeItem("devis-lignes-largeurs");
            } catch (e) {
                /* stockage du navigateur indisponible : rien à reprendre */
            }
        }

        rendre();
        if (!choix.length) return;

        // --- Menu des colonnes ------------------------------------------------------------------------------------------
        const barre = document.createElement("div");
        barre.className = "tableau-outils";
        const conteneur = document.createElement("div");
        conteneur.className = "tableau-outils-conteneur";
        const bouton = document.createElement("button");
        bouton.type = "button";
        bouton.className = "tableau-outils-bouton";
        bouton.title = "Colonnes à afficher";
        bouton.setAttribute("aria-haspopup", "true");
        bouton.setAttribute("aria-expanded", "false");
        bouton.innerHTML = '<span class="material-symbols-outlined">view_column</span>';
        const menu = document.createElement("div");
        menu.className = "tableau-menu";
        menu.hidden = true;

        const titre = document.createElement("div");
        titre.className = "tableau-menu-titre";
        titre.textContent = "Colonnes à afficher";
        menu.appendChild(titre);
        const note = document.createElement("p");
        note.className = "tableau-menu-note";
        note.hidden = true;
        note.textContent = "Les colonnes réaffichées apparaîtront au prochain affichage de la page.";

        function memoriserColonnes() {
            envoyer(config, { ecran: reglage.ecran, masquees: Array.from(masquees) });
        }
        choix.forEach((colonne) => {
            const ligne = document.createElement("label");
            ligne.className = "tableau-menu-case";
            const caseACocher = document.createElement("input");
            caseACocher.type = "checkbox";
            caseACocher.checked = !masquees.has(colonne.nom);
            caseACocher.addEventListener("change", () => {
                if (caseACocher.checked) masquees.delete(colonne.nom);
                else masquees.add(colonne.nom);
                if (colonne.absente && caseACocher.checked) note.hidden = false;
                rendre();
                memoriserColonnes();
            });
            const texte = document.createElement("span");
            texte.textContent = colonne.libelle;
            ligne.append(caseACocher, texte);
            menu.appendChild(ligne);
        });
        menu.appendChild(note);

        const actions = document.createElement("div");
        actions.className = "tableau-menu-actions";
        const toutAfficher = document.createElement("button");
        toutAfficher.type = "button";
        toutAfficher.textContent = "Tout afficher";
        toutAfficher.addEventListener("click", () => {
            if (choix.some((c) => c.absente && masquees.has(c.nom))) note.hidden = false;
            masquees.clear();
            menu.querySelectorAll("input[type=checkbox]").forEach((c) => (c.checked = true));
            rendre();
            memoriserColonnes();
        });
        const largeursDefaut = document.createElement("button");
        largeursDefaut.type = "button";
        largeursDefaut.textContent = "Largeurs par défaut";
        largeursDefaut.addEventListener("click", () => {
            Object.keys(largeurs).forEach((nom) => delete largeurs[nom]);
            rendre();
            memoriserLargeurs();
        });
        actions.append(toutAfficher, largeursDefaut);
        menu.appendChild(actions);

        function fermer() {
            menu.hidden = true;
            bouton.setAttribute("aria-expanded", "false");
        }
        bouton.addEventListener("click", () => {
            menu.hidden = !menu.hidden;
            bouton.setAttribute("aria-expanded", String(!menu.hidden));
        });
        document.addEventListener("click", (event) => {
            if (!conteneur.contains(event.target)) fermer();
        });
        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape") fermer();
        });

        conteneur.append(bouton, menu);
        barre.appendChild(conteneur);
        // Au-dessus du cadre défilant du tableau, pour que l'icône reste en place quand on fait défiler les colonnes.
        const cadre = table.closest("[data-simplebar]") || table.parentElement;
        cadre.parentElement.insertBefore(barre, cadre);
    }

    function init() {
        const source = document.getElementById("tableaux-config");
        if (!source) return;
        let config;
        try {
            config = JSON.parse(source.textContent);
        } catch (e) {
            return;
        }
        Object.entries(config.tableaux || {}).forEach(([id, reglage]) => {
            const table = document.getElementById(id);
            if (table && table.tagName === "TABLE") initTableau(config, table, reglage);
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
