/* Tableau des lignes de devis : largeur des colonnes réglable à la souris.
   Chaque en-tête reçoit une poignée sur son bord droit : on la tire pour élargir ou rétrécir la colonne, un double-clic
   rend à la colonne sa largeur par défaut (devis_admin_live.css). Les largeurs choisies sont mémorisées dans ce navigateur
   (localStorage) et reprises sur tous les devis. */
(function () {
    "use strict";

    const CLE = "devis-lignes-largeurs";
    const MIN = 40;

    function lire() {
        try {
            return JSON.parse(window.localStorage.getItem(CLE)) || {};
        } catch (e) {
            return {};
        }
    }

    function ecrire(largeurs) {
        try {
            window.localStorage.setItem(CLE, JSON.stringify(largeurs));
        } catch (e) {
            /* navigation privée ou stockage bloqué : les largeurs valent pour cette page seulement */
        }
    }

    function nomColonne(th) {
        const classe = Array.from(th.classList).find((c) => c.startsWith("column-"));
        return classe ? classe.slice("column-".length) : null;
    }

    function appliquer(th, largeur) {
        if (largeur) {
            th.style.width = largeur + "px";
            th.style.minWidth = largeur + "px";
            th.style.maxWidth = largeur + "px";
        } else {
            th.style.width = th.style.minWidth = th.style.maxWidth = "";
        }
    }

    function init() {
        const table = document.getElementById("lignes-data");
        if (!table) return;
        const largeurs = lire();
        table.querySelectorAll("thead th").forEach((th) => {
            const nom = nomColonne(th);
            if (!nom) return;
            appliquer(th, largeurs[nom]);
            th.classList.add("colonne-reglable");
            const poignee = document.createElement("span");
            poignee.className = "colonne-poignee";
            poignee.title = "Tirer pour régler la largeur (double-clic : largeur par défaut)";
            th.appendChild(poignee);

            poignee.addEventListener("pointerdown", (event) => {
                event.preventDefault();
                const depart = event.clientX;
                const largeurDepart = th.getBoundingClientRect().width;
                poignee.setPointerCapture(event.pointerId);
                document.body.classList.add("colonne-en-reglage");
                const bouger = (e) => appliquer(th, Math.max(MIN, Math.round(largeurDepart + e.clientX - depart)));
                const lacher = () => {
                    poignee.removeEventListener("pointermove", bouger);
                    poignee.removeEventListener("pointerup", lacher);
                    poignee.removeEventListener("pointercancel", lacher);
                    document.body.classList.remove("colonne-en-reglage");
                    const memo = lire();
                    memo[nom] = Math.round(th.getBoundingClientRect().width);
                    ecrire(memo);
                };
                poignee.addEventListener("pointermove", bouger);
                poignee.addEventListener("pointerup", lacher);
                poignee.addEventListener("pointercancel", lacher);
            });
            poignee.addEventListener("dblclick", () => {
                appliquer(th, null);
                const memo = lire();
                delete memo[nom];
                ecrire(memo);
            });
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
