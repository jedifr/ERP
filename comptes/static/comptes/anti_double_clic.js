// Empêche le double envoi d'un formulaire (double-clic sur « Enregistrer », Entrée répétée).
// On bloque les envois suivants au lieu de désactiver le bouton : un bouton désactivé n'est
// pas transmis, et le serveur perdrait l'information « Enregistrer » vs « Enregistrer et
// continuer ».
(function () {
    "use strict";

    document.addEventListener("submit", function (event) {
        const form = event.target;
        if (!(form instanceof HTMLFormElement) || (form.method || "").toLowerCase() !== "post") {
            return;
        }
        if (form.dataset.envoiEnCours === "1") {
            event.preventDefault();
            return;
        }
        form.dataset.envoiEnCours = "1";
        form.setAttribute("aria-busy", "true");
        // Filet : si l'envoi n'aboutit pas (réseau coupé), le formulaire redevient utilisable.
        window.setTimeout(function () {
            form.dataset.envoiEnCours = "0";
            form.removeAttribute("aria-busy");
        }, 10000);
    });

    // Retour arrière du navigateur (page restaurée depuis le cache) : formulaire de nouveau libre.
    window.addEventListener("pageshow", function () {
        document.querySelectorAll("form[data-envoi-en-cours]").forEach(function (form) {
            form.dataset.envoiEnCours = "0";
            form.removeAttribute("aria-busy");
        });
    });
})();
