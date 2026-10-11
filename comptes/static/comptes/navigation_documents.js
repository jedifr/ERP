// Navigation entre documents de même type : à côté du fil d'Ariane d'une fiche, un menu déroulant avec champ de recherche
// (numéro, tiers, statut…) et des flèches vers le précédent / le suivant. Données : /admin/navigation/<app>/<modèle>/.
(function () {
    "use strict";

    const m = window.location.pathname.match(/^\/admin\/([^/]+)\/([^/]+)\/(.+)\/change\/$/);
    if (!m) return;
    const [, app, modele, pkEncode] = m;
    let actuel = pkEncode;
    try { actuel = decodeURIComponent(pkEncode); } catch (e) { /* identifiant déjà brut */ }

    function demarrer() {
        const titre = document.querySelector("h1");
        if (!titre || document.getElementById("dpn")) return;
        const formulaire = document.querySelector("#content-main form, form[id$='_form']");
        let modifie = false;
        if (formulaire) {
            ["input", "change"].forEach((e) => formulaire.addEventListener(e, (ev) => { if (!ev.target.closest(".dpn")) modifie = true; }));
            formulaire.addEventListener("submit", () => { modifie = false; });
        }
        const url = "/admin/navigation/" + app + "/" + modele + "/";

        const racine = document.createElement("span");
        racine.id = "dpn";
        racine.className = "dpn";
        racine.innerHTML =
            '<a class="dpn-fleche" data-sens="precedent" aria-label="Document précédent" title="Précédent" hidden><span class="material-symbols-outlined">chevron_left</span></a>' +
            '<button type="button" class="dpn-bouton" aria-haspopup="listbox" aria-expanded="false" title="Aller à un autre document"><span class="material-symbols-outlined">unfold_more</span></button>' +
            '<a class="dpn-fleche" data-sens="suivant" aria-label="Document suivant" title="Suivant" hidden><span class="material-symbols-outlined">chevron_right</span></a>' +
            '<div class="dpn-menu" hidden><div class="dpn-recherche"><input type="search" placeholder="Rechercher…" autocomplete="off" aria-label="Rechercher un document"><small class="dpn-compte"></small></div>' +
            '<ul class="dpn-liste" role="listbox"></ul></div>';
        titre.after(racine);
        const bouton = racine.querySelector(".dpn-bouton");
        const menu = racine.querySelector(".dpn-menu");
        const champ = racine.querySelector("input");
        const liste = racine.querySelector(".dpn-liste");
        const compte = racine.querySelector(".dpn-compte");
        let minuteur = null, survol = -1;

        function aller(lien) {
            if (modifie && !window.confirm("Ce document a des modifications non enregistrées. Quitter sans enregistrer ?")) return;
            window.location.href = lien;
        }
        racine.querySelectorAll(".dpn-fleche").forEach((a) => a.addEventListener("click", (e) => { e.preventDefault(); if (a.href) aller(a.href); }));

        function dessiner(donnees) {
            liste.textContent = "";
            survol = -1;
            donnees.items.forEach((it, i) => {
                const li = document.createElement("li");
                li.setAttribute("role", "option");
                li.dataset.url = it.url;
                if (it.courant) li.classList.add("dpn-courant");
                const a = document.createElement("span");
                a.className = "dpn-label";
                a.textContent = it.label;
                const b = document.createElement("small");
                b.textContent = it.sous || "";
                li.append(a, b);
                if (it.statut) {
                    const s = document.createElement("span");
                    s.className = "dpn-pastille dpn-" + it.statut.ton;
                    s.textContent = it.statut.libelle;
                    li.append(s);
                }
                li.addEventListener("click", () => aller(it.url));
                liste.appendChild(li);
            });
            if (!donnees.items.length) {
                const vide = document.createElement("li");
                vide.className = "dpn-vide";
                vide.textContent = "Aucun résultat";
                liste.appendChild(vide);
            }
            const q = champ.value.trim();
            compte.textContent = q ? donnees.total + " résultat" + (donnees.total > 1 ? "s" : "") + " sur " + donnees.ensemble + " documents"
                : (donnees.total > donnees.items.length ? "Les " + donnees.items.length + " premiers sur " + donnees.total + " — tapez pour chercher" : donnees.total + " document" + (donnees.total > 1 ? "s" : ""));
        }

        async function charger() {
            try {
                const r = await fetch(url + "?actuel=" + encodeURIComponent(actuel) + "&q=" + encodeURIComponent(champ.value.trim()), { credentials: "same-origin" });
                if (!r.ok) throw new Error(r.status);
                const d = await r.json();
                dessiner(d);
                return d;
            } catch (e) {
                compte.textContent = "Liste indisponible";
                return null;
            }
        }

        // Flèches précédent / suivant (chargées une fois, sans recherche)
        fetch(url + "?actuel=" + encodeURIComponent(actuel), { credentials: "same-origin" })
            .then((r) => (r.ok ? r.json() : null))
            .then((d) => {
                if (!d) return;
                ["precedent", "suivant"].forEach((sens) => {
                    const a = racine.querySelector('[data-sens="' + sens + '"]');
                    if (d[sens]) { a.href = d[sens]; a.hidden = false; }
                });
            })
            .catch(() => { /* navigation facultative */ });

        function ouvrir() {
            menu.hidden = false;
            bouton.setAttribute("aria-expanded", "true");
            champ.value = "";
            charger().then(() => { champ.focus(); const c = liste.querySelector(".dpn-courant"); if (c) c.scrollIntoView({ block: "nearest" }); });
        }
        function fermer() {
            menu.hidden = true;
            bouton.setAttribute("aria-expanded", "false");
        }
        bouton.addEventListener("click", () => (menu.hidden ? ouvrir() : fermer()));
        document.addEventListener("click", (e) => { if (!racine.contains(e.target)) fermer(); });
        champ.addEventListener("input", () => { clearTimeout(minuteur); minuteur = setTimeout(charger, 200); });
        champ.addEventListener("keydown", (e) => {
            const lignes = [...liste.querySelectorAll("li[data-url]")];
            if (e.key === "Escape") { fermer(); bouton.focus(); return; }
            if (e.key === "ArrowDown" || e.key === "ArrowUp") {
                e.preventDefault();
                survol = Math.max(0, Math.min(lignes.length - 1, survol + (e.key === "ArrowDown" ? 1 : -1)));
                lignes.forEach((l, i) => l.classList.toggle("dpn-survol", i === survol));
                if (lignes[survol]) lignes[survol].scrollIntoView({ block: "nearest" });
            }
            if (e.key === "Enter") {
                e.preventDefault();
                const cible = lignes[survol >= 0 ? survol : 0];
                if (cible) aller(cible.dataset.url);
            }
        });
    }

    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", demarrer);
    else demarrer();
})();
