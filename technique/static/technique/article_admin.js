(function () {
    "use strict";

    document.addEventListener("DOMContentLoaded", init);

    function init() {
        const natureField = document.getElementById("id_nature");
        const uniteCoutField = document.getElementById("id_unite_cout");
        const coutUnitaireField = document.getElementById("id_cout_unitaire");
        if (!natureField || !uniteCoutField || !coutUnitaireField) {
            return;
        }

        const matiereField = document.getElementById("id_matiere");
        const epaisseurField = document.getElementById("id_epaisseur");
        const poidsLineiqueField = document.getElementById("id_poids_lineique");

        const helperRow = buildHelperRow(coutUnitaireField);
        const matiereCache = {};

        function fetchDensite(nom, callback) {
            if (!nom) {
                callback(null);
                return;
            }
            if (matiereCache[nom] !== undefined) {
                callback(matiereCache[nom]);
                return;
            }
            fetch(`/api/v1/matieres/${encodeURIComponent(nom)}/`, { credentials: "same-origin" })
                .then((r) => (r.ok ? r.json() : null))
                .then((data) => {
                    const densite = data ? data.densite : null;
                    matiereCache[nom] = densite;
                    callback(densite);
                })
                .catch(() => callback(null));
        }

        let syncing = false;

        function refresh() {
            const nature = natureField.value;
            const uniteCout = uniteCoutField.value;
            const epaisseur = parseFloat(epaisseurField ? epaisseurField.value : "");
            const poidsLineique = parseFloat(poidsLineiqueField ? poidsLineiqueField.value : "");

            if (nature !== "matiere_premiere") {
                helperRow.container.classList.add("hidden");
                return;
            }

            if (uniteCout === "poids" && matiereField && matiereField.value && epaisseur) {
                fetchDensite(matiereField.value, (densite) => {
                    if (!densite) {
                        helperRow.container.classList.add("hidden");
                        return;
                    }
                    helperRow.label.textContent = "Prix équivalent au m²";
                    helperRow.help.textContent = `Calculé : coût/kg × épaisseur (mm) × densité (kg/dm³) = ${(epaisseur * densite).toFixed(3)} kg par m².`;
                    helperRow.container.classList.remove("hidden");
                    helperRow.factor = epaisseur * densite;
                    helperRow.mode = "poids_vers_surface";
                    if (!syncing) {
                        const coutKg = parseFloat(coutUnitaireField.value);
                        helperRow.input.value = coutKg ? (coutKg * helperRow.factor).toFixed(4) : "";
                    }
                });
            } else if (uniteCout === "surface" && matiereField && matiereField.value && epaisseur) {
                fetchDensite(matiereField.value, (densite) => {
                    if (!densite) {
                        helperRow.container.classList.add("hidden");
                        return;
                    }
                    helperRow.label.textContent = "Prix équivalent au kg";
                    helperRow.help.textContent = `Calculé : coût/m² ÷ (épaisseur (mm) × densité (kg/dm³)) = ÷ ${(epaisseur * densite).toFixed(3)}.`;
                    helperRow.container.classList.remove("hidden");
                    helperRow.factor = epaisseur * densite;
                    helperRow.mode = "surface_vers_poids";
                    if (!syncing) {
                        const coutM2 = parseFloat(coutUnitaireField.value);
                        helperRow.input.value = coutM2 && helperRow.factor ? (coutM2 / helperRow.factor).toFixed(4) : "";
                    }
                });
            } else if (uniteCout === "longueur" && poidsLineique) {
                helperRow.label.textContent = "Prix équivalent au mètre";
                helperRow.help.textContent = `Calculé : coût/kg × poids linéique (${poidsLineique} kg/m).`;
                helperRow.container.classList.remove("hidden");
                helperRow.factor = poidsLineique;
                helperRow.mode = "poids_vers_metre";
                if (!syncing) {
                    const coutKg = parseFloat(coutUnitaireField.value);
                    helperRow.input.value = coutKg ? (coutKg * poidsLineique).toFixed(4) : "";
                }
            } else {
                helperRow.container.classList.add("hidden");
            }
        }

        helperRow.input.addEventListener("input", () => {
            const value = parseFloat(helperRow.input.value);
            if (!value || !helperRow.factor) return;
            syncing = true;
            if (helperRow.mode === "poids_vers_surface") {
                coutUnitaireField.value = (value / helperRow.factor).toFixed(4);
            } else if (helperRow.mode === "surface_vers_poids") {
                coutUnitaireField.value = (value * helperRow.factor).toFixed(4);
            } else if (helperRow.mode === "poids_vers_metre") {
                coutUnitaireField.value = (value / helperRow.factor).toFixed(4);
            }
            syncing = false;
        });

        [natureField, uniteCoutField, coutUnitaireField, epaisseurField, poidsLineiqueField].forEach((el) => {
            if (el) {
                el.addEventListener("input", refresh);
                el.addEventListener("change", refresh);
            }
        });
        if (matiereField) {
            matiereField.addEventListener("change", refresh);
        }

        refresh();
    }

    function buildHelperRow(coutUnitaireField) {
        const container = document.createElement("div");
        container.className = "hidden group/row field-row form-row px-3 py-2.5";

        const label = document.createElement("label");
        label.className = "block font-semibold mb-2 text-font-important-light text-sm dark:text-font-important-dark";
        container.appendChild(label);

        const input = document.createElement("input");
        input.type = "number";
        input.step = "any";
        input.className =
            "border border-base-200 bg-white font-medium rounded-default shadow-xs text-sm dark:bg-base-900 dark:border-base-700 px-3 py-2 w-full max-w-xs";
        container.appendChild(input);

        const help = document.createElement("p");
        help.className = "text-xs text-font-subtle-light dark:text-font-subtle-dark mt-1";
        container.appendChild(help);

        const fieldRow = coutUnitaireField.closest(".field-row") || coutUnitaireField.closest("div");
        fieldRow.insertAdjacentElement("afterend", container);

        return { container, label, input, help, factor: null, mode: null };
    }
})();
