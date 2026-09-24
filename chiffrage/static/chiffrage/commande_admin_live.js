(function () {
    "use strict";

    document.addEventListener("DOMContentLoaded", init);

    // Même principe que devis_admin_live.js pour les lignes de devis : une
    // nouvelle ligne ajoutée via "Ajouter un objet Ligne de commande
    // supplémentaire" est un clone DOM, jamais câblé automatiquement —
    // Django/Unfold déclenche "formset:added" dessus (voir ce même fichier
    // côté devis pour le détail du mécanisme).
    document.addEventListener("formset:added", (event) => {
        if (!event.target || !event.target.closest) {
            return;
        }
        const row = event.target.closest("tbody.form-group");
        if (!row) {
            return;
        }
        copierDateLivraisonLignePrecedente(row);
        const numero = commandeNumeroFromUrl();
        if (numero) {
            wireRow(row, numero);
        } else if (formulaireAjoutCommandeActif()) {
            wireRowNouvelleCommande(row);
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

    function commandeNumeroFromUrl() {
        const match = window.location.pathname.match(/\/admin\/chiffrage\/commande\/([^/]+)\/change\/?/);
        return match ? match[1] : null;
    }

    function formulaireAjoutCommandeActif() {
        return /\/admin\/chiffrage\/commande\/add\/?$/.test(window.location.pathname);
    }

    function init() {
        wireClientDefaults();

        const numero = commandeNumeroFromUrl();
        if (numero) {
            document.querySelectorAll("tbody.form-group").forEach((row) => wireRow(row, numero));
            return;
        }
        if (formulaireAjoutCommandeActif()) {
            document.querySelectorAll("tbody.form-group").forEach((row) => wireRowNouvelleCommande(row));
        }
    }

    // Dès qu'un client est choisi (ajout comme modification), propose
    // automatiquement son adresse de facturation et son adresse de
    // livraison marquées "principale(s)" — sans jamais écraser un choix
    // déjà fait (voir remplirSiVide()). Réutilise l'endpoint déjà exposé
    // pour la fiche Devis (même lookup : adresses principales d'un tiers),
    // la commande n'a simplement pas de champ "contact" à en tirer.
    function wireClientDefaults() {
        if (typeof django === "undefined" || !django.jQuery) {
            return;
        }
        const $ = django.jQuery;
        const $client = $("#id_client");
        if ($client.length === 0) {
            return;
        }

        $client.on("change", function () {
            const code = $client.val();
            if (!code) {
                return;
            }
            fetch(`/admin/chiffrage/devis/tiers/${encodeURIComponent(code)}/valeurs-defaut/`, {
                credentials: "same-origin",
            })
                .then((response) => (response.ok ? response.json() : null))
                .then((data) => {
                    if (!data) return;
                    remplirSiVide($, "#id_adresse_facturation", data.adresse_facturation);
                    remplirSiVide($, "#id_adresse_livraison", data.adresse_livraison);
                })
                .catch(() => {
                    console.error("Valeurs par défaut du client : erreur réseau.");
                });
        });
    }

    function remplirSiVide($, selector, valeur) {
        if (!valeur) {
            return;
        }
        const $select = $(selector);
        if ($select.length === 0 || $select.val()) {
            return;
        }
        const option = new Option(valeur.texte, valeur.id, true, true);
        $select.append(option).trigger("change");
    }

    // A l'ajout d'une nouvelle ligne, reprend la date de livraison prévue
    // de la dernière ligne déjà présente (signalé par l'utilisateur :
    // souvent la même pour tout un lot de lignes saisies à la suite) —
    // seulement si la nouvelle ligne n'a, elle, pas encore de date (ne
    // devrait jamais en avoir puisqu'elle vient d'apparaître).
    function copierDateLivraisonLignePrecedente(nouvelleLigne) {
        const champNouvelle = nouvelleLigne.querySelector('input[name$="-date_livraison_prevue"]');
        if (!champNouvelle || champNouvelle.value) {
            return;
        }
        const lignes = Array.from(document.querySelectorAll("#lignes-data > tbody.form-group:not(.empty-form)"));
        const index = lignes.indexOf(nouvelleLigne);
        if (index <= 0) {
            return;
        }
        const lignePrecedente = lignes[index - 1];
        const champPrecedent = lignePrecedente.querySelector('input[name$="-date_livraison_prevue"]');
        if (champPrecedent && champPrecedent.value) {
            champNouvelle.value = champPrecedent.value;
        }
    }

    function ligneCells(row) {
        return {
            prixCell: row.querySelector(".field-prix_vente_unitaire .readonly"),
            montantHtCell: row.querySelector(".field-montant_ht .readonly"),
            montantTtcCell: row.querySelector(".field-montant_ttc .readonly"),
        };
    }

    // prix_vente_unitaire reste un champ EDITABLE (pas une colonne readonly
    // comme côté devis) : le calcul automatique remplit l'input lui-même,
    // pas une cellule à part — l'utilisateur peut ensuite le surcharger à
    // la main sans que rien ne revienne l'écraser (voir
    // recalculer_ligne_commande_view côté serveur).
    function updateLigne(row, prixInput, tauxTvaSelect, data) {
        clearLigneErreur(row);
        if (data.prix_vente_unitaire != null) {
            prixInput.value = data.prix_vente_unitaire;
        }
        const cells = ligneCells(row);
        if (cells.montantHtCell && data.montant_ht != null) {
            cells.montantHtCell.textContent = data.montant_ht;
        }
        if (cells.montantTtcCell && data.montant_ttc != null) {
            cells.montantTtcCell.textContent = data.montant_ttc;
        }
        if (tauxTvaSelect && !tauxTvaSelect.value && data.taux_tva_suggere) {
            tauxTvaSelect.value = String(data.taux_tva_suggere.id);
        }
    }

    function showLigneErreur(row, message) {
        const cells = ligneCells(row);
        const cible = cells.prixCell || cells.montantHtCell;
        if (cible) {
            cible.textContent = "⚠ " + message;
            cible.title = message;
            cible.style.color = "#dc2626";
        } else {
            console.error(message);
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
            return;
        }

        const idInput = row.querySelector('input[name$="-id"]');
        const quantiteInput = row.querySelector('input[name$="-quantite_commandee"]');
        if (!quantiteInput || quantiteInput.name.indexOf("__prefix__") !== -1) {
            return;
        }

        row.dataset.liveWired = "1";

        const prixInput = row.querySelector('input[name$="-prix_vente_unitaire"]');
        const tauxTvaSelect = row.querySelector('select[name$="-taux_tva"]');
        const champs = { quantiteInput, prixInput, tauxTvaSelect };

        if (idInput && idInput.value) {
            wireRowExistante(row, numero, idInput.value, champs);
        } else {
            wireRowNouvelle(row, numero, champs);
        }
    }

    // Ligne déjà enregistrée : recalcul en direct persisté côté serveur.
    // Le prix n'est recalculé automatiquement (quantité/gamme/nomenclature)
    // que pour une ligne sans devis d'origine (voir CommandeLigne et
    // recalculer_ligne_commande_view) : sur une ligne issue d'un devis, un
    // changement de quantité modifie juste la quantité, le prix (une
    // surcharge) reste tel quel tant qu'on ne le retouche pas soi-même.
    function wireRowExistante(row, numero, ligneId, champs) {
        const { quantiteInput, prixInput, tauxTvaSelect } = champs;
        const url = `/admin/chiffrage/commande/${encodeURIComponent(numero)}/lignes/${ligneId}/recalculer/`;
        let dernierPrixEnvoye = null;

        const executer = (prixModifieParUtilisateur) => {
            const payload = {
                quantite_commandee: quantiteInput.value,
                taux_tva: tauxTvaSelect ? tauxTvaSelect.value : null,
            };
            if (prixModifieParUtilisateur && prixInput) {
                payload.prix_vente_unitaire = prixInput.value;
            }

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
                        console.error("Recalcul ligne de commande :", data.detail);
                        showLigneErreur(row, data.detail || "Erreur lors du recalcul.");
                        return;
                    }
                    if (prixInput) {
                        dernierPrixEnvoye = prixInput.value;
                    }
                    updateLigne(row, prixInput, tauxTvaSelect, data);
                })
                .catch(() => {
                    row.style.opacity = "1";
                    console.error("Recalcul ligne de commande : erreur réseau.");
                    showLigneErreur(row, "Erreur réseau lors du recalcul.");
                });
        };

        const recalculerAutomatique = debounce(() => executer(false), 400);
        quantiteInput.addEventListener("input", recalculerAutomatique);
        if (tauxTvaSelect) {
            tauxTvaSelect.addEventListener("change", recalculerAutomatique);
        }
        if (prixInput) {
            // Distingue "le serveur vient de remplir ce champ" (ignoré) d'une
            // vraie frappe de l'utilisateur (envoyée comme surcharge
            // explicite) — sans ça, la valeur que le serveur vient de
            // renvoyer redéclencherait aussitôt un envoi la faisant passer
            // pour une surcharge manuelle.
            const prixModifie = debounce(() => {
                if (prixInput.value === dernierPrixEnvoye) {
                    return;
                }
                executer(true);
            }, 400);
            prixInput.addEventListener("input", prixModifie);
        }
    }

    // Ligne pas encore enregistrée : simple aperçu, ne persiste rien.
    function wireRowNouvelle(row, numero, champs) {
        const { quantiteInput, prixInput, tauxTvaSelect } = champs;
        const articleSelect = row.querySelector('select[name$="-article"]');
        if (!articleSelect) {
            return;
        }

        const url = `/admin/chiffrage/commande/${encodeURIComponent(numero)}/lignes/previsualiser/`;

        const executer = () => {
            const article = articleSelect.value;
            const quantite = quantiteInput.value;
            if (!article || !quantite) {
                return;
            }

            row.style.opacity = "0.6";

            fetch(url, {
                method: "POST",
                credentials: "same-origin",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRFToken": csrfToken(),
                },
                body: JSON.stringify({ article: article, quantite: quantite }),
            })
                .then((response) => response.json().then((data) => ({ status: response.status, data })))
                .then(({ status, data }) => {
                    row.style.opacity = "1";
                    if (status >= 400) {
                        console.error("Aperçu ligne de commande :", data.detail);
                        showLigneErreur(row, data.detail || "Erreur lors de l'aperçu.");
                        return;
                    }
                    updateLigne(row, prixInput, tauxTvaSelect, data);
                })
                .catch(() => {
                    row.style.opacity = "1";
                    console.error("Aperçu ligne de commande : erreur réseau.");
                    showLigneErreur(row, "Erreur réseau lors de l'aperçu.");
                });
        };

        const previsualiser = debounce(executer, 400);
        quantiteInput.addEventListener("input", previsualiser);
        articleSelect.addEventListener("change", previsualiser);

        executer();
    }

    // Ligne sur le formulaire d'AJOUT d'une commande : pas encore de
    // numéro, donc pas d'objet Commande en base — date de commande et
    // client sont lus en direct sur le formulaire.
    function wireRowNouvelleCommande(row) {
        if (row.dataset.liveWired === "1") {
            return;
        }

        const quantiteInput = row.querySelector('input[name$="-quantite_commandee"]');
        if (!quantiteInput || quantiteInput.name.indexOf("__prefix__") !== -1) {
            return;
        }
        const articleSelect = row.querySelector('select[name$="-article"]');
        if (!articleSelect) {
            return;
        }

        row.dataset.liveWired = "1";

        const prixInput = row.querySelector('input[name$="-prix_vente_unitaire"]');
        const tauxTvaSelect = row.querySelector('select[name$="-taux_tva"]');
        const url = "/admin/chiffrage/commande/nouvelle-commande/previsualiser-ligne/";

        const executer = () => {
            const article = articleSelect.value;
            const quantite = quantiteInput.value;
            if (!article || !quantite) {
                return;
            }

            const dateCommandeInput = document.querySelector('[name="date_commande"]');
            const clientSelect = document.querySelector('[name="client"]');

            const payload = {
                article: article,
                quantite: quantite,
                date_commande: dateCommandeInput ? dateCommandeInput.value : null,
                client: clientSelect ? clientSelect.value : null,
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
                        console.error("Aperçu ligne de commande (nouvelle commande) :", data.detail);
                        showLigneErreur(row, data.detail || "Erreur lors de l'aperçu.");
                        return;
                    }
                    updateLigne(row, prixInput, tauxTvaSelect, data);
                })
                .catch(() => {
                    row.style.opacity = "1";
                    console.error("Aperçu ligne de commande (nouvelle commande) : erreur réseau.");
                    showLigneErreur(row, "Erreur réseau lors de l'aperçu.");
                });
        };

        const previsualiser = debounce(executer, 400);
        quantiteInput.addEventListener("input", previsualiser);
        articleSelect.addEventListener("change", previsualiser);

        executer();
    }
})();
