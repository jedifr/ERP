/* Éditeur d'opérations de fabrication (gamme de l'article), partagé : tout élément .gamme-editeur[data-url] est rempli par ce
 * script (fiche devis aujourd'hui, constructeur de devis ensuite). Dialogue JSON avec technique/gamme_editeur.py. */
(function () {
    "use strict";

    function csrf() {
        const el = document.querySelector("[name=csrfmiddlewaretoken]");
        return el ? el.value : "";
    }
    function el(tag, attrs, ...enfants) {
        const e = document.createElement(tag);
        Object.entries(attrs || {}).forEach(([k, v]) => { if (k === "class") e.className = v; else if (v !== null && v !== false) e.setAttribute(k, v === true ? "" : v); });
        enfants.flat().forEach((c) => e.append(c));
        return e;
    }
    const nombre = (x) => (x === null || x === undefined ? "" : String(x).replace(".", ","));
    const euros = (x) => (x === null || x === undefined ? "—" : x.toFixed(2).replace(".", ",") + " €");

    function init(racine) {
        if (racine.dataset.pret) return;
        racine.dataset.pret = "1";
        const editable = racine.dataset.editable === "1";
        let autres = [];
        try { autres = JSON.parse(racine.dataset.autres || "[]"); } catch (e) { /* aucune autre pièce */ }
        let etat = null, modifie = false, lignes = [];

        async function appeler(corps) {
            const url = racine.dataset.url + (corps ? "" : "?quantite=" + (racine.dataset.quantite || 1));
            const reponse = await fetch(url, corps ? {
                method: "POST", credentials: "same-origin", headers: { "X-CSRFToken": csrf(), "Content-Type": "application/json" },
                body: JSON.stringify({ ...corps, quantite: racine.dataset.quantite || 1 }),
            } : { credentials: "same-origin" });
            let json = {};
            try { json = await reponse.json(); } catch (e) { /* non JSON */ }
            if (!reponse.ok) throw new Error(json.detail || "Erreur " + reponse.status);
            return json;
        }

        function charger(donnees) {
            etat = donnees;
            lignes = donnees.etapes.map((e) => ({ ...e }));
            modifie = false;
            dessiner();
        }

        async function action(corps) {
            erreur("");
            try { charger(await appeler(corps)); } catch (e) { erreur(e.message); }
        }

        let zoneErreur = null;
        function erreur(texte) {
            if (!zoneErreur) return;
            zoneErreur.textContent = texte;
            zoneErreur.hidden = !texte;
        }

        function ligneTable(l, i) {
            const decoupe = l.origine === "decoupe";
            const horaire = l.mode === "horaire";
            const poste = el("select", { disabled: decoupe || !editable }, etat.postes.map((p) => el("option", { value: p.id, selected: p.id === l.poste }, p.nom)));
            poste.addEventListener("change", () => {
                l.poste = Number(poste.value);
                l.mode = (etat.postes.find((p) => p.id === l.poste) || {}).mode || "horaire";
                modifie = true; dessiner();
            });
            const champ = (cle, valeur, desactive) => {
                const input = el("input", { type: "text", inputmode: "decimal", value: nombre(valeur), disabled: desactive || !editable, "data-cle": cle });
                input.addEventListener("input", () => { l[cle] = input.value; modifie = true; marquer(); });
                return input;
            };
            const cellules = horaire
                ? [el("td", {}, champ("temps_fixe", l.temps_fixe)), el("td", {}, decoupe ? el("span", { title: "Calculé depuis la pièce" }, nombre(l.temps_variable)) : champ("temps_variable", l.temps_variable))]
                : [el("td", { colspan: 2 }, el("span", { class: "ge-aide" }, "Forfait par pièce "), champ("cout_forfaitaire", l.cout_forfaitaire))];
            const boutons = el("td", { class: "ge-actions" });
            if (editable) {
                const monter = el("button", { type: "button", title: "Monter", disabled: i === 0 }, "↑");
                const descendre = el("button", { type: "button", title: "Descendre", disabled: i === lignes.length - 1 }, "↓");
                monter.addEventListener("click", () => { lignes.splice(i - 1, 0, lignes.splice(i, 1)[0]); modifie = true; dessiner(); });
                descendre.addEventListener("click", () => { lignes.splice(i + 1, 0, lignes.splice(i, 1)[0]); modifie = true; dessiner(); });
                boutons.append(monter, descendre);
                if (!decoupe) {
                    const retirer = el("button", { type: "button", class: "ge-retirer", title: "Retirer l'opération" }, "✕");
                    retirer.addEventListener("click", () => { lignes.splice(i, 1); modifie = true; dessiner(); });
                    boutons.append(retirer);
                }
            }
            return el("tr", { class: decoupe ? "ge-decoupe" : "" },
                el("td", {}, String(i + 1)), el("td", {}, poste), ...cellules,
                el("td", {}, l.probleme ? el("span", { class: "ge-ko", title: l.probleme }, "tarif manquant") : euros(l.cout_unitaire)),
                el("td", {}, el("span", { class: "ge-pastille " + (decoupe ? "ge-auto" : "ge-main") }, decoupe ? "calculée" : "à la main")),
                boutons);
        }

        let etiquetteModif = null;
        function marquer() { if (etiquetteModif) etiquetteModif.hidden = !modifie; }

        function dessiner() {
            racine.replaceChildren();
            const n = lignes.length;
            const resume = `${n} étape${n > 1 ? "s" : ""} · ${nombre(etat.total_minutes_par_piece)} min/pièce · ${euros(etat.total_cout_par_piece)}/pièce`;
            const details = el("details", { class: "ge", open: modifie || n > 1 }, el("summary", {}, el("b", {}, "Opérations de fabrication"), el("span", { class: "ge-resume" }, " · " + resume)));
            if (n) {
                details.append(el("table", { class: "ge-table" },
                    el("thead", {}, el("tr", {}, ["#", "Opération / poste", "Réglage (min)", "Par pièce (min)", "Coût / pièce", "Origine", ""].map((t) => el("th", {}, t)))),
                    el("tbody", {}, lignes.map(ligneTable))));
            } else {
                details.append(el("p", { class: "ge-aide" }, "Aucune opération pour l'instant (la découpe s'ajoute dès que le temps de coupe est calculable)."));
            }
            zoneErreur = el("div", { class: "ge-erreur", hidden: true });
            details.append(zoneErreur);
            if (editable) {
                const barre = el("div", { class: "ge-barre" });
                const ajouter = el("button", { type: "button", class: "ge-ajouter" }, "＋ Ajouter une opération");
                ajouter.addEventListener("click", () => {
                    const premier = etat.postes[0];
                    if (!premier) { erreur("Créez d'abord un poste de travail (Paramétrage > Atelier)."); return; }
                    lignes.push({ id: null, poste: premier.id, mode: premier.mode, origine: "manuelle", temps_fixe: 0, temps_variable: 0, cout_forfaitaire: null });
                    modifie = true; dessiner();
                });
                barre.append(ajouter);
                if (etat.types.length) {
                    const choix = el("select", {}, el("option", { value: "" }, "Gamme type…"), etat.types.map((t) => el("option", { value: t.id }, `${t.nom} (${t.etapes} étape${t.etapes > 1 ? "s" : ""})`)));
                    choix.addEventListener("change", () => {
                        if (!choix.value) return;
                        if (modifie && !window.confirm("Les modifications non enregistrées seront perdues. Continuer ?")) { choix.value = ""; return; }
                        action({ action: "type", type: choix.value });
                    });
                    barre.append(choix);
                }
                if (autres.length) {
                    const choix = el("select", {}, el("option", { value: "" }, "Reprendre la gamme d'une autre pièce…"), autres.map((a) => el("option", { value: a.ref }, a.nom)));
                    choix.addEventListener("change", () => {
                        if (!choix.value) return;
                        if (modifie && !window.confirm("Les modifications non enregistrées seront perdues. Continuer ?")) { choix.value = ""; return; }
                        action({ action: "reprendre", source: choix.value });
                    });
                    barre.append(choix);
                }
                const enregistrer = el("button", { type: "button", class: "ge-enregistrer" }, "Enregistrer les opérations");
                enregistrer.addEventListener("click", () => action({ action: "enregistrer", etapes: lignes.map((l) => ({ id: l.id, poste: l.poste, temps_fixe: l.temps_fixe, temps_variable: l.temps_variable, cout_forfaitaire: l.cout_forfaitaire })) }));
                etiquetteModif = el("span", { class: "ge-modifie", hidden: !modifie }, "Modifications non enregistrées — ");
                barre.append(el("span", { class: "ge-droite" }, etiquetteModif, enregistrer));
                details.append(barre);
            }
            details.append(el("p", { class: "ge-note" }, "Ces opérations font partie de la gamme de l'article " + (racine.dataset.libelle || "") + " : elles seront reprises dans les prochains devis de cette pièce. Les devis déjà établis gardent leurs anciens temps (historique par date)."));
            racine.append(details);
        }

        racine.addEventListener("keydown", (e) => { if (e.key === "Enter" && e.target.tagName === "INPUT") { e.preventDefault(); e.target.blur(); } });
        racine.recharger = () => { if (!modifie) action(null); };
        appeler(null).then(charger).catch((e) => racine.replaceChildren(el("p", { class: "ge-aide" }, "Opérations indisponibles : " + e.message)));
    }

    function tout() { document.querySelectorAll(".gamme-editeur[data-url]").forEach(init); }
    function demarrer() {
        tout();
        const cartes = document.getElementById("dp-cartes");
        if (cartes && window.MutationObserver) new MutationObserver(tout).observe(cartes, { childList: true, subtree: true });
        // Le temps de coupe (donc l'étape « découpe ») change quand on modifie la pièce : on recharge les opérations non modifiées.
        document.addEventListener("dp-verdict-change", (e) => { const g = e.target.closest(".dp-carte")?.querySelector(".gamme-editeur"); if (g && g.recharger) g.recharger(); });
    }
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", demarrer); else demarrer();
})();
