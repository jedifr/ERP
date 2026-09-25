(function () {
    "use strict";

    function csrfToken() {
        const el = document.querySelector("[name=csrfmiddlewaretoken]");
        return el ? el.value : "";
    }

    const ID_CHAMPS = {
        format_source: "decoupe-format-source",
        statut: "decoupe-statut",
        message_erreur: "decoupe-message-erreur",
        avertissements: "decoupe-avertissements",
        surface_mm2: "decoupe-surface-mm2",
        perimetre_decoupe_mm: "decoupe-perimetre-mm",
        largeur_mm: "decoupe-largeur-mm",
        hauteur_mm: "decoupe-hauteur-mm",
        nb_contours_interieurs: "decoupe-nb-contours",
    };

    function majChamp(nomChamp, texte) {
        const el = document.getElementById(ID_CHAMPS[nomChamp]);
        if (el) el.textContent = texte;
    }

    function formatNombre(valeur, decimales) {
        return valeur == null ? "-" : Number(valeur).toFixed(decimales);
    }

    function analyserFichier(fichier) {
        const apercu = document.getElementById("decoupe-apercu");
        if (apercu) apercu.textContent = "Analyse en cours...";

        const donnees = new FormData();
        donnees.append("fichier_source", fichier);

        fetch("/admin/decoupe/piecedecoupe/analyser/", {
            method: "POST",
            credentials: "same-origin",
            headers: { "X-CSRFToken": csrfToken() },
            body: donnees,
        })
            .then((response) => response.json())
            .then((data) => {
                majChamp("format_source", data.format_source_display || "-");
                majChamp("statut", data.statut_display || "-");
                majChamp("message_erreur", data.message_erreur || "-");
                majChamp(
                    "avertissements",
                    data.avertissements && data.avertissements.length ? data.avertissements.join(" ") : "-"
                );

                if (!data.ok) {
                    if (apercu) apercu.textContent = "-";
                    ["surface_mm2", "perimetre_decoupe_mm", "largeur_mm", "hauteur_mm", "nb_contours_interieurs"].forEach(
                        (champ) => majChamp(champ, "-")
                    );
                    return;
                }

                majChamp("surface_mm2", formatNombre(data.surface_mm2, 2));
                majChamp("perimetre_decoupe_mm", formatNombre(data.perimetre_decoupe_mm, 2));
                majChamp("largeur_mm", formatNombre(data.largeur_mm, 1));
                majChamp("hauteur_mm", formatNombre(data.hauteur_mm, 1));
                majChamp("nb_contours_interieurs", data.nb_contours_interieurs);

                if (apercu) apercu.innerHTML = data.svg || "-";
            })
            .catch(() => {
                if (apercu) apercu.textContent = "Erreur réseau lors de l'analyse.";
            });
    }

    document.addEventListener("DOMContentLoaded", function () {
        const input = document.getElementById("id_fichier_source");
        if (!input) return;
        input.addEventListener("change", function () {
            if (input.files && input.files[0]) {
                analyserFichier(input.files[0]);
            }
        });
    });
})();
