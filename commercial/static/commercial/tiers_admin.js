(function () {
    "use strict";

    document.addEventListener("DOMContentLoaded", init);

    // { nomChamp: nom du champ de formulaire (suffixe, ex.
    // "comptes_comptables-0-code_client"), prefixe: début du compte généré
    // (TiersCompteComptable.save()) } — voir comptabilite/models.py.
    const CHAMPS = [
        { nomChamp: "code_client", prefixe: "411" },
        { nomChamp: "code_fournisseur", prefixe: "401" },
    ];

    function init() {
        // Délégation d'événement sur le document : couvre aussi bien les
        // champs déjà présents au chargement que ceux qu'un futur "Ajouter
        // un objet Compte comptable de tiers supplémentaire" insérerait
        // (clonage DOM Django), sans avoir à écouter "formset:added".
        document.addEventListener("input", debounce(onInput, 300));
        CHAMPS.forEach(({ nomChamp }) => {
            document.querySelectorAll(`input[name$="-${nomChamp}"]`).forEach(rafraichirApercu);
        });

        initAdresseLivraison();
    }

    function debounce(fn, delay) {
        let timer = null;
        return function (...args) {
            clearTimeout(timer);
            timer = setTimeout(() => fn.apply(this, args), delay);
        };
    }

    function champPour(input) {
        if (!input || !input.name) {
            return null;
        }
        return CHAMPS.find(({ nomChamp }) => input.name.endsWith(`-${nomChamp}`)) || null;
    }

    function onInput(event) {
        if (champPour(event.target)) {
            rafraichirApercu(event.target);
        }
    }

    function apercuElement(input) {
        let el = input.parentElement.querySelector(".apercu-compte-comptable");
        if (!el) {
            el = document.createElement("div");
            el.className = "apercu-compte-comptable text-sm text-font-subtle-light dark:text-font-subtle-dark mt-1";
            input.insertAdjacentElement("afterend", el);
        }
        return el;
    }

    function rafraichirApercu(input) {
        const champ = champPour(input);
        if (!champ) {
            return;
        }
        const el = apercuElement(input);
        const valeur = input.value.trim();
        if (!/^[A-Za-z0-9]{5}$/.test(valeur)) {
            el.textContent = "";
            return;
        }
        el.textContent = "…";
        const url = `/admin/commercial/tiers/apercu-compte-comptable/?prefixe=${champ.prefixe}&code=${encodeURIComponent(valeur)}`;
        fetch(url, { credentials: "same-origin" })
            .then((response) => response.json())
            .then((data) => {
                // La réponse peut arriver après que l'utilisateur ait déjà
                // continué à taper : on ignore une réponse devenue obsolète.
                if (input.value.trim() !== valeur) {
                    return;
                }
                if (!data.valide) {
                    el.textContent = "";
                    return;
                }
                el.textContent = data.existe
                    ? `→ ${data.code} — ${data.libelle} (compte existant)`
                    : `→ ${data.code} (sera créé)`;
            })
            .catch(() => {
                el.textContent = "";
            });
    }

    // « Adresse de livraison associée » (tableau Contacts) : le <select>
    // n'est jamais alimenté depuis la base (voir ContactInlineForm côté
    // Python) — ses options sont reconstruites ici à partir des lignes
    // actuellement affichées dans le tableau Adresses (type Livraison
    // uniquement, même non enregistrées), pour pouvoir lier un contact à
    // une adresse tout juste ajoutée, dans le même enregistrement.
    function initAdresseLivraison() {
        if (!document.querySelector('select[name$="-adresse_livraison_ref"]')) {
            return;
        }
        const rafraichir = debounce(rafraichirOptionsAdresseLivraison, 300);
        document.addEventListener("input", (event) => {
            if (event.target.name && event.target.name.startsWith("adresses-")) {
                rafraichir();
            }
        });
        document.addEventListener("change", (event) => {
            if (event.target.name && event.target.name.startsWith("adresses-")) {
                rafraichir();
            }
        });
        document.addEventListener("formset:added", () => rafraichirOptionsAdresseLivraison());
        rafraichirOptionsAdresseLivraison();
    }

    function indexDeLigneAdresse(input) {
        const m = input.name.match(/^adresses-(\d+)-/);
        return m ? parseInt(m[1], 10) : null;
    }

    function ligneEstSupprimee(row) {
        const suppression = row.querySelector('input[type="checkbox"][name$="-DELETE"]');
        return !!(suppression && suppression.checked);
    }

    function collecterLignesAdresseLivraison() {
        const lignes = [];
        document.querySelectorAll("tbody.form-group").forEach((row) => {
            const typeSelect = row.querySelector('select[name^="adresses-"][name$="-type_adresse"]');
            if (!typeSelect || typeSelect.value !== "livraison" || ligneEstSupprimee(row)) {
                return;
            }
            const index = indexDeLigneAdresse(typeSelect);
            if (index === null) {
                return;
            }
            const libelleInput = row.querySelector('input[name$="-libelle"]');
            const villeInput = row.querySelector('input[name$="-ville"]');
            const libelle = (libelleInput && libelleInput.value) || (villeInput && villeInput.value) || `Adresse ${index + 1}`;
            lignes.push({ index, libelle });
        });
        return lignes;
    }

    function trouverIndexParPk(pk) {
        let trouve = null;
        document.querySelectorAll('input[name^="adresses-"][name$="-id"]').forEach((idInput) => {
            if (idInput.value && idInput.value === String(pk)) {
                trouve = indexDeLigneAdresse(idInput);
            }
        });
        return trouve;
    }

    function rafraichirOptionsAdresseLivraison() {
        const lignes = collecterLignesAdresseLivraison();
        document.querySelectorAll('select[name$="-adresse_livraison_ref"]').forEach((select) => {
            const valeurActuelle = select.value;
            select.innerHTML = "";
            select.appendChild(new Option("---------", ""));
            lignes.forEach(({ index, libelle }) => {
                select.appendChild(new Option(libelle, String(index)));
            });

            const options = Array.from(select.options).map((o) => o.value);
            if (valeurActuelle && options.includes(valeurActuelle)) {
                select.value = valeurActuelle;
                return;
            }
            // Premier rendu seulement : tente de retrouver la ligne
            // correspondant à l'adresse déjà liée (édition d'un contact
            // existant) — comparée par pk, pas par indice (voir
            // ContactInlineForm côté Python).
            if (select.dataset.preselectionFaite) {
                return;
            }
            select.dataset.preselectionFaite = "1";
            const pkActuel = select.dataset.adresseLivraisonActuelle;
            if (pkActuel) {
                const index = trouverIndexParPk(pkActuel);
                if (index !== null && options.includes(String(index))) {
                    select.value = String(index);
                }
            }
        });
    }
})();
