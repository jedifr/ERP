(function () {
    "use strict";

    document.addEventListener("DOMContentLoaded", init);

    function init() {
        if (typeof django === "undefined" || !django.jQuery) {
            return;
        }
        const $ = django.jQuery;
        const $commande = $("#id_commande");
        if ($commande.length === 0) {
            return;
        }

        $commande.on("change", function () {
            const numero = $commande.val();
            if (!numero) {
                return;
            }
            fetch(`/admin/facturation/facture/${encodeURIComponent(numero)}/montants-calcules/`, {
                credentials: "same-origin",
            })
                .then((response) => response.json())
                .then((data) => {
                    remplirSiVide("#id_montant_ht", data.montant_ht);
                    remplirSiVide("#id_montant_ttc", data.montant_ttc);
                })
                .catch(() => {
                    console.error("Montants calculés de la commande : erreur réseau.");
                });
        });
    }

    // Ne renseigne le champ que s'il est encore vide — jamais au détriment
    // d'un montant déjà saisi à la main (même principe que
    // chiffrage/devis_admin_live.js).
    function remplirSiVide(selector, valeur) {
        if (valeur === null || valeur === undefined) {
            return;
        }
        const input = document.querySelector(selector);
        if (!input || input.value) {
            return;
        }
        input.value = valeur;
    }
})();
