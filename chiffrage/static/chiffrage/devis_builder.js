function demarrerConstructeur() {
    "use strict";
    if (!document.getElementById("lignes-constructeur")) return;

    const API_ARTICLES = "/api/v1/articles/";
    const API_MATIERES = "/api/v1/matieres/";
    const csrfToken = document.querySelector("[name=csrfmiddlewaretoken]")
        ? document.querySelector("[name=csrfmiddlewaretoken]").value
        : "";
    const dataEl = document.getElementById("devis-builder-data");
    const devisBuilderData = dataEl ? JSON.parse(dataEl.textContent) : {};
    const DEVIS_NUMERO = devisBuilderData.numero || null;
    const DEVIS_DATE_CREATION = devisBuilderData.date_creation || null;
    const URL_AJOUT = devisBuilderData.url;

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
                    const nature = article.nature === "matiere_premiere" ? "matière première" : "fabriqué";
                    li.textContent = article.libelle
                        ? `${article.reference} — ${article.libelle} (${nature})`
                        : `${article.reference} (${nature})`;
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
        const voirArticleLink = document.getElementById("article-existant-voir");
        wireTypeahead(
            articleExistantSearch,
            document.getElementById("article-existant-results"),
            document.getElementById("article-existant-reference"),
            (article) => {
                if (voirArticleLink) {
                    voirArticleLink.href = `/admin/technique/article/${encodeURIComponent(article.reference)}/change/`;
                    voirArticleLink.classList.remove("hidden");
                }
            }
        );
        // Retape dans la recherche = plus d'article sélectionné : cacher le lien.
        articleExistantSearch.addEventListener("input", () => {
            if (voirArticleLink) voirArticleLink.classList.add("hidden");
        });
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
        // Les étapes sont saisies avec l'éditeur d'opérations partagé (comptes/gamme_editeur.js), en mode brouillon : la date de début
        // est celle du DEVIS (une étape datée d'aujourd'hui sur un devis plus ancien serait exclue du calcul).
        const editeur = document.getElementById("gamme-editeur");
        return editeur && editeur.etapesBrouillon ? editeur.etapesBrouillon() : [];
    }

    // ---- Ouverture / fermeture de l'assistant ----
    const panneau = document.getElementById("panneau-constructeur");
    const ouvrir = document.getElementById("ouvrir-constructeur");
    let formulaireModifie = false;
    document.addEventListener("input", (e) => {
        if (e.target.closest && !e.target.closest("#lignes-constructeur") && !e.target.closest("#dp-panneau") && e.target.closest("form")) formulaireModifie = true;
    });
    if (ouvrir && panneau) {
        ouvrir.addEventListener("click", () => { panneau.hidden = !panneau.hidden; ouvrir.classList.toggle("lc-ouvert", !panneau.hidden); });
        const fermer = document.getElementById("fermer-constructeur");
        if (fermer) fermer.addEventListener("click", () => { panneau.hidden = true; ouvrir.classList.remove("lc-ouvert"); });
    }

    function construireCharge() {
        const quantite = parseFloat(document.getElementById("ligne-quantite").value);
        if (!quantite) {
            showMessage("La quantité de la ligne de devis est requise.", true);
            return null;
        }
        const nouveau = document.querySelector("input[name=mode-article]:checked").value === "nouveau";
        const payload = { quantite: quantite };
        if (nouveau) {
            const reference = document.getElementById("na-reference").value.trim();
            if (!reference) {
                showMessage("La référence du nouvel article est requise.", true);
                return null;
            }
            payload.nouvel_article = {
                reference: reference,
                libelle: document.getElementById("na-libelle").value.trim(),
                taux_marge_defaut: parseFloat(document.getElementById("na-taux-marge").value) || null,
                composants: collectNomenclature(),
                etapes: collectGamme(),
            };
        } else {
            const reference = document.getElementById("article-existant-reference").value;
            if (!reference) {
                showMessage("Sélectionnez un article existant dans la liste.", true);
                return null;
            }
            payload.article_existant = reference;
        }
        return payload;
    }

    function envoyer(apercu) {
        const payload = construireCharge();
        if (!payload) return;
        if (apercu) payload.apercu = true;
        else if (formulaireModifie && !window.confirm("Le devis a des modifications non enregistrées : l'ajout recharge la page et elles seront perdues. Continuer ?")) return;
        const zoneApercu = document.getElementById("lc-apercu");
        fetch(URL_AJOUT, {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
            body: JSON.stringify(payload),
        })
            .then((response) => response.json().then((data) => ({ status: response.status, data })))
            .then(({ status, data }) => {
                if (status >= 400) {
                    showMessage(data.detail || "Erreur lors de l'ajout de la ligne.", true);
                    return;
                }
                if (apercu) {
                    const e = (x) => (x === null || x === undefined ? "—" : Number(x).toFixed(2).replace(".", ",") + " €");
                    zoneApercu.hidden = false;
                    zoneApercu.innerHTML = "";
                    [["Matière", data.prix_vente_matiere], ["Opérations", data.prix_vente_operations], ["Prix unitaire HT", data.prix_vente_unitaire], ["Total HT", data.prix_vente_total], ["Total TTC", data.prix_vente_ttc]].forEach(([titre, valeur]) => {
                        const bloc = document.createElement("div");
                        const petit = document.createElement("small");
                        petit.textContent = titre;
                        const gras = document.createElement("b");
                        gras.textContent = e(valeur);
                        bloc.append(petit, gras);
                        zoneApercu.appendChild(bloc);
                    });
                    showMessage("Aperçu : rien n'a été créé.", false);
                    return;
                }
                showMessage(`Ligne ajoutée (${data.article} × ${data.quantite}). Rechargement...`, false);
                try { window.sessionStorage.setItem("devis-onglet-apres-rechargement", "lignes"); } catch (e) { /* ignoré */ }
                setTimeout(() => { window.location.hash = "#onglet=lignes"; window.location.reload(); }, 800);
            })
            .catch(() => showMessage("Erreur réseau lors de l'envoi.", true));
    }

    const submitBtn = document.getElementById("submit-ligne");
    if (submitBtn) submitBtn.addEventListener("click", () => envoyer(false));
    const apercuBtn = document.getElementById("apercu-ligne");
    if (apercuBtn) apercuBtn.addEventListener("click", () => envoyer(true));

    // Une ligne de chaque par défaut pour démarrer
    if (nomenclatureRowsEl) {
        addNomenclatureRow();
    }
}
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", demarrerConstructeur);
else demarrerConstructeur();
