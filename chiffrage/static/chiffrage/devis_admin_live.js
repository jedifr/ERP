(function () {
    "use strict";

    document.addEventListener("DOMContentLoaded", init);

    function debounce(fn, delay) {
        let timer = null;
        return function (...args) {
            clearTimeout(timer);
            timer = setTimeout(() => fn.apply(this, args), delay);
        };
    }

    function csrfToken() {
        const el = document.querySelector("[name=csrfmiddlewaretoken]");
        return el ? el.value : "";
    }

    function devisNumeroFromUrl() {
        const match = window.location.pathname.match(/\/admin\/chiffrage\/devis\/([^/]+)\/change\/?/);
        return match ? match[1] : null;
    }

    function init() {
        const numero = devisNumeroFromUrl();
        if (!numero) {
            return; // pas sur la fiche d'un devis existant
        }
        document.querySelectorAll("tbody.form-group").forEach((row) => wireRow(row, numero));
    }

    function wireRow(row, numero) {
        const idInput = row.querySelector('input[name$="-id"]');
        const quantiteInput = row.querySelector('input[name$="-quantite"]');
        const tauxInput = row.querySelector('input[name$="-taux_marge_matiere_applique"]');
        const coutCell = row.querySelector(".field-cout_matiere_calcule .readonly");
        const prixCell = row.querySelector(".field-prix_vente_matiere .readonly");

        // Ligne pas encore enregistrée (formulaire d'ajout vide) : pas de recalcul
        // live possible tant qu'elle n'a pas d'identifiant en base.
        if (!idInput || !idInput.value || !quantiteInput || !coutCell || !prixCell) {
            return;
        }

        const ligneId = idInput.value;
        const url = `/admin/chiffrage/devis/${encodeURIComponent(numero)}/lignes/${ligneId}/recalculer/`;

        const recalculer = debounce(() => {
            const payload = {
                quantite: quantiteInput.value,
                taux_marge_matiere_applique: tauxInput ? tauxInput.value : null,
            };

            row.style.opacity = "0.6";

            fetch(url, {
                method: "POST",
                credentials: "same-origin",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRFToken": csrfToken(),
                },
                body: JSON.stringify(payload),
            })
                .then((response) => response.json().then((data) => ({ status: response.status, data })))
                .then(({ status, data }) => {
                    row.style.opacity = "1";
                    if (status >= 400) {
                        console.error("Recalcul ligne de devis :", data.detail);
                        return;
                    }
                    coutCell.textContent = data.cout_matiere_calcule != null ? data.cout_matiere_calcule : "-";
                    prixCell.textContent = data.prix_vente_matiere != null ? data.prix_vente_matiere : "-";
                    if (tauxInput && data.taux_marge_matiere_applique != null) {
                        tauxInput.value = data.taux_marge_matiere_applique;
                    }
                })
                .catch(() => {
                    row.style.opacity = "1";
                    console.error("Recalcul ligne de devis : erreur réseau.");
                });
        }, 400);

        quantiteInput.addEventListener("input", recalculer);
        if (tauxInput) {
            tauxInput.addEventListener("input", recalculer);
        }
    }
})();
