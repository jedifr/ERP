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
            groupe.querySelectorAll("select[data-i], input[data-i]").forEach((el) => { c[el.dataset.i] = el.value; });
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
            const bouton = e.target.closest(".dp-retenir");
            if (!bouton) return;
            const groupe = bouton.closest(".dp-groupe");
            const c = lireChoix()[groupe.dataset.cle];
            try {
                const reponse = await fetch(zoneImb.dataset.urlRetenir, {
                    method: "POST", credentials: "same-origin", headers: { "X-CSRFToken": csrf(), "Content-Type": "application/json" },
                    body: JSON.stringify({ cle: groupe.dataset.cle, tole: c.tole, format: bouton.dataset.format, marge: c.marge, chute: c.chute }),
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
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", demarrer);
else demarrer();
