/* Éditeur de nomenclature d'un article fabriqué : tout élément .nomenclature-editeur[data-url] est rempli par ce script (fiche devis,
 * lignes dépliées). Dialogue JSON avec technique/nomenclature_editeur.py. */
(function () {
    "use strict";
    const csrf = () => (document.querySelector("[name=csrfmiddlewaretoken]") || {}).value || "";
    function el(tag, attrs, ...enfants) {
        const e = document.createElement(tag);
        Object.entries(attrs || {}).forEach(([k, v]) => { if (k === "class") e.className = v; else if (v !== null && v !== false) e.setAttribute(k, v === true ? "" : v); });
        enfants.flat().forEach((c) => e.append(c));
        return e;
    }
    const nombre = (x) => (x === null || x === undefined ? "" : String(x).replace(".", ","));

    function init(racine) {
        if (racine.dataset.pret) return;
        racine.dataset.pret = "1";
        const editable = racine.dataset.editable === "1";
        let lignes = [], modifie = false, zoneErreur = null;
        const minuteurs = new WeakMap();

        async function appeler(corps) {
            const reponse = await fetch(racine.dataset.url, corps ? {
                method: "POST", credentials: "same-origin", headers: { "X-CSRFToken": csrf(), "Content-Type": "application/json" }, body: JSON.stringify(corps),
            } : { credentials: "same-origin" });
            let json = {};
            try { json = await reponse.json(); } catch (e) { /* non JSON */ }
            if (!reponse.ok) throw new Error(json.detail || "Erreur " + reponse.status);
            return json;
        }
        function charger(d) { lignes = d.lignes.map((l) => ({ ...l })); modifie = false; dessiner(); }
        function erreur(t) { if (zoneErreur) { zoneErreur.textContent = t; zoneErreur.hidden = !t; } }

        function suggestions(input, liste) {
            clearTimeout(minuteurs.get(input));
            minuteurs.set(input, setTimeout(async () => {
                if (!input.value) return;
                try {
                    const r = await fetch("/api/v1/articles/?search=" + encodeURIComponent(input.value), { credentials: "same-origin" });
                    const j = r.ok ? await r.json() : { results: [] };
                    liste.replaceChildren(...(j.results || []).slice(0, 15).map((a) => el("option", { value: a.reference }, a.libelle || "")));
                } catch (e) { /* ignoré */ }
            }, 250));
        }

        function dessiner() {
            racine.replaceChildren();
            const liste = el("datalist", { id: "ne-" + Math.random().toString(36).slice(2) });
            const table = el("table", { class: "ge-table" }, el("thead", {}, el("tr", {}, ["Composant", "Longueur (mm)", "Largeur (mm)", "Quantité", ""].map((t) => el("th", {}, t)))));
            const corps = el("tbody", {});
            lignes.forEach((l, i) => {
                const champ = (cle, valeur, large) => {
                    const input = el("input", { type: "text", inputmode: "decimal", value: nombre(valeur), disabled: !editable });
                    input.addEventListener("input", () => { l[cle] = input.value; modifie = true; });
                    return input;
                };
                const composant = el("input", { type: "text", value: l.composant || "", list: liste.id, disabled: !editable, placeholder: "Référence de l'article…" });
                composant.addEventListener("input", () => { l.composant = composant.value; modifie = true; suggestions(composant, liste); });
                const retirer = el("button", { type: "button", class: "ge-retirer", title: "Retirer" }, "✕");
                retirer.addEventListener("click", () => { lignes.splice(i, 1); modifie = true; dessiner(); });
                corps.append(el("tr", {}, el("td", {}, composant, l.libelle ? el("small", { class: "ge-aide" }, l.libelle) : ""), el("td", {}, champ("longueur_mm", l.longueur_mm)), el("td", {}, champ("largeur_mm", l.largeur_mm)), el("td", {}, champ("quantite", l.quantite)), el("td", { class: "ge-actions" }, editable ? retirer : "")));
            });
            table.append(corps);
            racine.append(el("div", { class: "ge-titre" }, el("b", {}, "Nomenclature"), el("span", { class: "ge-resume" }, ` · ${lignes.length} composant${lignes.length > 1 ? "s" : ""}`)), lignes.length ? table : el("p", { class: "ge-aide" }, "Aucun composant."), liste);
            zoneErreur = el("div", { class: "ge-erreur", hidden: true });
            racine.append(zoneErreur);
            if (editable) {
                const ajouter = el("button", { type: "button", class: "ge-ajouter" }, "＋ Composant");
                ajouter.addEventListener("click", () => { lignes.push({ id: null, composant: "", quantite: 1, longueur_mm: null, largeur_mm: null }); modifie = true; dessiner(); });
                const enregistrer = el("button", { type: "button", class: "ge-enregistrer" }, "Enregistrer la nomenclature");
                enregistrer.addEventListener("click", async () => {
                    erreur("");
                    try { charger(await appeler({ lignes: lignes.map((l) => ({ id: l.id, composant: l.composant, quantite: l.quantite, longueur_mm: l.longueur_mm, largeur_mm: l.largeur_mm })) })); window.dispatchEvent(new Event("nomenclature-modifiee")); }
                    catch (e) { erreur(e.message); }
                });
                racine.append(el("div", { class: "ge-barre" }, ajouter, el("span", { class: "ge-droite" }, enregistrer)));
            }
            racine.append(el("p", { class: "ge-note" }, "La nomenclature appartient à l'article " + (racine.dataset.libelle || "") + " : elle est partagée avec les autres devis qui l'utilisent. Recalculez le devis (« Enregistrer ») pour mettre les prix à jour."));
        }
        appeler(null).then(charger).catch((e) => racine.replaceChildren(el("p", { class: "ge-aide" }, "Nomenclature indisponible : " + e.message)));
    }
    function tout() { document.querySelectorAll(".nomenclature-editeur[data-url]").forEach(init); }
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", tout); else tout();
})();
