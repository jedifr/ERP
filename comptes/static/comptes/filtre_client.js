// Sur les fiches Devis et Commande : les listes « adresse de facturation », « adresse de livraison » et
// « contact » ne proposent que ce qui appartient au client choisi. On ajoute le client aux requêtes
// d'autocomplétion de ces trois champs ; le serveur (AdresseAdmin / ContactAdmin.get_search_results) filtre.
(function () {
    "use strict";

    const CHAMPS = ["adresse_facturation", "adresse_livraison", "contact"];

    function brancher($) {
        if (!$ || !$.ajaxPrefilter || $.__filtreClientBranche) {
            return;
        }
        $.__filtreClientBranche = true;
        $.ajaxPrefilter(function (options) {
            if (!options.url || options.url.indexOf("/autocomplete/") < 0 || typeof options.data !== "string") {
                return;
            }
            const m = options.data.match(/(?:^|&)field_name=([^&]+)/);
            if (!m || CHAMPS.indexOf(decodeURIComponent(m[1])) < 0) {
                return;
            }
            const client = document.getElementById("id_client");
            if (client && client.value) {
                options.data += "&tiers=" + encodeURIComponent(client.value);
            }
        });
    }

    document.addEventListener("DOMContentLoaded", function () {
        brancher(window.django && window.django.jQuery);
        brancher(window.jQuery);
    });
})();
