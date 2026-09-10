(function () {
    "use strict";

    // Recherche d'entreprises (recherche-entreprises.api.gouv.fr) : service
    // public gratuit, sans clé, basé sur la base SIRENE — pensé pour un
    // appel direct depuis le navigateur (CORS ouvert), comme
    // adresse_autocomplete.js pour l'API Adresse. Uniquement sur la fiche
    // Tiers : "Raison sociale" et "SIRET" en sont les seuls champs, chacun
    // sert de point d'entrée à l'autre — et à l'adresse du siège.
    const URL_RECHERCHE = "https://recherche-entreprises.api.gouv.fr/search";
    const LONGUEUR_MIN_RAISON_SOCIALE = 3;

    document.addEventListener("DOMContentLoaded", init);

    function init() {
        const champRaisonSociale = document.querySelector("#id_raison_sociale");
        const champSiret = document.querySelector("#id_siret");
        if (!champRaisonSociale && !champSiret) {
            return;
        }
        if (champRaisonSociale) {
            champRaisonSociale.addEventListener("input", debounce(onRaisonSocialeInput, 400));
            champRaisonSociale.addEventListener("focusout", () => {
                setTimeout(() => fermerSuggestions(champRaisonSociale), 150);
            });
            champRaisonSociale.addEventListener("keydown", (event) => {
                if (event.key === "Escape") {
                    fermerSuggestions(champRaisonSociale);
                }
            });
        }
        if (champSiret) {
            champSiret.addEventListener("input", debounce(onSiretInput, 400));
        }
    }

    function debounce(fn, delay) {
        let timer = null;
        return function (...args) {
            clearTimeout(timer);
            timer = setTimeout(() => fn.apply(this, args), delay);
        };
    }

    function rechercherEntreprises(q) {
        const url = `${URL_RECHERCHE}?q=${encodeURIComponent(q)}&per_page=7`;
        return fetch(url)
            .then((response) => (response.ok ? response.json() : { results: [] }))
            .then((data) => data.results || [])
            .catch(() => []);
    }

    // --- Raison sociale tapée -> propose SIRET + adresse ------------------

    function onRaisonSocialeInput(event) {
        const input = event.target;
        const requete = input.value.trim();
        if (requete.length < LONGUEUR_MIN_RAISON_SOCIALE) {
            fermerSuggestions(input);
            return;
        }
        rechercherEntreprises(requete).then((resultats) => {
            // Ignore une réponse devenue obsolète (l'utilisateur a continué
            // à taper depuis l'envoi de cette requête).
            if (input.value.trim() !== requete) {
                return;
            }
            afficherSuggestions(input, resultats);
        });
    }

    function afficherSuggestions(input, resultats) {
        fermerSuggestions(input);
        if (resultats.length === 0) {
            return;
        }

        const liste = document.createElement("ul");
        liste.className =
            "entreprise-suggestions text-sm bg-white dark:bg-base-900 border border-base-200 " +
            "dark:border-base-700 rounded-default shadow-lg max-h-60 overflow-auto";
        liste.style.position = "absolute";
        liste.style.zIndex = "50";
        liste.style.top = "100%";
        liste.style.left = "0";
        liste.style.minWidth = "320px";
        liste.style.marginTop = "2px";

        resultats.forEach((resultat) => {
            const adresse = adresseSiege(resultat.siege);
            const item = document.createElement("li");
            item.className = "px-3 py-2 cursor-pointer hover:bg-base-100 dark:hover:bg-base-800";
            const libelleAdresse = adresse
                ? [adresse.adresse, adresse.code_postal, adresse.ville].filter(Boolean).join(" ")
                : "";
            item.innerHTML =
                `<div class="font-medium">${echapper(resultat.nom_complet || resultat.nom_raison_sociale || "")}</div>` +
                `<div class="text-font-subtle-light dark:text-font-subtle-dark">${echapper(libelleAdresse)}</div>`;
            // mousedown (pas click) : se déclenche avant le focusout de
            // l'input, qui fermerait sinon la liste avant la sélection.
            item.addEventListener("mousedown", (event) => {
                event.preventDefault();
                appliquerEntreprise(resultat, { champOrigine: "raison_sociale" });
                fermerSuggestions(input);
            });
            liste.appendChild(item);
        });

        const parent = input.parentElement;
        if (parent && getComputedStyle(parent).position === "static") {
            parent.style.position = "relative";
        }
        input.insertAdjacentElement("afterend", liste);
        input._suggestionsEntreprise = liste;
    }

    function fermerSuggestions(input) {
        if (input._suggestionsEntreprise) {
            input._suggestionsEntreprise.remove();
            input._suggestionsEntreprise = null;
        }
    }

    function echapper(texte) {
        const div = document.createElement("div");
        div.textContent = texte || "";
        return div.innerHTML;
    }

    // --- SIREN/SIRET tapé -> propose raison sociale + adresse -------------

    function onSiretInput(event) {
        const input = event.target;
        const chiffres = input.value.replace(/\s+/g, "");
        // 9 chiffres (SIREN) ou 14 (SIRET complet) : en dessous, une requête
        // ne donnerait que du bruit.
        if (!/^\d{9}$/.test(chiffres) && !/^\d{14}$/.test(chiffres)) {
            return;
        }
        const siren = chiffres.slice(0, 9);
        rechercherEntreprises(chiffres).then((resultats) => {
            if (input.value.replace(/\s+/g, "") !== chiffres) {
                return;
            }
            const correspondance = resultats.find((resultat) => resultat.siren === siren);
            if (correspondance) {
                appliquerEntreprise(correspondance, { champOrigine: "siret" });
            }
        });
    }

    // --- Application commune aux deux sens ---------------------------------

    function appliquerEntreprise(resultat, { champOrigine }) {
        const champRaisonSociale = document.querySelector("#id_raison_sociale");
        const champSiret = document.querySelector("#id_siret");
        const siretSiege = resultat.siege && resultat.siege.siret;

        if (champRaisonSociale && (champOrigine === "raison_sociale" || !champRaisonSociale.value.trim())) {
            champRaisonSociale.value = resultat.nom_complet || resultat.nom_raison_sociale || champRaisonSociale.value;
        }
        if (champSiret && siretSiege) {
            champSiret.value = siretSiege;
        }

        proposerAdresseSiege(resultat.siege);
    }

    // N'ajoute une ligne au tableau Adresses que s'il est encore vide : une
    // fois qu'une adresse existe (même partiellement remplie), on ne sait
    // plus si c'est celle du siège ou une autre — mieux vaut ne rien
    // écraser plutôt que de deviner.
    function proposerAdresseSiege(siege) {
        const adresse = adresseSiege(siege);
        if (!adresse || !adresse.adresse) {
            return;
        }
        const totalForms = document.querySelector('input[name="adresses-TOTAL_FORMS"]');
        const boutonAjouter = document.querySelector("#adresses-data .add-row");
        if (!totalForms || !boutonAjouter || parseInt(totalForms.value, 10) !== 0) {
            return;
        }

        document.addEventListener(
            "formset:added",
            (event) => {
                if (event.detail && event.detail.formsetName !== "adresses") {
                    return;
                }
                remplirLigneAdresseSiege(adresse);
            },
            { once: true }
        );
        boutonAjouter.click();
    }

    function adresseSiege(siege) {
        if (!siege) {
            return null;
        }
        const rue = [siege.numero_voie, siege.type_voie, siege.libelle_voie].filter(Boolean).join(" ");
        return {
            adresse: rue || (siege.adresse || "").trim(),
            code_postal: siege.code_postal || "",
            ville: siege.libelle_commune || "",
        };
    }

    function remplirLigneAdresseSiege(adresse) {
        const typeSelect = document.querySelector('select[name="adresses-0-type_adresse"]');
        const libelleInput = document.querySelector('input[name="adresses-0-libelle"]');
        const adresseInput = document.querySelector('input[name="adresses-0-adresse"]');
        const codePostalInput = document.querySelector('input[name="adresses-0-code_postal"]');
        const villeInput = document.querySelector('input[name="adresses-0-ville"]');
        if (!typeSelect || !adresseInput) {
            return;
        }

        typeSelect.value = "livraison";
        if (libelleInput) {
            libelleInput.value = "Siège";
        }
        adresseInput.value = adresse.adresse;
        if (codePostalInput) {
            codePostalInput.value = adresse.code_postal;
        }
        if (villeInput) {
            villeInput.value = adresse.ville;
        }

        // tiers_admin.js reconstruit les options "Adresse associée" des
        // contacts sur ces mêmes événements (délégués au document) — cette
        // ligne vient d'apparaître hors de toute frappe utilisateur.
        typeSelect.dispatchEvent(new Event("input", { bubbles: true }));
        typeSelect.dispatchEvent(new Event("change", { bubbles: true }));
    }
})();
