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

    async function envoyer(url, donnees) {
        const reponse = await fetch(url, { method: "POST", body: donnees, headers: { "X-CSRFToken": csrf() }, credentials: "same-origin" });
        let json = {};
        try { json = await reponse.json(); } catch (e) { /* réponse non JSON */ }
        if (!reponse.ok) throw new Error(json.detail || "Erreur " + reponse.status);
        return json;
    }

    // ---------------------------------------------------------------- imbrication
    const zoneImb = document.getElementById("dp-imbrication");
    const formatsChoisis = {}; // groupe -> format cliqué dans le tableau de comparaison
    let minuteur = null;

    function lireChoix() {
        const choix = {};
        zoneImb.querySelectorAll(".dp-groupe").forEach((groupe) => {
            const c = {};
            groupe.querySelectorAll("select[data-i], input[data-i]").forEach((el) => { c[el.dataset.i] = el.type === "checkbox" ? (el.checked ? "1" : "0") : el.value; });
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
        zoneImb.addEventListener("change", (e) => { if (e.target.dataset.i) planifierImbrication(); });
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
            const bouton = e.target.closest(".dp-retenir");
            if (!bouton) return;
            const groupe = bouton.closest(".dp-groupe");
            const c = lireChoix()[groupe.dataset.cle];
            try {
                const reponse = await fetch(zoneImb.dataset.urlRetenir, {
                    method: "POST", credentials: "same-origin", headers: { "X-CSRFToken": csrf(), "Content-Type": "application/json" },
                    body: JSON.stringify({ cle: groupe.dataset.cle, tole: c.tole, format: bouton.dataset.format, marge: c.marge, chute: c.chute, forme: c.forme }),
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

        function construireChamps(saisies) {
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
                const reponse = await fetch(formes.dataset.urlApercu, {
                    method: "POST", credentials: "same-origin", headers: { "X-CSRFToken": csrf(), "Content-Type": "application/json" },
                    body: JSON.stringify({ famille: selFamille.value, cotes: saisies() }),
                });
                const json = await reponse.json();
                if (!reponse.ok) throw new Error(json.detail || "Erreur " + reponse.status);
                document.getElementById("dpf-svg").innerHTML = json.svg;
                document.getElementById("dpf-resume").textContent = json.nom + " — " + json.largeur + " × " + json.hauteur + " mm, " + json.trous + " perçage" + (json.trous > 1 ? "s" : "");
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
                    const reponse = await fetch(formes.dataset.urlAjouter, {
                        method: "POST", credentials: "same-origin", headers: { "X-CSRFToken": csrf(), "Content-Type": "application/json" },
                        body: JSON.stringify({
                            famille: selFamille.value, cotes: saisies(), piece_id: pieceModifiee,
                            quantite: document.getElementById("dpf-quantite").value, procede: document.getElementById("dpf-procede").value,
                        }),
                    });
                    const retour = await reponse.json();
                    if (!reponse.ok) throw new Error(retour.detail || "Erreur " + reponse.status);
                    return retour;
                })();
                const nouvelle = depuisHtml(json.html);
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
