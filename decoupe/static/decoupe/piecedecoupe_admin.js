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
        calques_detectes: "decoupe-calques",
        a_gravure: "decoupe-a-gravure",
        longueur_gravure_mm: "decoupe-longueur-gravure",
    };

    function majChamp(nomChamp, texte) {
        const el = document.getElementById(ID_CHAMPS[nomChamp]);
        if (el) el.textContent = texte;
    }

    function formatNombre(valeur, decimales) {
        return valeur == null ? "-" : Number(valeur).toFixed(decimales);
    }

    function dernierFichierSelectionne() {
        const input = document.getElementById("id_fichier_source");
        return input && input.files && input.files[0] ? input.files[0] : null;
    }

    function decocherSymetrieSiGravure(aGravure) {
        const case_ = document.getElementById("id_symetrie_autorisee");
        if (case_ && aGravure && case_.checked) {
            case_.checked = false;
            case_.dispatchEvent(new Event("change", { bubbles: true }));
        }
    }

    function analyserFichier(fichier) {
        const apercu = document.getElementById("decoupe-apercu");
        if (apercu) apercu.textContent = "Analyse en cours...";

        const donnees = new FormData();
        donnees.append("fichier_source", fichier);
        const profilSelect = document.getElementById("id_profil_import");
        if (profilSelect && profilSelect.value) {
            donnees.append("profil_import", profilSelect.value);
        }

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
                    [
                        "surface_mm2",
                        "perimetre_decoupe_mm",
                        "largeur_mm",
                        "hauteur_mm",
                        "nb_contours_interieurs",
                        "calques_detectes",
                        "a_gravure",
                        "longueur_gravure_mm",
                    ].forEach((champ) => majChamp(champ, "-"));
                    return;
                }

                majChamp("surface_mm2", formatNombre(data.surface_mm2, 2));
                majChamp("perimetre_decoupe_mm", formatNombre(data.perimetre_decoupe_mm, 2));
                majChamp("largeur_mm", formatNombre(data.largeur_mm, 1));
                majChamp("hauteur_mm", formatNombre(data.hauteur_mm, 1));
                majChamp("nb_contours_interieurs", data.nb_contours_interieurs);
                majChamp(
                    "calques_detectes",
                    data.calques_detectes && data.calques_detectes.length ? data.calques_detectes.join(", ") : "-"
                );
                majChamp("a_gravure", data.a_gravure ? "Oui" : "Non");
                majChamp("longueur_gravure_mm", formatNombre(data.longueur_gravure_mm, 2));
                decocherSymetrieSiGravure(data.a_gravure);

                if (apercu) apercu.innerHTML = data.svg || "-";
            })
            .catch(() => {
                if (apercu) apercu.textContent = "Erreur réseau lors de l'analyse.";
            });
    }

    document.addEventListener("DOMContentLoaded", function () {
        const input = document.getElementById("id_fichier_source");
        if (input) {
            input.addEventListener("change", function () {
                const fichier = dernierFichierSelectionne();
                if (fichier) analyserFichier(fichier);
            });
        }

        // Changer de profil d'import doit ré-analyser le fichier déjà choisi (le classement
        // des calques en dépend) — nécessite jQuery pour capter le "change" émis par le
        // widget select2 de l'autocomplete, comme pour les champs client/article ailleurs
        // dans l'admin (un addEventListener natif ne le reçoit pas toujours).
        if (typeof django !== "undefined" && django.jQuery) {
            django.jQuery("#id_profil_import").on("change", function () {
                const fichier = dernierFichierSelectionne();
                if (fichier) analyserFichier(fichier);
            });
        }
    });
})();
