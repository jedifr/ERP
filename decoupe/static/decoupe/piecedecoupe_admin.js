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
        a_gravure: "decoupe-a-gravure",
        longueur_gravure_mm: "decoupe-longueur-gravure",
    };

    // Repli si la toute première requête échoue avant même d'avoir reçu role_choices (ex.
    // erreur réseau) : mêmes libellés que RegleProfilImportDecoupe.Role côté serveur.
    const ROLES_PAR_DEFAUT = [
        ["decoupe", "Découpe"],
        ["gravure", "Gravure / marquage"],
        ["pliage", "Pliage"],
        ["ignore", "Ignoré"],
    ];

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

    function pieceIdActuel() {
        const match = window.location.pathname.match(/\/admin\/decoupe\/piecedecoupe\/(\d+)\/change\/?/);
        return match ? match[1] : null;
    }

    function decocherSymetrieSiGravure(aGravure) {
        const case_ = document.getElementById("id_symetrie_autorisee");
        if (case_ && aGravure && case_.checked) {
            case_.checked = false;
            case_.dispatchEvent(new Event("change", { bubbles: true }));
        }
    }

    // Construite via les API DOM (textContent/dataset), jamais via innerHTML avec le nom du
    // calque interpolé : ce nom vient du fichier DXF/DWG uploadé, donc non fiable — éviter
    // toute injection HTML si un fichier contient un nom de calque malveillant.
    function construireTableCalques(conteneur, calques, rolesEffectifs, roleChoices) {
        conteneur.innerHTML = "";
        if (!calques || !calques.length) {
            conteneur.textContent = "En attente d'import du fichier source.";
            return;
        }
        const table = document.createElement("table");
        table.style.borderCollapse = "collapse";

        const thead = document.createElement("thead");
        const trHead = document.createElement("tr");
        ["Calque", "Rôle"].forEach((texte, index) => {
            const th = document.createElement("th");
            th.textContent = texte;
            th.style.textAlign = "left";
            th.style.padding = index === 0 ? "4px 8px 4px 0" : "4px 0";
            trHead.appendChild(th);
        });
        thead.appendChild(trHead);
        table.appendChild(thead);

        const tbody = document.createElement("tbody");
        calques.forEach((calque) => {
            const tr = document.createElement("tr");

            const tdCalque = document.createElement("td");
            tdCalque.textContent = calque;
            tdCalque.style.padding = "4px 8px 4px 0";

            const tdRole = document.createElement("td");
            tdRole.style.padding = "4px 0";
            const select = document.createElement("select");
            select.className = "decoupe-calque-role";
            select.dataset.calque = calque;
            select.style.padding = "0.375rem";
            select.style.borderRadius = "0.375rem";
            select.style.border = "1px solid #e5e7eb";
            const roleActuel = (rolesEffectifs && rolesEffectifs[calque.toLowerCase()]) || "decoupe";
            (roleChoices || ROLES_PAR_DEFAUT).forEach(([valeur, libelle]) => {
                const option = document.createElement("option");
                option.value = valeur;
                option.textContent = libelle;
                if (valeur === roleActuel) option.selected = true;
                select.appendChild(option);
            });
            tdRole.appendChild(select);

            tr.appendChild(tdCalque);
            tr.appendChild(tdRole);
            tbody.appendChild(tr);
        });
        table.appendChild(tbody);
        conteneur.appendChild(table);
    }

    function calquesDepuisTable() {
        const mapping = {};
        document.querySelectorAll("#decoupe-calques-editables .decoupe-calque-role").forEach((select) => {
            mapping[select.dataset.calque] = select.value;
        });
        return mapping;
    }

    function synchroniserChampCache() {
        const champ = document.getElementById("id_regles_calques_manuelles");
        if (champ) champ.value = JSON.stringify(calquesDepuisTable());
    }

    function lancerAnalyse(donneesBase, options) {
        const reconstruireCalques = !options || options.reconstruireCalques !== false;
        const apercu = document.getElementById("decoupe-apercu");
        if (apercu && reconstruireCalques) apercu.textContent = "Analyse en cours...";

        if (reconstruireCalques) {
            const profilSelect = document.getElementById("id_profil_import");
            if (profilSelect && profilSelect.value) {
                donneesBase.append("profil_import", profilSelect.value);
            }
        } else {
            const champCache = document.getElementById("id_regles_calques_manuelles");
            if (champCache && champCache.value) {
                donneesBase.append("calques_roles", champCache.value);
            }
        }

        fetch("/admin/decoupe/piecedecoupe/analyser/", {
            method: "POST",
            credentials: "same-origin",
            headers: { "X-CSRFToken": csrfToken() },
            body: donneesBase,
        })
            .then((response) => response.json())
            .then((data) => {
                const conteneurCalques = document.getElementById("decoupe-calques-editables");
                if (reconstruireCalques && conteneurCalques) {
                    construireTableCalques(
                        conteneurCalques,
                        data.calques_detectes || [],
                        data.calques_roles || {},
                        data.role_choices
                    );
                    const champCache = document.getElementById("id_regles_calques_manuelles");
                    if (champCache) champCache.value = "{}";
                }

                majChamp("format_source", data.format_source_display || "-");
                majChamp("statut", data.statut_display || "-");
                majChamp("message_erreur", data.message_erreur || "-");
                majChamp(
                    "avertissements",
                    data.avertissements && data.avertissements.length ? data.avertissements.join(" ") : "-"
                );

                if (!data.ok) {
                    if (apercu) apercu.textContent = "-";
                    ["surface_mm2", "perimetre_decoupe_mm", "largeur_mm", "hauteur_mm", "nb_contours_interieurs", "a_gravure", "longueur_gravure_mm"].forEach(
                        (champ) => majChamp(champ, "-")
                    );
                    return;
                }

                majChamp("surface_mm2", formatNombre(data.surface_mm2, 2));
                majChamp("perimetre_decoupe_mm", formatNombre(data.perimetre_decoupe_mm, 2));
                majChamp("largeur_mm", formatNombre(data.largeur_mm, 1));
                majChamp("hauteur_mm", formatNombre(data.hauteur_mm, 1));
                majChamp("nb_contours_interieurs", data.nb_contours_interieurs);
                majChamp("a_gravure", data.a_gravure ? "Oui" : "Non");
                majChamp("longueur_gravure_mm", formatNombre(data.longueur_gravure_mm, 2));
                if (reconstruireCalques) decocherSymetrieSiGravure(data.a_gravure);

                if (apercu) apercu.innerHTML = data.svg || "-";
            })
            .catch(() => {
                if (apercu) apercu.textContent = "Erreur réseau lors de l'analyse.";
            });
    }

    function analyserFichier(fichier, options) {
        const donnees = new FormData();
        donnees.append("fichier_source", fichier);
        lancerAnalyse(donnees, options);
    }

    function analyserPieceExistante(pieceId, options) {
        const donnees = new FormData();
        donnees.append("piece_id", pieceId);
        lancerAnalyse(donnees, options);
    }

    // Relance l'analyse depuis le fichier déjà choisi (frais) ou, à défaut, depuis le fichier
    // déjà enregistré de la pièce en cours de modification — pour que changer le profil ou un
    // rôle de calque marche aussi sans ré-uploader (voir analyser_fichier_view : `piece_id`).
    function relancerAnalyse(options) {
        const fichier = dernierFichierSelectionne();
        if (fichier) {
            analyserFichier(fichier, options);
            return;
        }
        const pieceId = pieceIdActuel();
        if (pieceId) analyserPieceExistante(pieceId, options);
    }

    document.addEventListener("DOMContentLoaded", function () {
        const input = document.getElementById("id_fichier_source");
        if (input) {
            input.addEventListener("change", function () {
                const fichier = dernierFichierSelectionne();
                if (fichier) analyserFichier(fichier);
            });
        }

        // Un rôle changé dans le tableau de calques : on synchronise le champ cache tout de
        // suite (pour que "Enregistrer" persiste le bon classement même sans re-déclencher
        // l'analyse en direct), puis on relance l'analyse pour rafraîchir stats/aperçu — sans
        // reconstruire le tableau, qui EST la source de vérité à ce stade.
        document.addEventListener("change", function (event) {
            if (event.target.matches && event.target.matches(".decoupe-calque-role")) {
                synchroniserChampCache();
                relancerAnalyse({ reconstruireCalques: false });
            }
        });

        // Changer de profil d'import doit ré-analyser le fichier déjà choisi (le classement
        // des calques en dépend) — nécessite jQuery pour capter le "change" émis par le
        // widget select2 de l'autocomplete, comme pour les champs client/article ailleurs
        // dans l'admin (un addEventListener natif ne le reçoit pas toujours).
        //
        // Piège : l'initialisation de select2 sur ce champ déclenche elle-même un "change"
        // au chargement de la page (sans aucune action de l'utilisateur). Sur la fiche de
        // modification d'une pièce déjà importée, réagir à cet événement fantôme relançait une
        // analyse par défaut (sans les rôles de calques choisis à la main) et écrasait le
        // tableau de calques correctement affiché — corrompant le classement enregistré au
        // prochain "Enregistrer". On ne réagit donc qu'à un changement réel de valeur.
        if (typeof django !== "undefined" && django.jQuery) {
            const $profilImport = django.jQuery("#id_profil_import");
            let derniereValeur = $profilImport.val();
            $profilImport.on("change", function () {
                const valeur = $profilImport.val();
                if (valeur === derniereValeur) return;
                derniereValeur = valeur;
                relancerAnalyse();
            });
        }
    });
})();
