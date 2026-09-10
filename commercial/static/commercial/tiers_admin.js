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
        if (!/^[A-Za-z]{5}$/.test(valeur)) {
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
})();
