// Touche Entrée dans un formulaire : passe au champ suivant (comme Tab) au lieu d'envoyer le
// formulaire, qui déclenchait « Enregistrer et fermer » (premier bouton d'envoi). Maj+Entrée
// revient au champ précédent. Entrée garde son rôle dans une zone de texte multiligne, sur un
// bouton ou un lien, dans les formulaires de recherche/filtre (GET) et sur la page de connexion.
// Les éditeurs qui gèrent déjà Entrée (preventDefault) ne sont pas touchés.
(function () {
    "use strict";

    const CANDIDATS = "input, select, textarea, .select2-selection[tabindex], [contenteditable=''], [contenteditable='true']";
    const TYPES_IGNORES = ["hidden", "submit", "button", "reset", "image", "file"];

    function formulaireConcerne(el) {
        const form = el.form || el.closest("form");
        if (!form || form.id === "login-form") return null;
        if ((form.getAttribute("method") || "get").toLowerCase() !== "post") return null;
        return form;
    }

    function atteignable(el) {
        if (el.disabled || el.readOnly || el.tabIndex < 0) return false;
        if (el.tagName === "INPUT" && TYPES_IGNORES.includes(el.type)) return false;
        if (el.classList.contains("select2-hidden-accessible")) return false;
        if (!el.getClientRects().length) return false;
        return getComputedStyle(el).visibility !== "hidden";
    }

    function champsDu(form) {
        return [...document.querySelectorAll(CANDIDATS)].filter((el) => (el.form || el.closest("form")) === form && atteignable(el));
    }

    document.addEventListener("keydown", function (event) {
        if (event.key !== "Enter" || event.defaultPrevented || event.isComposing) return;
        if (event.ctrlKey || event.metaKey || event.altKey) return;
        const el = event.target;
        if (!(el instanceof HTMLElement) || el.tagName === "TEXTAREA" || el.isContentEditable) return;
        if (el.tagName !== "INPUT" && el.tagName !== "SELECT") return;
        if (el.tagName === "INPUT" && TYPES_IGNORES.includes(el.type)) return;
        const form = formulaireConcerne(el);
        if (!form) return;

        event.preventDefault();
        const champs = champsDu(form);
        const i = champs.indexOf(el);
        const suivant = champs[i + (event.shiftKey ? -1 : 1)];
        if (!suivant) return; // dernier champ : on reste dessus, rien n'est envoyé
        suivant.focus();
        if (suivant.tagName === "INPUT" && typeof suivant.select === "function" && /^(text|search|tel|url|email|password|number)$/.test(suivant.type)) {
            try { suivant.select(); } catch (e) { /* type sans sélection */ }
        }
    });
})();
