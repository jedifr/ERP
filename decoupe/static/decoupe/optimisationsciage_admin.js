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
        document.addEventListener("formset:added", recalculer);
        document.addEventListener("formset:removed", recalculer);
        // Les listes à autocomplétion (select2) ne signalent leur changement qu'à jQuery.
        if (window.django && window.django.jQuery) window.django.jQuery(form).on("change", recalculer);
    }

    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
    else init();
})();
