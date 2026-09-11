(function () {
    "use strict";

    // Glisser-déposer natif (HTML5 drag & drop, pas de librairie externe) sur
    // le tableau "Lignes de devis" de la fiche Devis : persiste l'ordre dans
    // DevisLigne.ordre (poignée invisible aux yeux de Django — un simple
    // champ caché rempli ici, jamais affiché ni saisi à la main). Cet ordre
    // est ensuite repris tel quel à la création de la commande
    // (lancer_en_production itère devis.lignes.all(), trié par ordre).
    document.addEventListener("DOMContentLoaded", init);

    function init() {
        const table = document.querySelector("#lignes-data");
        if (!table) {
            return;
        }
        preparer(table);
        renumeroter(table);
        document.addEventListener("formset:added", (event) => {
            if (event.detail && event.detail.formsetName && event.detail.formsetName !== "lignes") {
                return;
            }
            preparer(table);
            renumeroter(table);
        });
    }

    function lignes(table) {
        return Array.from(table.querySelectorAll(":scope > tbody.form-group:not(.empty-form)"));
    }

    function preparer(table) {
        lignes(table).forEach((tbody) => {
            if (tbody.dataset.reorderPret) {
                return;
            }
            tbody.dataset.reorderPret = "1";
            tbody.draggable = true;
            tbody.addEventListener("dragstart", onDragStart);
            tbody.addEventListener("dragover", onDragOver);
            tbody.addEventListener("drop", onDrop);
            tbody.addEventListener("dragend", onDragEnd);

            const premiereCellule = tbody.querySelector("td");
            if (premiereCellule) {
                const poignee = document.createElement("span");
                poignee.className =
                    "poignee-glisser-ligne cursor-grab select-none mr-2 text-font-subtle-light dark:text-font-subtle-dark";
                poignee.textContent = "⠿";
                poignee.title = "Glisser pour réordonner les lignes";
                premiereCellule.prepend(poignee);
            }
        });
    }

    let ligneEnCours = null;

    function onDragStart(event) {
        // Un glisser démarré depuis un champ de saisie (texte, select...) est
        // annulé : laisse le comportement natif du champ (sélection de
        // texte, focus) plutôt que de déplacer la ligne entière. Démarrer
        // depuis la poignée ou une cellule vide reste, lui, autorisé.
        if (event.target.closest("input, select, textarea, button, a")) {
            event.preventDefault();
            return;
        }
        ligneEnCours = event.currentTarget;
        event.dataTransfer.effectAllowed = "move";
        // Firefox exige un setData pour autoriser le drag.
        event.dataTransfer.setData("text/plain", "");
        ligneEnCours.classList.add("opacity-50");
    }

    function onDragOver(event) {
        if (!ligneEnCours) {
            return;
        }
        event.preventDefault();
        if (ligneEnCours === event.currentTarget) {
            return;
        }
        const cible = event.currentTarget;
        const rect = cible.getBoundingClientRect();
        const avant = event.clientY - rect.top < rect.height / 2;
        cible.parentElement.insertBefore(ligneEnCours, avant ? cible : cible.nextSibling);
    }

    function onDrop(event) {
        event.preventDefault();
    }

    function onDragEnd(event) {
        event.currentTarget.classList.remove("opacity-50");
        const table = event.currentTarget.closest("table");
        if (table) {
            renumeroter(table);
        }
        ligneEnCours = null;
    }

    function renumeroter(table) {
        // Une ligne "extra" jamais remplie (aucun article choisi) ne doit
        // jamais recevoir d'ordre : Django la considérerait alors comme
        // modifiée (son champ ordre diffère de son état initial) et
        // exigerait qu'elle soit intégralement remplie (article, quantité)
        // — même défaut de fond que la ligne de téléphone vide corrigée
        // précédemment, ici réintroduit si on numérote sans discernement.
        let index = 0;
        lignes(table).forEach((tbody) => {
            const articleSelect = tbody.querySelector('select[name$="-article"]');
            if (!articleSelect || !articleSelect.value) {
                return;
            }
            const champOrdre = tbody.querySelector('input[name$="-ordre"]');
            if (champOrdre) {
                champOrdre.value = String(index);
            }
            index += 1;
        });
    }
})();
