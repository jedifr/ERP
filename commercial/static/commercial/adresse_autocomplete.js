(function () {
    "use strict";

    // API Adresse (data.gouv.fr / Base Adresse Nationale, Etalab) : service
    // public gratuit, sans clé, pensé pour un appel direct depuis le
    // navigateur (CORS ouvert) — pas de détour par le backend nécessaire.
    // Ne couvre que la France : les adresses hors France restent à saisir
    // à la main (le champ Pays le dit déjà).
    const URL_RECHERCHE = "https://api-adresse.data.gouv.fr/search/";
    const LONGUEUR_MIN = 3;

    document.addEventListener("DOMContentLoaded", init);

    function init() {
        document.addEventListener("input", debounce(onInput, 300));
        document.addEventListener("focusout", onFocusOut);
        document.addEventListener("keydown", onKeydown, true);
    }

    function debounce(fn, delay) {
        let timer = null;
        return function (...args) {
            clearTimeout(timer);
            timer = setTimeout(() => fn.apply(this, args), delay);
        };
    }

    // Vrai pour le champ "Adresse" du modèle Adresse, qu'il soit affiché
    // seul (fiche Adresse autonome, name="adresse") ou dans une ligne de
    // tableau imbriqué (fiche Tiers, name="adresses-0-adresse").
    function champAdresse(input) {
        return !!(
            input &&
            input.tagName === "INPUT" &&
            input.name &&
            (input.name === "adresse" || input.name.endsWith("-adresse"))
        );
    }

    function onInput(event) {
        const input = event.target;
        if (!champAdresse(input)) {
            return;
        }
        const requete = input.value.trim();
        if (requete.length < LONGUEUR_MIN) {
            fermerSuggestions(input);
            return;
        }
        const url = `${URL_RECHERCHE}?q=${encodeURIComponent(requete)}&limit=5&autocomplete=1`;
        fetch(url)
            .then((response) => response.json())
            .then((data) => {
                // Ignore une réponse devenue obsolète (l'utilisateur a
                // continué à taper depuis l'envoi de cette requête).
                if (input.value.trim() !== requete) {
                    return;
                }
                afficherSuggestions(input, data.features || []);
            })
            .catch(() => {
                fermerSuggestions(input);
            });
    }

    function afficherSuggestions(input, suggestions) {
        fermerSuggestions(input);
        if (suggestions.length === 0) {
            return;
        }

        const liste = document.createElement("ul");
        liste.className =
            "adresse-suggestions text-sm bg-white dark:bg-base-900 border border-base-200 " +
            "dark:border-base-700 rounded-default shadow-lg max-h-60 overflow-auto";
        liste.style.position = "absolute";
        liste.style.zIndex = "50";
        liste.style.top = "100%";
        liste.style.left = "0";
        liste.style.minWidth = "320px";
        liste.style.marginTop = "2px";

        suggestions.forEach((suggestion) => {
            const item = document.createElement("li");
            item.className = "px-3 py-2 cursor-pointer hover:bg-base-100 dark:hover:bg-base-800";
            item.textContent = suggestion.properties.label;
            // mousedown (pas click) : se déclenche avant le focusout de
            // l'input, qui fermerait sinon la liste avant la sélection.
            item.addEventListener("mousedown", (event) => {
                event.preventDefault();
                selectionnerSuggestion(input, suggestion);
            });
            liste.appendChild(item);
        });

        const parent = input.parentElement;
        if (parent && getComputedStyle(parent).position === "static") {
            parent.style.position = "relative";
        }
        input.insertAdjacentElement("afterend", liste);
        input._suggestionsAdresse = liste;
    }

    function fermerSuggestions(input) {
        if (input._suggestionsAdresse) {
            input._suggestionsAdresse.remove();
            input._suggestionsAdresse = null;
        }
    }

    function selectionnerSuggestion(input, suggestion) {
        const props = suggestion.properties;
        input.value = props.name || props.label;
        remplirChampFrere(input, "code_postal", props.postcode);
        remplirChampFrere(input, "ville", props.city);
        fermerSuggestions(input);
    }

    // "adresses-0-adresse" -> "adresses-0-code_postal" ; "adresse" ->
    // "code_postal" : construit le nom du champ frère à partir de celui
    // du champ Adresse qui vient de servir à la recherche.
    function nomChampFrere(nomChampAdresse, nomCible) {
        if (nomChampAdresse === "adresse") {
            return nomCible;
        }
        return nomChampAdresse.slice(0, -"adresse".length) + nomCible;
    }

    function remplirChampFrere(inputAdresse, nomCible, valeur) {
        if (!valeur) {
            return;
        }
        const cible = document.querySelector(`input[name="${nomChampFrere(inputAdresse.name, nomCible)}"]`);
        if (cible) {
            cible.value = valeur;
        }
    }

    function onFocusOut(event) {
        const input = event.target;
        if (!champAdresse(input)) {
            return;
        }
        // Laisse le temps au mousedown d'une suggestion de s'exécuter
        // avant de fermer (voir selectionnerSuggestion ci-dessus).
        setTimeout(() => fermerSuggestions(input), 150);
    }

    function onKeydown(event) {
        if (event.key === "Escape" && champAdresse(event.target)) {
            fermerSuggestions(event.target);
        }
    }
})();
