(function () {
    "use strict";

    const API_ARTICLES = "/api/v1/articles/";
    const API_MATIERES = "/api/v1/matieres/";
    const csrfToken = document.querySelector("[name=csrfmiddlewaretoken]")
        ? document.querySelector("[name=csrfmiddlewaretoken]").value
        : "";
    const dataEl = document.getElementById("devis-builder-data");
    const DEVIS_NUMERO = dataEl ? JSON.parse(dataEl.textContent).numero : null;

    const matiereCache = {};

    function debounce(fn, delay) {
        let timer = null;
        return function (...args) {
            clearTimeout(timer);
            timer = setTimeout(() => fn.apply(this, args), delay);
        };
    }

    function searchArticles(query, callback) {
        if (!query || query.length < 1) {
            callback([]);
            return;
        }
        fetch(`${API_ARTICLES}?search=${encodeURIComponent(query)}`, { credentials: "same-origin" })
            .then((r) => (r.ok ? r.json() : { results: [] }))
            .then((data) => callback(data.results || []))
            .catch(() => callback([]));
    }

    function fetchMatiereDensite(nom, callback) {
        if (!nom) {
            callback(null);
            return;
        }
        if (matiereCache[nom] !== undefined) {
            callback(matiereCache[nom]);
            return;
        }
        fetch(`${API_MATIERES}${encodeURIComponent(nom)}/`, { credentials: "same-origin" })
            .then((r) => (r.ok ? r.json() : null))
            .then((data) => {
                const densite = data ? data.densite : null;
                matiereCache[nom] = densite;
                callback(densite);
            })
            .catch(() => callback(null));
    }

    function wireTypeahead(inputEl, resultsEl, hiddenEl, onSelect) {
        const doSearch = debounce(() => {
            searchArticles(inputEl.value, (results) => {
                resultsEl.innerHTML = "";
                if (results.length === 0) {
                    resultsEl.classList.add("hidden");
                    return;
                }
                results.forEach((article) => {
                    const li = document.createElement("li");
                    li.className = "px-3 py-2 cursor-pointer hover:bg-base-100 dark:hover:bg-base-700";
                    li.textContent = `${article.reference} (${article.nature === "matiere_premiere" ? "matière première" : "fabriqué"})`;
                    li.addEventListener("click", () => {
                        inputEl.value = article.reference;
                        hiddenEl.value = article.reference;
                        resultsEl.classList.add("hidden");
                        onSelect(article);
                    });
                    resultsEl.appendChild(li);
                });
                resultsEl.classList.remove("hidden");
            });
        }, 250);

        inputEl.addEventListener("input", () => {
            hiddenEl.value = "";
            doSearch();
        });
        inputEl.addEventListener("blur", () => {
            setTimeout(() => resultsEl.classList.add("hidden"), 150);
        });
    }

    // ---- Toggle article existant / nouvel article ----
    const radios = document.querySelectorAll("input[name=mode-article]");
    const blocExistant = document.getElementById("bloc-article-existant");
    const blocNouveau = document.getElementById("bloc-nouvel-article");
    if (radios.length) {
        radios.forEach((radio) => {
            radio.addEventListener("change", () => {
                const nouveau = document.querySelector("input[name=mode-article]:checked").value === "nouveau";
                blocExistant.classList.toggle("hidden", nouveau);
                blocNouveau.classList.toggle("hidden", !nouveau);
            });
        });
    }

    // ---- Typeahead article existant ----
    const articleExistantSearch = document.getElementById("article-existant-search");
    if (articleExistantSearch) {
        wireTypeahead(
            articleExistantSearch,
            document.getElementById("article-existant-results"),
            document.getElementById("article-existant-reference"),
            () => {}
        );
    }

    // ---- Nomenclature rows ----
    const nomenclatureRowsEl = document.getElementById("nomenclature-rows");
    const templateNomenclature = document.getElementById("template-nomenclature-row");

    function computeSurface(row) {
        const l = parseFloat(row.querySelector(".input-longueur").value) || 0;
        const larg = parseFloat(row.querySelector(".input-largeur").value) || 0;
        return (l * larg) / 1_000_000;
    }

    function refreshDerivedFields(row) {
        const data = row._articleData;
        if (!data) return;

        if (data.unite_cout === "surface" || data.unite_cout === "poids") {
            const surface = computeSurface(row);
            row.querySelector(".input-surface").value = surface ? surface.toFixed(4) : "";
            const champPoids = row.querySelector(".champ-poids");
            if (data.epaisseur && data._densite) {
                champPoids.classList.remove("hidden");
                const poids = surface * data.epaisseur * data._densite;
                row.querySelector(".input-poids").value = poids ? poids.toFixed(3) : "";
                row.querySelector(".input-poids").readOnly = true;
                row.querySelector(".poids-editable-tag").textContent = "(calculé)";
            } else {
                champPoids.classList.add("hidden");
            }
        }
    }

    function configureRowForArticle(row, article) {
        row._articleData = Object.assign({}, article, { _densite: null });
        const infoEl = row.querySelector(".composant-info");
        const champsDimension = row.querySelector(".champs-dimension");
        const champLongueur = row.querySelector(".champ-longueur");
        const champLargeur = row.querySelector(".champ-largeur");
        const champPoids = row.querySelector(".champ-poids");
        const champSurface = row.querySelector(".champ-surface");
        const inputLongueur = row.querySelector(".input-longueur");
        const inputLargeur = row.querySelector(".input-largeur");
        const inputPoids = row.querySelector(".input-poids");

        champLongueur.classList.add("hidden");
        champLargeur.classList.add("hidden");
        champPoids.classList.add("hidden");
        champSurface.classList.add("hidden");
        inputPoids.readOnly = false;
        row.querySelector(".poids-editable-tag").textContent = "";

        if (article.unite_cout === "piece") {
            infoEl.textContent = "Vendu à la pièce — pas de dimension à renseigner.";
            champsDimension.classList.add("hidden");
        } else if (article.unite_cout === "longueur") {
            infoEl.textContent = article.poids_lineique
                ? `Vendu au mètre — ${article.poids_lineique} kg/m. Longueur ou poids : au choix.`
                : "Vendu au mètre.";
            champsDimension.classList.remove("hidden");
            champLongueur.classList.remove("hidden");
            if (article.poids_lineique) {
                champPoids.classList.remove("hidden");
            }
        } else if (article.unite_cout === "surface") {
            infoEl.textContent = "Vendu au m² — renseignez longueur × largeur.";
            champsDimension.classList.remove("hidden");
            champLongueur.classList.remove("hidden");
            champLargeur.classList.remove("hidden");
            champSurface.classList.remove("hidden");
        } else if (article.unite_cout === "poids") {
            champsDimension.classList.remove("hidden");
            champLongueur.classList.remove("hidden");
            champLargeur.classList.remove("hidden");
            champSurface.classList.remove("hidden");
            if (article.matiere && article.epaisseur) {
                infoEl.textContent = "Vendu au poids — renseignez longueur × largeur, le poids est calculé.";
                fetchMatiereDensite(article.matiere, (densite) => {
                    row._articleData._densite = densite;
                    refreshDerivedFields(row);
                });
            } else {
                infoEl.textContent = "⚠ Matière ou épaisseur manquante sur cet article : poids non calculable.";
            }
        } else {
            infoEl.textContent = "⚠ Unité de coût non définie sur cet article.";
            champsDimension.classList.add("hidden");
        }

        // Conversion bidirectionnelle longueur <-> poids (profilé vendu au poids linéique)
        inputLongueur.oninput = () => {
            if (article.unite_cout === "longueur" && article.poids_lineique) {
                const poids = (parseFloat(inputLongueur.value) || 0) / 1000 * article.poids_lineique;
                inputPoids.value = poids ? poids.toFixed(3) : "";
            }
            refreshDerivedFields(row);
        };
        inputLargeur.oninput = () => refreshDerivedFields(row);
        if (article.unite_cout === "longueur" && article.poids_lineique) {
            inputPoids.oninput = () => {
                const longueur = ((parseFloat(inputPoids.value) || 0) / article.poids_lineique) * 1000;
                inputLongueur.value = longueur ? longueur.toFixed(1) : "";
            };
        }
    }

    function addNomenclatureRow() {
        const fragment = templateNomenclature.content.cloneNode(true);
        const row = fragment.querySelector(".nomenclature-row");
        nomenclatureRowsEl.appendChild(fragment);

        const searchInput = row.querySelector(".composant-search");
        const resultsEl = row.querySelector(".composant-results");
        const hiddenEl = row.querySelector(".composant-reference");
        wireTypeahead(searchInput, resultsEl, hiddenEl, (article) => configureRowForArticle(row, article));

        row.querySelector(".remove-row").addEventListener("click", () => row.remove());
    }

    if (document.getElementById("add-nomenclature-row")) {
        document.getElementById("add-nomenclature-row").addEventListener("click", addNomenclatureRow);
    }

    // ---- Gamme rows ----
    const gammeRowsEl = document.getElementById("gamme-rows");
    const templateGamme = document.getElementById("template-gamme-row");

    function addGammeRow() {
        const fragment = templateGamme.content.cloneNode(true);
        const row = fragment.querySelector(".gamme-row");
        gammeRowsEl.appendChild(fragment);

        const ordreInput = row.querySelector(".input-ordre");
        ordreInput.value = gammeRowsEl.children.length;

        const dateDebut = row.querySelector(".input-date-debut");
        dateDebut.value = new Date().toISOString().slice(0, 10);

        const selectPoste = row.querySelector(".select-poste");
        const champsHoraire = row.querySelector(".champs-horaire");
        const champsForfaitaire = row.querySelector(".champs-forfaitaire");
        selectPoste.addEventListener("change", () => {
            const option = selectPoste.selectedOptions[0];
            const mode = option ? option.dataset.mode : "";
            champsHoraire.classList.toggle("hidden", mode !== "horaire");
            champsForfaitaire.classList.toggle("hidden", mode !== "forfaitaire");
        });

        row.querySelector(".remove-row").addEventListener("click", () => row.remove());
    }

    if (document.getElementById("add-gamme-row")) {
        document.getElementById("add-gamme-row").addEventListener("click", addGammeRow);
    }

    // ---- Soumission ----
    function showMessage(text, isError) {
        const el = document.getElementById("form-message");
        el.textContent = text;
        el.classList.remove("hidden", "bg-red-100", "text-red-800", "bg-green-100", "text-green-800");
        el.classList.add(isError ? "bg-red-100" : "bg-green-100", isError ? "text-red-800" : "text-green-800");
    }

    function collectNomenclature() {
        const composants = [];
        nomenclatureRowsEl.querySelectorAll(".nomenclature-row").forEach((row) => {
            composants.push({
                article_composant: row.querySelector(".composant-reference").value,
                quantite: parseFloat(row.querySelector(".input-quantite").value) || null,
                longueur_mm: parseFloat(row.querySelector(".input-longueur").value) || null,
                largeur_mm: parseFloat(row.querySelector(".input-largeur").value) || null,
            });
        });
        return composants;
    }

    function collectGamme() {
        const etapes = [];
        gammeRowsEl.querySelectorAll(".gamme-row").forEach((row) => {
            etapes.push({
                poste: row.querySelector(".select-poste").value,
                ordre: parseInt(row.querySelector(".input-ordre").value, 10) || null,
                temps_fixe: parseFloat(row.querySelector(".input-temps-fixe").value) || null,
                temps_variable: parseFloat(row.querySelector(".input-temps-variable").value) || null,
                cout_forfaitaire: parseFloat(row.querySelector(".input-cout-forfaitaire").value) || null,
                date_debut: row.querySelector(".input-date-debut").value || null,
            });
        });
        return etapes;
    }

    const submitBtn = document.getElementById("submit-ligne");
    if (submitBtn) {
        submitBtn.addEventListener("click", () => {
            const quantite = parseFloat(document.getElementById("ligne-quantite").value);
            if (!quantite) {
                showMessage("La quantité de la ligne de devis est requise.", true);
                return;
            }

            const nouveau = document.querySelector("input[name=mode-article]:checked").value === "nouveau";
            const payload = { quantite: quantite };

            if (nouveau) {
                const reference = document.getElementById("na-reference").value.trim();
                if (!reference) {
                    showMessage("La référence du nouvel article est requise.", true);
                    return;
                }
                payload.nouvel_article = {
                    reference: reference,
                    taux_marge_defaut: parseFloat(document.getElementById("na-taux-marge").value) || null,
                    composants: collectNomenclature(),
                    etapes: collectGamme(),
                };
            } else {
                const reference = document.getElementById("article-existant-reference").value;
                if (!reference) {
                    showMessage("Sélectionnez un article existant dans la liste.", true);
                    return;
                }
                payload.article_existant = reference;
            }

            fetch(window.location.pathname, {
                method: "POST",
                credentials: "same-origin",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRFToken": csrfToken,
                },
                body: JSON.stringify(payload),
            })
                .then((response) => response.json().then((data) => ({ status: response.status, data })))
                .then(({ status, data }) => {
                    if (status >= 400) {
                        showMessage(data.detail || "Erreur lors de l'ajout de la ligne.", true);
                        return;
                    }
                    if (data.avertissement) {
                        showMessage(
                            `Ligne ajoutée (${data.article} × ${data.quantite}), mais le chiffrage n'a pas pu être recalculé : ${data.avertissement}`,
                            true
                        );
                    } else {
                        showMessage(`Ligne ajoutée (${data.article} × ${data.quantite}). Rechargement...`, false);
                    }
                    setTimeout(() => window.location.reload(), data.avertissement ? 2500 : 800);
                })
                .catch(() => showMessage("Erreur réseau lors de l'envoi.", true));
        });
    }

    // Une ligne de chaque par défaut pour démarrer
    if (nomenclatureRowsEl) {
        addNomenclatureRow();
    }
    if (gammeRowsEl) {
        addGammeRow();
    }
})();
