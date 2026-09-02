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
        if (!event.target || !event.target.closest) {
            return;
        }
        // event.target est la ligne <tr> insérée par Django (admin/js/inlines.js),
        // pas le <tbody class="form-group"> qui l'englobe (structure propre à Unfold).
        const row = event.target.closest("tbody.form-group");
        if (!row) {
            return;
        }
        const numero = devisNumeroFromUrl();
        if (numero) {
            wireRow(row, numero);
        } else if (formulaireAjoutDevisActif()) {
            wireRowNouveauDevis(row);
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

    // Formulaire d'AJOUT d'un devis (/admin/chiffrage/devis/add/) : l'objet
    // Devis n'existe pas encore en base, donc pas de numéro pour construire
    // les URL de recalcul/aperçu habituelles — voir wireRowNouveauDevis().
    function formulaireAjoutDevisActif() {
        return /\/admin\/chiffrage\/devis\/add\/?$/.test(window.location.pathname);
    }

    function init() {
        const numero = devisNumeroFromUrl();
        if (numero) {
            document.querySelectorAll("tbody.form-group").forEach((row) => wireRow(row, numero));
            return;
        }
        if (formulaireAjoutDevisActif()) {
            document.querySelectorAll("tbody.form-group").forEach((row) => wireRowNouveauDevis(row));
        }
    }

    function updateDevisTotals(data) {
        const map = {
            montant_matiere_ht: "#montant-matiere-ht",
            montant_operations_ht: "#montant-operations-ht",
            montant_total_ht: "#montant-total-ht",
            montant_total_ttc: "#montant-total-ttc",
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
            ttcCell: row.querySelector(".field-prix_vente_ttc .readonly"),
        };
    }

    function updateLigneCells(row, data) {
        const cells = ligneCells(row);
        clearLigneErreur(row);
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
        if (cells.ttcCell) {
            cells.ttcCell.textContent = data.prix_vente_ttc != null ? data.prix_vente_ttc : "-";
        }
    }

    // Affiche le message d'erreur retourné par le serveur directement dans la
    // ligne (au lieu de le laisser uniquement dans la console, invisible pour
    // l'utilisateur) : ex. "L'article « TOTO1 » n'a pas de coût unitaire
    // renseigné." — la colonne "Prix de vente total" est la plus visible et
    // sert de point d'ancrage, le message complet apparaît en infobulle et
    // sous le tableau si la ligne n'a pas de cellule à elle (ex. réseau).
    function showLigneErreur(row, message) {
        const cells = ligneCells(row);
        const cible = cells.totalCell || cells.prixCell || cells.coutCell;
        if (cible) {
            cible.textContent = "⚠ " + message;
            cible.title = message;
            cible.style.color = "#dc2626";
        }
    }

    function clearLigneErreur(row) {
        const cells = ligneCells(row);
        Object.values(cells).forEach((cell) => {
            if (cell) {
                cell.style.color = "";
                cell.title = "";
            }
        });
    }

    function wireRow(row, numero) {
        if (row.dataset.liveWired === "1") {
            return; // déjà câblée (évite un double câblage si init() et formset:added se recoupent)
        }

        const idInput = row.querySelector('input[name$="-id"]');
        const quantiteInput = row.querySelector('input[name$="-quantite"]');
        const tauxInput = row.querySelector('input[name$="-taux_marge_matiere_applique"]');
        const prixForceInput = row.querySelector('input[name$="-prix_vente_unitaire_force"]');
        const tauxTvaSelect = row.querySelector('select[name$="-taux_tva"]');

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

        const champs = { quantiteInput, tauxInput, prixForceInput, tauxTvaSelect };

        if (idInput && idInput.value) {
            wireRowExistante(row, numero, idInput.value, champs);
        } else {
            wireRowNouvelle(row, numero, champs);
        }
    }

    // Ligne déjà enregistrée : recalcul en direct persisté côté serveur
    // (POST .../recalculer/), qui met aussi à jour les totaux du devis.
    // Déclenché à la fois sur chaque modification ET une première fois tout de
    // suite (une ligne enregistrée a forcément déjà une quantité et un
    // article) : sans ce premier appel, une ligne déjà remplie au chargement
    // de la page (le cas normal) affiche des "-" tant que personne n'a encore
    // retouché un champ, ce qui donne l'impression que le calcul en direct ne
    // fonctionne pas du tout.
    function wireRowExistante(row, numero, ligneId, champs) {
        const { quantiteInput, tauxInput, prixForceInput, tauxTvaSelect } = champs;
        const url = `/admin/chiffrage/devis/${encodeURIComponent(numero)}/lignes/${ligneId}/recalculer/`;

        const executer = () => {
            const payload = {
                quantite: quantiteInput.value,
                taux_marge_matiere_applique: tauxInput ? tauxInput.value : null,
                prix_vente_unitaire_force: prixForceInput ? prixForceInput.value : null,
                taux_tva: tauxTvaSelect ? tauxTvaSelect.value : null,
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
                        showLigneErreur(row, data.detail || "Erreur lors du recalcul.");
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
                    showLigneErreur(row, "Erreur réseau lors du recalcul.");
                });
        };

        const recalculer = debounce(executer, 400);

        quantiteInput.addEventListener("input", recalculer);
        if (tauxInput) {
            tauxInput.addEventListener("input", recalculer);
        }
        if (prixForceInput) {
            prixForceInput.addEventListener("input", recalculer);
        }
        if (tauxTvaSelect) {
            tauxTvaSelect.addEventListener("change", recalculer);
        }

        executer();
    }

    // Ligne pas encore enregistrée : simple aperçu (POST .../previsualiser/),
    // qui ne persiste rien et ne touche donc pas les totaux du devis — ceux-ci
    // ne reflètent que les lignes réellement enregistrées.
    function wireRowNouvelle(row, numero, champs) {
        const { quantiteInput, tauxInput, prixForceInput, tauxTvaSelect } = champs;
        const articleSelect = row.querySelector('select[name$="-article"]');
        if (!articleSelect) {
            return;
        }

        const url = `/admin/chiffrage/devis/${encodeURIComponent(numero)}/lignes/previsualiser/`;

        const executer = () => {
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
                taux_tva: tauxTvaSelect ? tauxTvaSelect.value : null,
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
                        showLigneErreur(row, data.detail || "Erreur lors de l'aperçu.");
                        return;
                    }
                    updateLigneCells(row, data);
                })
                .catch(() => {
                    row.style.opacity = "1";
                    console.error("Aperçu ligne de devis : erreur réseau.");
                    showLigneErreur(row, "Erreur réseau lors de l'aperçu.");
                });
        };

        const previsualiser = debounce(executer, 400);

        quantiteInput.addEventListener("input", previsualiser);
        articleSelect.addEventListener("change", previsualiser);
        if (tauxInput) {
            tauxInput.addEventListener("input", previsualiser);
        }
        if (prixForceInput) {
            prixForceInput.addEventListener("input", previsualiser);
        }
        if (tauxTvaSelect) {
            tauxTvaSelect.addEventListener("change", previsualiser);
        }

        // Cas rare mais possible (ex. retour arrière du navigateur restaurant
        // un formulaire partiellement rempli) : si article + quantité sont
        // déjà renseignés au chargement, calcule tout de suite plutôt que
        // d'attendre une interaction qui n'aura peut-être jamais lieu.
        executer();
    }

    // Ligne sur le formulaire d'AJOUT d'un devis : le devis lui-même n'est pas
    // encore enregistré (pas de numéro), donc pas question d'appeler les
    // endpoints .../lignes/previsualiser/ qui exigent un Devis existant en
    // base. previsualiser_ligne_nouveau_devis_view ne dépend d'aucun objet
    // Devis persistant : on lui fournit directement, dans le payload, les
    // deux champs de contexte que previsualiser_ligne() lit habituellement
    // sur l'objet (date de création, taux de marge globale), lus en direct
    // sur le formulaire au moment du calcul.
    function wireRowNouveauDevis(row) {
        if (row.dataset.liveWired === "1") {
            return;
        }

        const quantiteInput = row.querySelector('input[name$="-quantite"]');
        if (!quantiteInput || quantiteInput.name.indexOf("__prefix__") !== -1) {
            return;
        }
        const articleSelect = row.querySelector('select[name$="-article"]');
        if (!articleSelect) {
            return;
        }

        row.dataset.liveWired = "1";

        const tauxInput = row.querySelector('input[name$="-taux_marge_matiere_applique"]');
        const prixForceInput = row.querySelector('input[name$="-prix_vente_unitaire_force"]');
        const tauxTvaSelect = row.querySelector('select[name$="-taux_tva"]');

        const url = "/admin/chiffrage/devis/nouveau-devis/previsualiser-ligne/";

        const executer = () => {
            const article = articleSelect.value;
            const quantite = quantiteInput.value;
            if (!article || !quantite) {
                return; // pas assez d'information pour un aperçu
            }

            const dateCreationInput = document.querySelector('[name="date_creation"]');
            const tauxGlobalInput = document.querySelector('[name="taux_marge_globale"]');

            const payload = {
                article: article,
                quantite: quantite,
                date_creation: dateCreationInput ? dateCreationInput.value : null,
                taux_marge_globale: tauxGlobalInput ? tauxGlobalInput.value : null,
                taux_marge_matiere_applique: tauxInput ? tauxInput.value : null,
                prix_vente_unitaire_force: prixForceInput ? prixForceInput.value : null,
                taux_tva: tauxTvaSelect ? tauxTvaSelect.value : null,
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
                        console.error("Aperçu ligne de devis (nouveau devis) :", data.detail);
                        showLigneErreur(row, data.detail || "Erreur lors de l'aperçu.");
                        return;
                    }
                    updateLigneCells(row, data);
                })
                .catch(() => {
                    row.style.opacity = "1";
                    console.error("Aperçu ligne de devis (nouveau devis) : erreur réseau.");
                    showLigneErreur(row, "Erreur réseau lors de l'aperçu.");
                });
        };

        const previsualiser = debounce(executer, 400);

        quantiteInput.addEventListener("input", previsualiser);
        articleSelect.addEventListener("change", previsualiser);
        if (tauxInput) {
            tauxInput.addEventListener("input", previsualiser);
        }
        if (prixForceInput) {
            prixForceInput.addEventListener("input", previsualiser);
        }
        if (tauxTvaSelect) {
            tauxTvaSelect.addEventListener("change", previsualiser);
        }

        executer();
    }
})();
