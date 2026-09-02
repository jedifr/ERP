(function () {
    "use strict";

    document.addEventListener("DOMContentLoaded", init);

    // Une nouvelle ligne ajoutée via "Ajouter un objet Ligne de devis
    // supplémentaire" est un clone DOM : les écouteurs posés par wireRow()
    // sur le gabarit ne sont jamais copiés avec (comportement standard du
    // clonage DOM), donc chaque ligne ajoutée dynamiquement doit être
    // câblée à nouveau. Django déclenche "formset:added" sur la ligne
    // fraîchement insérée (admin/js/inlines.js) — Unfold réutilise ce
    // mécanisme, seul le gabarit HTML change.
    document.addEventListener("formset:added", (event) => {
        const numero = devisNumeroFromUrl();
        if (!numero || !event.target || !event.target.closest) {
            return;
        }
        // event.target est la ligne <tr> insérée par Django (admin/js/inlines.js),
        // pas le <tbody class="form-group"> qui l'englobe (structure propre à Unfold).
        const row = event.target.closest("tbody.form-group");
        if (row) {
            wireRow(row, numero);
        }
    });

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

    function updateDevisTotals(data) {
        const map = {
            montant_matiere_ht: "#montant-matiere-ht",
            montant_operations_ht: "#montant-operations-ht",
            montant_total_ht: "#montant-total-ht",
        };
        Object.keys(map).forEach((key) => {
            if (data[key] == null) return;
            const el = document.querySelector(map[key]);
            if (el) el.textContent = data[key];
        });
    }

    function ligneCells(row) {
        return {
            coutCell: row.querySelector(".field-cout_matiere_calcule .readonly"),
            prixCell: row.querySelector(".field-prix_vente_matiere .readonly"),
            operationsCell: row.querySelector(".field-prix_vente_operations .readonly"),
            totalCell: row.querySelector(".field-prix_vente_total .readonly"),
        };
    }

    function updateLigneCells(row, data) {
        const cells = ligneCells(row);
        if (cells.coutCell) {
            cells.coutCell.textContent = data.cout_matiere_calcule != null ? data.cout_matiere_calcule : "-";
        }
        if (cells.prixCell) {
            cells.prixCell.textContent = data.prix_vente_matiere != null ? data.prix_vente_matiere : "-";
        }
        if (cells.operationsCell) {
            cells.operationsCell.textContent =
                data.prix_vente_operations != null ? data.prix_vente_operations : "-";
        }
        if (cells.totalCell) {
            cells.totalCell.textContent = data.prix_vente_total != null ? data.prix_vente_total : "-";
        }
    }

    function wireRow(row, numero) {
        if (row.dataset.liveWired === "1") {
            return; // déjà câblée (évite un double câblage si init() et formset:added se recoupent)
        }

        const idInput = row.querySelector('input[name$="-id"]');
        const quantiteInput = row.querySelector('input[name$="-quantite"]');
        const tauxInput = row.querySelector('input[name$="-taux_marge_matiere_applique"]');
        const prixForceInput = row.querySelector('input[name$="-prix_vente_unitaire_force"]');

        if (!quantiteInput || quantiteInput.name.indexOf("__prefix__") !== -1) {
            // Le gabarit caché (__prefix__) sert uniquement de source de clonage au bouton
            // "Ajouter" : il n'est jamais rempli directement par l'utilisateur. Le marquer
            // "câblé" ici serait recopié tel quel (comme tout attribut data-*) sur chaque
            // clone par le clonage DOM, alors que les écouteurs JS, eux, ne le sont jamais
            // — chaque ligne ajoutée dynamiquement se retrouverait donc marquée "câblée"
            // sans qu'aucun écouteur n'y soit réellement attaché.
            return;
        }

        row.dataset.liveWired = "1";

        if (idInput && idInput.value) {
            wireRowExistante(row, numero, idInput.value, quantiteInput, tauxInput, prixForceInput);
        } else {
            wireRowNouvelle(row, numero, quantiteInput, tauxInput, prixForceInput);
        }
    }

    // Ligne déjà enregistrée : recalcul en direct persisté côté serveur
    // (POST .../recalculer/), qui met aussi à jour les totaux du devis.
    function wireRowExistante(row, numero, ligneId, quantiteInput, tauxInput, prixForceInput) {
        const url = `/admin/chiffrage/devis/${encodeURIComponent(numero)}/lignes/${ligneId}/recalculer/`;

        const recalculer = debounce(() => {
            const payload = {
                quantite: quantiteInput.value,
                taux_marge_matiere_applique: tauxInput ? tauxInput.value : null,
                prix_vente_unitaire_force: prixForceInput ? prixForceInput.value : null,
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
                    updateLigneCells(row, data);
                    if (tauxInput && data.taux_marge_matiere_applique != null) {
                        tauxInput.value = data.taux_marge_matiere_applique;
                    }
                    updateDevisTotals(data);
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
        if (prixForceInput) {
            prixForceInput.addEventListener("input", recalculer);
        }
    }

    // Ligne pas encore enregistrée : simple aperçu (POST .../previsualiser/),
    // qui ne persiste rien et ne touche donc pas les totaux du devis — ceux-ci
    // ne reflètent que les lignes réellement enregistrées.
    function wireRowNouvelle(row, numero, quantiteInput, tauxInput, prixForceInput) {
        const articleSelect = row.querySelector('select[name$="-article"]');
        if (!articleSelect) {
            return;
        }

        const url = `/admin/chiffrage/devis/${encodeURIComponent(numero)}/lignes/previsualiser/`;

        const previsualiser = debounce(() => {
            const article = articleSelect.value;
            const quantite = quantiteInput.value;
            if (!article || !quantite) {
                return; // pas assez d'information pour un aperçu
            }

            const payload = {
                article: article,
                quantite: quantite,
                taux_marge_matiere_applique: tauxInput ? tauxInput.value : null,
                prix_vente_unitaire_force: prixForceInput ? prixForceInput.value : null,
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
                        console.error("Aperçu ligne de devis :", data.detail);
                        return;
                    }
                    updateLigneCells(row, data);
                })
                .catch(() => {
                    row.style.opacity = "1";
                    console.error("Aperçu ligne de devis : erreur réseau.");
                });
        }, 400);

        quantiteInput.addEventListener("input", previsualiser);
        articleSelect.addEventListener("change", previsualiser);
        if (tauxInput) {
            tauxInput.addEventListener("input", previsualiser);
        }
        if (prixForceInput) {
            prixForceInput.addEventListener("input", previsualiser);
        }
    }
})();
