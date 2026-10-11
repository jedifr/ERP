(function () {
    "use strict";

    function csrfToken() {
        const el = document.querySelector("[name=csrfmiddlewaretoken]");
        return el ? el.value : "";
    }

    function debounce(fn, delay) {
        let timer = null;
        return function (...args) {
            clearTimeout(timer);
            timer = setTimeout(() => fn.apply(this, args), delay);
        };
    }

    function formatNombre(valeur, decimales) {
        return valeur == null ? "-" : Number(valeur).toFixed(decimales);
    }

    function majTexte(id, texte) {
        const el = document.getElementById(id);
        if (el) el.textContent = texte;
    }

    function collecterLignes() {
        const total = document.getElementById("id_lignes-TOTAL_FORMS");
        if (!total) return [];
        const nb = parseInt(total.value, 10) || 0;
        const lignes = [];
        for (let i = 0; i < nb; i++) {
            const pieceEl = document.getElementById(`id_lignes-${i}-piece`);
            const quantiteEl = document.getElementById(`id_lignes-${i}-quantite`);
            if (!pieceEl || !quantiteEl || !pieceEl.value) continue;
            const deleteEl = document.getElementById(`id_lignes-${i}-DELETE`);
            if (deleteEl && deleteEl.checked) continue;
            lignes.push({ piece: pieceEl.value, quantite: quantiteEl.value });
        }
        return lignes;
    }

    function rendreFeuilles(feuilles) {
        const conteneur = document.getElementById("decoupe-apercu-imbrication");
        if (!conteneur) return;
        const style =
            "<style>#decoupe-apercu-imbrication svg " +
            "{ width: 100%; max-width: 480px; height: auto; display: block; }</style>";
        const blocs = feuilles.length
            ? feuilles
                  .map(
                      (f) =>
                          `<div style="margin-bottom: 1rem;">` +
                          `<div style="font-weight: 600; margin-bottom: 0.25rem;">Feuille ${f.numero}</div>` +
                          `${f.svg || "—"}</div>`
                  )
                  .join("")
            : "Aucune feuille calculée pour l'instant.";
        conteneur.innerHTML = style + blocs;
    }

    function recalculer() {
        const largeurEl = document.getElementById("id_largeur_feuille_mm");
        const longueurEl = document.getElementById("id_longueur_feuille_mm");
        if (!largeurEl || !longueurEl) return;

        const champ = (id) => {
            const el = document.getElementById(id);
            return el ? el.value : null;
        };

        const payload = {
            largeur_feuille_mm: largeurEl.value,
            longueur_feuille_mm: longueurEl.value,
            marge_bord_mm: champ("id_marge_bord_mm"),
            espacement_pieces_mm: champ("id_espacement_pieces_mm"),
            direction: champ("id_direction"),
            coin_depart: champ("id_coin_depart"),
            article_matiere: champ("id_article_matiere"),
            lignes: collecterLignes(),
        };

        fetch("/admin/decoupe/imbricationjob/previsualiser/", {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
            body: JSON.stringify(payload),
        })
            .then((response) => response.json())
            .then((data) => {
                if (!data.ok) return;
                majTexte("decoupe-imb-nb-feuilles", data.nb_feuilles || "-");
                majTexte("decoupe-imb-surface-pieces", formatNombre(data.surface_pieces_mm2, 2));
                majTexte("decoupe-imb-surface-feuilles", formatNombre(data.surface_feuilles_mm2, 2));
                majTexte("decoupe-imb-taux", formatNombre(data.taux_utilisation_pct, 2));
                majTexte(
                    "decoupe-imb-cout",
                    data.cout_matiere_estime == null ? "-" : formatNombre(data.cout_matiere_estime, 2)
                );
                majTexte(
                    "decoupe-imb-non-placees",
                    data.pieces_non_placees && data.pieces_non_placees.length
                        ? data.pieces_non_placees.join(", ")
                        : "-"
                );
                rendreFeuilles(data.feuilles || []);
            })
            .catch(() => {
                console.error("Aperçu imbrication : erreur réseau.");
            });
    }

    const recalculerDifferee = debounce(recalculer, 400);

    document.addEventListener("DOMContentLoaded", function () {
        document.addEventListener("input", function (event) {
            if (
                event.target.matches &&
                event.target.matches(
                    "#id_largeur_feuille_mm, #id_longueur_feuille_mm, #id_marge_bord_mm, " +
                        '#id_espacement_pieces_mm, [name$="-quantite"]'
                )
            ) {
                recalculerDifferee();
            }
        });
        document.addEventListener("change", function (event) {
            if (
                event.target.matches &&
                event.target.matches('#id_direction, #id_coin_depart, [name$="-DELETE"]')
            ) {
                recalculerDifferee();
            }
        });
        // Ajout/suppression d'une ligne (boutons "Ajouter"/"Enlever" du formset) : évènements
        // maison déclenchés par admin/js/inlines.js, repris tels quels par Unfold.
        document.addEventListener("formset:added", recalculerDifferee);
        document.addEventListener("formset:removed", recalculerDifferee);

        // Sélection du tiers matière et des pièces : widgets select2, dont le "change" passe par
        // jQuery et n'est pas toujours reçu par un addEventListener natif — même contrainte que
        // pour le profil d'import sur la fiche PieceDecoupe (piecedecoupe_admin.js).
        if (typeof django !== "undefined" && django.jQuery) {
            django.jQuery(document).on("change", "#id_article_matiere, [name$='-piece']", recalculerDifferee);
        }
    });
})();
