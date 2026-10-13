// Fiche « Optimisation de sciage » : recalcule le plan de coupe et les croquis des débits à chaque modification, sans enregistrer
// (vue decoupe/admin_views.py:previsualiser_sciage_view). Le calcul enregistré reste celui de la fiche et du PDF.
(function () {
    "use strict";

    function debounce(fn, delai) {
        let minuterie = null;
        return function () {
            clearTimeout(minuterie);
            minuterie = setTimeout(fn, delai);
        };
    }

    // Mêmes couleurs que l'import des débits (services/sciage.py : COULEURS).
    const COULEURS = ["#d4d4d4", "#fdf26b", "#7ee07e", "#f472b6", "#60a5fa", "#fb923c", "#a78bfa", "#5eead4", "#fca5a5", "#bef264"];

    function lignes(sauf) {
        // Lignes de débits déjà présentes (hors gabarit vide et ligne ajoutée), dans l'ordre du formulaire.
        return Array.from(document.querySelectorAll("[name^='debits-'][name$='-longueur_mm']"))
            .map(function (champ) { return champ.closest("tr"); })
            .filter(function (tr) { return tr && tr !== sauf && !tr.querySelector("[name*='__prefix__']"); });
    }

    function champ(tr, nom) {
        return tr.querySelector("[name^='debits-'][name$='-" + nom + "']");
    }

    // Nouvelle ligne : couleur pas encore prise, repère suivant et section de la ligne précédente.
    function preremplir(tr) {
        if (!tr || !tr.querySelector) return;
        const autres = lignes(tr);
        const prises = autres.map(function (l) { const c = champ(l, "couleur"); return c ? c.value.toLowerCase() : ""; });
        const couleur = champ(tr, "couleur");
        if (couleur) couleur.value = COULEURS.find(function (c) { return prises.indexOf(c) < 0; }) || COULEURS[autres.length % COULEURS.length];
        const repere = champ(tr, "repere");
        if (repere && !repere.value) {
            const numeros = autres.map(function (l) { const r = champ(l, "repere"); return parseInt(r && r.value, 10) || 0; });
            repere.value = String(Math.max(0, ...numeros) + 1);
        }
        const precedente = autres.slice().reverse().find(function (l) { const s = champ(l, "section"); return s && s.value; });
        const section = champ(tr, "section");
        if (precedente && section && !section.value) {
            const source = champ(precedente, "section");
            const option = source.options[source.selectedIndex];
            // Après l'initialisation de la liste à autocomplétion de la nouvelle ligne (select2).
            setTimeout(function () {
                section.appendChild(new Option(option.text, option.value, true, true));
                if (window.django && window.django.jQuery) window.django.jQuery(section).trigger("change");
                else section.dispatchEvent(new Event("change", { bubbles: true }));
            }, 0);
        }
    }

    function init() {
        const plan = document.getElementById("sciage-plan");
        const form = plan && plan.closest("form");
        if (!form) return;
        let requete = 0;

        function majCroquis(croquis) {
            document.querySelectorAll(".sc-croquis").forEach(function (cellule) {
                const ligne = cellule.closest("tr");
                const champ = ligne && ligne.querySelector("[name^='debits-'][name$='-longueur_mm']");
                const m = champ && champ.name.match(/^debits-(\d+)-/);
                if (m) cellule.innerHTML = croquis[m[1]] || "";
            });
        }

        const recalculer = debounce(function () {
            const numero = ++requete;
            const donnees = new FormData(form);
            fetch(plan.dataset.url, {
                method: "POST",
                body: donnees,
                headers: { "X-CSRFToken": donnees.get("csrfmiddlewaretoken") || "" },
                credentials: "same-origin",
            })
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (json) {
                    if (!json || numero !== requete) return; // une réponse plus récente est déjà partie
                    plan.innerHTML = json.html;
                    majCroquis(json.croquis || {});
                })
                .catch(function () {});
        }, 350);

        form.addEventListener("input", recalculer);
        form.addEventListener("change", recalculer);
        // Lignes ajoutées ou supprimées par l'inline (boutons « Ajouter » / corbeille).
        document.addEventListener("formset:added", function (e) {
            if (e.detail && e.detail.formsetName === "debits") preremplir(e.target);
            recalculer();
        });
        document.addEventListener("formset:removed", recalculer);
        // Les listes à autocomplétion (select2) ne signalent leur changement qu'à jQuery.
        if (window.django && window.django.jQuery) window.django.jQuery(form).on("change", recalculer);
    }

    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
    else init();
})();
