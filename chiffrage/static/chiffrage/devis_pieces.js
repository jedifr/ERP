/* Panneau « Pièces à découper » de la fiche devis : import de DXF/DWG par glisser-déposer, réglages par pièce enregistrés
 * à la volée, verdict immédiat. Les cartes sont rendues par le serveur (_carte_piece.html). Aucun champ du panneau n'a de
 * `name` : rien n'est envoyé avec le formulaire du devis. */
function demarrer() {
    "use strict";
    const panneau = document.getElementById("dp-panneau");
    if (!panneau) return;
    const cartes = document.getElementById("dp-cartes");
    const messages = document.getElementById("dp-messages");
    const zone = document.getElementById("dp-drop");

    function csrf() {
        const el = document.querySelector("[name=csrfmiddlewaretoken]");
        return el ? el.value : "";
    }

    function message(texte, erreur) {
        const div = document.createElement("div");
        div.className = "dp-message" + (erreur ? " dp-message-ko" : "");
        div.textContent = texte;
        const fermer = document.createElement("button");
        fermer.type = "button";
        fermer.textContent = "×";
        fermer.addEventListener("click", () => div.remove());
        div.appendChild(fermer);
        messages.appendChild(div);
    }

    function depuisHtml(html) {
        const t = document.createElement("template");
        t.innerHTML = html.trim();
        return t.content.firstElementChild;
    }

    async function envoyer(url, donnees, json_) {
        const entetes = { "X-CSRFToken": csrf() };
        if (json_) entetes["Content-Type"] = "application/json";
        const reponse = await fetch(url, { method: "POST", body: donnees, headers: entetes, credentials: "same-origin" });
        let json = {};
        try { json = await reponse.json(); } catch (e) { /* réponse non JSON */ }
        if (!reponse.ok) throw new Error(json.detail || "Erreur " + reponse.status);
        return json;
    }

    // ---------------------------------------------------------------- imbrication
    const zoneImb = document.getElementById("dp-imbrication");
    const vues = document.getElementById("dp-vues");
    if (vues && zoneImb) {
        // Nombre de feuilles côte à côte (1 à 4), mémorisé dans le navigateur.
        const appliquer = (n) => {
            zoneImb.dataset.colonnes = n;
            vues.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", b.dataset.colonnes === String(n) ? "true" : "false"));
        };
        let initial = "1";
        try { initial = window.localStorage.getItem("dp-colonnes") || "1"; } catch (e) { /* stockage indisponible */ }
        appliquer(["1", "2", "3", "4"].includes(initial) ? initial : "1");
        vues.addEventListener("click", (e) => {
            const bouton = e.target.closest("button[data-colonnes]");
            if (!bouton) return;
            appliquer(bouton.dataset.colonnes);
            try { window.localStorage.setItem("dp-colonnes", bouton.dataset.colonnes); } catch (err) { /* ignoré */ }
        });
    }
    const formatsChoisis = {}; // groupe -> format cliqué dans le tableau de comparaison
    let minuteur = null;

    function lireChoix(brut) {
        // Bord de tôle automatique : envoyé vide pour que le serveur le recalcule (sauf pour retenir, qui enregistre la valeur affichée).
        const choix = {};
        zoneImb.querySelectorAll(".dp-groupe").forEach((groupe) => {
            const c = {};
            groupe.querySelectorAll("select[data-i], input[data-i]").forEach((el) => {
                if (el.type === "checkbox") c[el.dataset.i] = el.checked ? "1" : "0";
                else if (el.dataset.auto === "1" && !brut) c[el.dataset.i] = "";
                else c[el.dataset.i] = el.value;
            });
            if (formatsChoisis[groupe.dataset.cle]) c.format = formatsChoisis[groupe.dataset.cle];
            choix[groupe.dataset.cle] = c;
        });
        return choix;
    }

    async function rafraichirImbrication() {
        if (!zoneImb) return;
        zoneImb.classList.add("dp-calcul");
        try {
            const reponse = await fetch(zoneImb.dataset.urlImbrication, {
                method: "POST", credentials: "same-origin", headers: { "X-CSRFToken": csrf(), "Content-Type": "application/json" },
                body: JSON.stringify({ choix: lireChoix() }),
            });
            const json = await reponse.json();
            if (!reponse.ok) throw new Error(json.detail || "Erreur " + reponse.status);
            zoneImb.innerHTML = json.html;
        } catch (e) {
            message("Imbrication : " + e.message, true);
        } finally {
            zoneImb.classList.remove("dp-calcul");
        }
    }

    // Le formulaire du devis est modifié par l'utilisateur (hors champs du panneau) : l'ajout recharge la page, on le prévient.
    let formulaireModifie = false;
    document.addEventListener("input", (e) => {
        if (e.target.closest && !e.target.closest("#dp-panneau") && e.target.closest("form")) formulaireModifie = true;
    });

    async function ajouterAuDevis(bouton) {
        if (formulaireModifie && !window.confirm("Le devis a des modifications non enregistrées : l'ajout recharge la page et elles seront perdues. Continuer ?")) return;
        bouton.disabled = true;
        try {
            const reponse = await fetch(panneau.dataset.urlAjouter, {
                method: "POST", credentials: "same-origin", headers: { "X-CSRFToken": csrf() },
            });
            const json = await reponse.json();
            if (!reponse.ok) throw new Error(json.detail || "Erreur " + reponse.status);
            json.resultats.filter((r) => r.etat === "ignorée").forEach((r) => message(r.nom + " : non ajoutée — " + r.raison, true));
            const n = json.ajoutees + json.mises_a_jour;
            if (n) {
                message(json.ajoutees + " ligne(s) ajoutée(s), " + json.mises_a_jour + " mise(s) à jour. La page se recharge…", false);
                setTimeout(() => window.location.reload(), 1300);
            } else {
                bouton.disabled = false;
            }
        } catch (e) {
            bouton.disabled = false;
            message(e.message, true);
        }
    }

    function planifierImbrication() {
        if (!zoneImb) return;
        clearTimeout(minuteur);
        minuteur = setTimeout(rafraichirImbrication, 350);
    }

    if (zoneImb) {
        zoneImb.addEventListener("input", (e) => { if (e.target.dataset.auto === "1") e.target.dataset.auto = e.target.value.trim() === "" ? "1" : "0"; });
        zoneImb.addEventListener("change", (e) => {
            if (e.target.dataset.i === "marge" && e.target.value.trim() === "") e.target.dataset.auto = "1";
            if (e.target.dataset.i) planifierImbrication();
        });
        zoneImb.addEventListener("keydown", (e) => {
            if (e.key === "Enter" && e.target.matches("input[data-i]")) { e.preventDefault(); e.target.blur(); }
            if (e.key === "Enter" && e.target.matches("tr[data-i=format]")) e.target.click();
        });
        zoneImb.addEventListener("click", async (e) => {
            const ligne = e.target.closest("tr[data-i=format]");
            if (ligne) {
                formatsChoisis[ligne.closest(".dp-groupe").dataset.cle] = ligne.dataset.valeur;
                planifierImbrication();
                return;
            }
            const ajouter = e.target.closest(".dp-ajouter");
            if (ajouter) { ajouterAuDevis(ajouter); return; }
            const retenirProfil = e.target.closest(".dp-retenir-profil");
            if (retenirProfil) {
                const c = lireChoix()["profil|" + retenirProfil.dataset.section];
                try {
                    await envoyer(document.getElementById("dp-profils").dataset.urlRetenir, JSON.stringify(Object.assign({ section: retenirProfil.dataset.section }, c)), true);
                    planifierImbrication();
                } catch (err) {
                    message(err.message, true);
                }
                return;
            }
            const bouton = e.target.closest(".dp-retenir");
            if (!bouton) return;
            const groupe = bouton.closest(".dp-groupe");
            const c = lireChoix(true)[groupe.dataset.cle];
            try {
                const reponse = await fetch(zoneImb.dataset.urlRetenir, {
                    method: "POST", credentials: "same-origin", headers: { "X-CSRFToken": csrf(), "Content-Type": "application/json" },
                    body: JSON.stringify({ cle: groupe.dataset.cle, tole: c.tole, format: bouton.dataset.format, marge: c.marge, chute: c.chute, forme: c.forme, sens: c.sens, coin: c.coin }),
                });
                const json = await reponse.json();
                if (!reponse.ok) throw new Error(json.detail || "Erreur " + reponse.status);
                delete formatsChoisis[groupe.dataset.cle];
                planifierImbrication();
            } catch (err) {
                message(err.message, true);
            }
        });
        planifierImbrication();
    }

    // ---------------------------------------------------------------- import
    async function importer(fichiers) {
        for (const fichier of fichiers) {
            const attente = document.createElement("div");
            attente.className = "dp-carte dp-charge";
            attente.textContent = "Import de « " + fichier.name + " »…";
            cartes.appendChild(attente);
            const donnees = new FormData();
            donnees.append("fichier", fichier);
            donnees.append("procede", document.getElementById("dp-procede-defaut").value);
            donnees.append("profil_import", document.getElementById("dp-profil").value);
            try {
                const json = await envoyer(panneau.dataset.urlImporter, donnees);
                attente.replaceWith(depuisHtml(json.html));
                planifierImbrication();
            } catch (e) {
                attente.remove();
                message(fichier.name + " : " + e.message, true);
            }
        }
    }

    if (zone) {
        const entree = document.getElementById("dp-fichiers");
        zone.addEventListener("click", (e) => { if (!e.target.closest("select, label")) entree.click(); });
        zone.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); entree.click(); } });
        entree.addEventListener("change", () => { importer(Array.from(entree.files)); entree.value = ""; });
        ["dragenter", "dragover"].forEach((nom) => zone.addEventListener(nom, (e) => { e.preventDefault(); zone.classList.add("dp-drop-actif"); }));
        ["dragleave", "drop"].forEach((nom) => zone.addEventListener(nom, (e) => { e.preventDefault(); zone.classList.remove("dp-drop-actif"); }));
        zone.addEventListener("drop", (e) => importer(Array.from(e.dataTransfer.files)));
    }

    // ---------------------------------------------------------------- réglages
    function champs(carte) {
        const donnees = new FormData();
        carte.querySelectorAll("[data-champ]").forEach((el) => donnees.append(el.dataset.champ, el.value));
        return donnees;
    }

    async function enregistrer(carte) {
        const verdict = carte.querySelector(".dp-verdict");
        verdict.classList.add("dp-verdict-calcul");
        try {
            const json = await envoyer(carte.dataset.urlEnregistrer, champs(carte));
            const nouvelle = depuisHtml(json.html);
            // Seuls le verdict et la référence d'article sont remplacés : le focus reste dans les champs.
            verdict.replaceWith(nouvelle.querySelector(".dp-verdict"));
            const article = carte.querySelector(".dp-article"), nouveauLien = nouvelle.querySelector(".dp-article");
            if (article && nouveauLien) article.replaceWith(nouveauLien);
            planifierImbrication();
        } catch (e) {
            verdict.classList.remove("dp-verdict-calcul");
            message(e.message, true);
        }
    }

    cartes.addEventListener("change", (e) => {
        const carte = e.target.closest(".dp-carte[data-piece]");
        if (!carte || !e.target.dataset.champ) return;
        if (e.target.dataset.champ === "procede") {
            const gaz = carte.querySelector(".dp-gaz");
            if (gaz) gaz.hidden = e.target.value !== "laser";
        }
        enregistrer(carte);
    });

    cartes.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && e.target.matches("input[data-champ]")) {
            e.preventDefault(); // ne valide pas le devis
            e.target.blur();
        }
    });

    cartes.addEventListener("click", async (e) => {
        const bouton = e.target.closest(".dp-supprimer");
        if (!bouton) return;
        const carte = bouton.closest(".dp-carte[data-piece]");
        if (!window.confirm("Retirer cette pièce du devis ? Son article fabriqué est supprimé s'il n'est utilisé nulle part.")) return;
        try {
            const json = await envoyer(carte.dataset.urlSupprimer, new FormData());
            carte.remove();
            planifierImbrication();
            if (json.article_conserve) message("L'article " + json.article_conserve + " est conservé : il est utilisé ailleurs.", false);
        } catch (err) {
            message(err.message, true);
        }
    });

    // ---------------------------------------------------------------- débits de profilés (cartes)
    const zoneProfils = document.getElementById("dp-profils");
    if (zoneProfils) {
        zoneProfils.addEventListener("change", async (e) => {
            const carte = e.target.closest(".dp-carte[data-profil]");
            if (!carte || !e.target.dataset.champ) return;
            const donnees = new FormData();
            carte.querySelectorAll("[data-champ]").forEach((el) => donnees.append(el.dataset.champ, el.value));
            try {
                const json = await envoyer(carte.dataset.urlEnregistrer, donnees);
                carte.replaceWith(depuisHtml(json.html));
                planifierImbrication();
            } catch (err) {
                message(err.message, true);
                e.target.focus();
            }
        });
        zoneProfils.addEventListener("keydown", (e) => {
            if (e.key === "Enter" && e.target.matches("input[data-champ]")) { e.preventDefault(); e.target.blur(); }
        });
        zoneProfils.addEventListener("click", async (e) => {
            const bouton = e.target.closest(".dp-supprimer");
            if (!bouton) return;
            const carte = bouton.closest(".dp-carte[data-profil]");
            if (!window.confirm("Retirer ce débit du devis ? Son article fabriqué est supprimé s'il n'est utilisé nulle part.")) return;
            try {
                const json = await envoyer(carte.dataset.urlSupprimer, new FormData());
                carte.remove();
                planifierImbrication();
                if (json.article_conserve) message("L'article " + json.article_conserve + " est conservé : il est utilisé ailleurs.", false);
            } catch (err) {
                message(err.message, true);
            }
        });
    }

    // ---------------------------------------------------------------- bibliothèque de formes
    const formes = document.getElementById("dp-formes");
    const donneesCatalogue = document.getElementById("dp-catalogue-formes");
    if (formes && donneesCatalogue) {
        const catalogue = JSON.parse(donneesCatalogue.textContent);
        const norm = catalogue.normalisees;
        const selFamille = document.getElementById("dpf-famille");
        const zoneChamps = document.getElementById("dpf-champs");
        const boutonValider = document.getElementById("dpf-valider");
        const boutonAnnuler = document.getElementById("dpf-annuler");
        const zoneErreur = document.getElementById("dpf-erreur");
        let pieceModifiee = null; // id de la pièce dont on modifie les cotes (sinon : création)
        let minuteurForme = null;

        const groupes = {};
        catalogue.familles.forEach((f) => { (groupes[f.groupe] = groupes[f.groupe] || []).push(f); });
        Object.keys(groupes).forEach((nomGroupe) => {
            const og = document.createElement("optgroup");
            og.label = nomGroupe;
            groupes[nomGroupe].forEach((f) => { const o = document.createElement("option"); o.value = f.cle; o.textContent = f.libelle; og.appendChild(o); });
            selFamille.appendChild(og);
        });

        const profils = catalogue.profils || [];
        if (profils.length) {
            const og = document.createElement("optgroup");
            og.label = "Profilés (barres)";
            profils.forEach((f) => { const o = document.createElement("option"); o.value = "profil:" + f.cle; o.textContent = f.libelle; og.appendChild(o); });
            selFamille.appendChild(og);
        }
        const COUPES = [["90", "Droite (90°)"], ["45", "Biais 45°"], ["60", "Biais 60°"], ["30", "Biais 30°"], ["22.5", "Biais 22,5°"]];
        function enProfil() { return selFamille.value.indexOf("profil:") === 0; }
        function familleProfil() { return profils.find((f) => "profil:" + f.cle === selFamille.value); }
        function famille() { return catalogue.familles.find((f) => f.cle === selFamille.value); }

        function options(p, f) {
            if (f.cle === "bride_en1092" && p.cle === "dn") return norm.dn.map((v) => [String(v), "DN" + v]);
            if (f.cle === "bride_en1092" && p.cle === "pn") return norm.pn.map((v) => [v, v]);
            if (f.cle === "bride_en1092" && p.cle === "type_bride") return [["01", "01 — plate à souder (alésage)"], ["05", "05 — pleine"]];
            if (f.cle === "rondelle" && p.cle === "norme") return norm.normes_rondelles.map((v) => [v, v]);
            if (f.cle === "rondelle" && p.cle === "taille") return (norm.tailles_rondelles[valeur("norme")] || []).map((v) => [v, v]);
            return null;
        }

        function valeur(cle) {
            const el = zoneChamps.querySelector('[data-p="' + cle + '"]');
            return el ? el.value : "";
        }

        function construireChampsProfil(saisies) {
            const f = familleProfil();
            document.getElementById("dpf-description").textContent = "Débit de profilé tiré d'une barre : longueur hors tout et coupes d'extrémité. Les débits d'une même section s'imbriquent dans les barres.";
            const attention = document.getElementById("dpf-attention");
            attention.hidden = !f.non_verifie;
            attention.textContent = "Les masses et cotes des profilés livrés avec l'application ne sont pas vérifiées : contrôlez-les (Production > Sections de profilés) et rattachez-y l'article d'achat pour chiffrer la matière.";
            document.getElementById("dpf-procede").closest("label").hidden = true;
            zoneChamps.textContent = "";
            const ajouter = (libelle, champ, cle) => {
                const label = document.createElement("label");
                label.textContent = libelle;
                champ.dataset.p = cle;
                label.appendChild(champ);
                zoneChamps.appendChild(label);
            };
            const sel = document.createElement("select");
            f.sections.forEach((x) => { const o = document.createElement("option"); o.value = x.id; o.textContent = x.designation; sel.appendChild(o); });
            sel.value = saisies && saisies.section ? saisies.section : f.sections.length ? f.sections[0].id : "";
            ajouter("Section", sel, "section");
            const longueur = document.createElement("input");
            longueur.type = "text"; longueur.inputMode = "decimal"; longueur.value = saisies && saisies.longueur ? saisies.longueur : "1000";
            ajouter("Longueur hors tout (mm)", longueur, "longueur");
            [["Coupe de l'extrémité A", "coupe_a"], ["Coupe de l'extrémité B", "coupe_b"]].forEach(([libelle, cle]) => {
                const c = document.createElement("select");
                COUPES.forEach(([v, l]) => { const o = document.createElement("option"); o.value = v; o.textContent = l; c.appendChild(o); });
                c.value = saisies && saisies[cle] ? saisies[cle] : "90";
                ajouter(libelle, c, cle);
            });
        }

        function construireChamps(saisies) {
            document.getElementById("dpf-procede").closest("label").hidden = false;
            document.getElementById("dpf-attention").textContent = "Les cotes normalisées livrées avec l'application n'ont pas encore été vérifiées avec la norme : contrôlez-les (Production > Cotes normalisées) avant de lancer une production.";
            if (enProfil()) { construireChampsProfil(saisies); return; }
            const f = famille();
            document.getElementById("dpf-description").textContent = f.description;
            document.getElementById("dpf-attention").hidden = !(f.normalisee && norm.brides_non_verifiees);
            zoneChamps.textContent = "";
            f.parametres.forEach((p) => {
                const label = document.createElement("label");
                label.textContent = p.libelle;
                if (p.aide) label.title = p.aide;
                const choix = options(p, f);
                let champ;
                if (choix) {
                    champ = document.createElement("select");
                    choix.forEach(([v, l]) => { const o = document.createElement("option"); o.value = v; o.textContent = l; champ.appendChild(o); });
                } else {
                    champ = document.createElement("input");
                    champ.type = "text";
                    champ.inputMode = "decimal";
                }
                champ.dataset.p = p.cle;
                const initiale = saisies && saisies[p.cle] !== undefined ? saisies[p.cle] : p.defaut;
                champ.value = String(initiale);
                if (choix && ![...champ.options].some((o) => o.value === champ.value) && champ.options.length) champ.selectedIndex = 0;
                label.appendChild(champ);
                zoneChamps.appendChild(label);
            });
        }

        function saisies() {
            const cotes = {};
            zoneChamps.querySelectorAll("[data-p]").forEach((el) => { cotes[el.dataset.p] = el.value; });
            return cotes;
        }

        function montrerErreur(texte) {
            zoneErreur.hidden = !texte;
            zoneErreur.textContent = texte || "";
            boutonValider.disabled = !!texte;
        }

        async function apercu() {
            try {
                const profil = enProfil();
                const reponse = await fetch(profil ? formes.dataset.urlProfilApercu : formes.dataset.urlApercu, {
                    method: "POST", credentials: "same-origin", headers: { "X-CSRFToken": csrf(), "Content-Type": "application/json" },
                    body: JSON.stringify(profil ? saisies() : { famille: selFamille.value, cotes: saisies() }),
                });
                const json = await reponse.json();
                if (!reponse.ok) throw new Error(json.detail || "Erreur " + reponse.status);
                if (profil) {
                    document.getElementById("dpf-svg").innerHTML = json.svg + json.section_svg;
                    document.getElementById("dpf-resume").textContent = json.nom + " — " + json.masse_lineique + " kg/m, " + json.masse + " kg le débit" + (json.prix ? " · " + json.prix : " · prix non renseigné");
                } else {
                    document.getElementById("dpf-svg").innerHTML = json.svg;
                    document.getElementById("dpf-resume").textContent = json.nom + " — " + json.largeur + " × " + json.hauteur + " mm, " + json.trous + " perçage" + (json.trous > 1 ? "s" : "");
                }
                montrerErreur("");
            } catch (e) {
                document.getElementById("dpf-svg").textContent = "";
                document.getElementById("dpf-resume").textContent = "";
                montrerErreur(e.message);
            }
        }

        function planifierApercu() {
            clearTimeout(minuteurForme);
            minuteurForme = setTimeout(apercu, 250);
        }

        function sortirDuModeModification() {
            pieceModifiee = null;
            boutonValider.textContent = "Ajouter la pièce au devis";
            boutonAnnuler.hidden = true;
        }

        selFamille.addEventListener("change", () => { construireChamps(null); planifierApercu(); });
        zoneChamps.addEventListener("input", planifierApercu);
        zoneChamps.addEventListener("change", (e) => {
            if (e.target.dataset.p === "norme") { construireChamps(Object.assign(saisies(), { taille: "" })); }
            planifierApercu();
        });
        zoneChamps.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); boutonValider.click(); } });
        formes.addEventListener("toggle", () => { if (formes.open) planifierApercu(); });
        boutonAnnuler.addEventListener("click", () => { sortirDuModeModification(); construireChamps(null); planifierApercu(); });

        boutonValider.addEventListener("click", async () => {
            boutonValider.disabled = true;
            try {
                const json = await (async () => {
                    const profil = enProfil();
                    const reponse = await fetch(profil ? formes.dataset.urlProfilAjouter : formes.dataset.urlAjouter, {
                        method: "POST", credentials: "same-origin", headers: { "X-CSRFToken": csrf(), "Content-Type": "application/json" },
                        body: JSON.stringify(profil ? Object.assign(saisies(), { quantite: document.getElementById("dpf-quantite").value }) : {
                            famille: selFamille.value, cotes: saisies(), piece_id: pieceModifiee,
                            quantite: document.getElementById("dpf-quantite").value, procede: document.getElementById("dpf-procede").value,
                        }),
                    });
                    const retour = await reponse.json();
                    if (!reponse.ok) throw new Error(retour.detail || "Erreur " + reponse.status);
                    return retour;
                })();
                const nouvelle = depuisHtml(json.html);
                if (enProfil()) {
                    document.getElementById("dp-profils").appendChild(nouvelle);
                    message("Débit ajouté au devis.", false);
                    nouvelle.scrollIntoView({ behavior: "smooth", block: "nearest" });
                    planifierImbrication();
                    return;
                }
                const existante = pieceModifiee ? cartes.querySelector('.dp-carte[data-piece="' + pieceModifiee + '"]') : null;
                if (existante) existante.replaceWith(nouvelle); else cartes.appendChild(nouvelle);
                message(pieceModifiee ? "Forme mise à jour." : "Pièce ajoutée au devis : choisissez sa matière et son épaisseur.", false);
                nouvelle.scrollIntoView({ behavior: "smooth", block: "nearest" });
                sortirDuModeModification();
                planifierImbrication();
            } catch (e) {
                message(e.message, true);
            } finally {
                boutonValider.disabled = false;
            }
        });

        cartes.addEventListener("click", (e) => {
            const bouton = e.target.closest(".dp-modifier-forme");
            if (!bouton) return;
            const carte = bouton.closest(".dp-carte[data-piece]");
            const recette = JSON.parse(carte.dataset.forme);
            selFamille.value = recette.famille;
            construireChamps(recette.cotes);
            pieceModifiee = carte.dataset.piece;
            boutonValider.textContent = "Mettre à jour la forme";
            boutonAnnuler.hidden = false;
            formes.open = true;
            formes.scrollIntoView({ behavior: "smooth", block: "start" });
            planifierApercu();
        });

        construireChamps(null);
    }
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", demarrer);
else demarrer();
