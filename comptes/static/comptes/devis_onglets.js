/* Fiche devis en onglets : Général (saisie + récapitulatif) · Pièces et matière · Lignes du devis.
   Le contenu reste dans le même formulaire (un seul « Enregistrer ») ; seul l'affichage change.
   L'onglet actif est mémorisé dans l'adresse (#onglet=…) et, si un champ est en erreur, c'est cet onglet qui s'ouvre. */
function initOnglets() {
    "use strict";
    var form = document.getElementById("devis_form");
    if (!form) return;
    var conteneur = form.querySelector(":scope > div:has(> fieldset.fiche-saisie)");
    if (!conteneur) return;

    var saisie = conteneur.querySelector(":scope > fieldset.fiche-saisie");
    var recap = conteneur.querySelector(":scope > fieldset.fiche-recap");
    var lignes = conteneur.querySelector(":scope > #lignes-group");
    var pieces = conteneur.querySelector(":scope > #dp-panneau");
    var chrono = conteneur.querySelector(":scope > #chronologie-affaire");
    var constructeur = conteneur.querySelector(":scope > #lignes-constructeur");
    var detail = conteneur.querySelector(":scope > #lignes-detail");
    // Devis pas encore enregistré : « Pièces et matière » et « Lignes du devis » enregistrent le devis puis s'ouvrent.
    var nouveau = !!document.getElementById("devis-nouveau");

    var onglets = [{ cle: "general", titre: "Général", icone: "assignment", elements: [saisie, recap] }];
    if (nouveau) {
        onglets.push({ cle: "pieces", titre: "Pièces et matière", icone: "content_cut", elements: [] });
        onglets.push({ cle: "lignes", titre: "Lignes du devis", icone: "list_alt", elements: [lignes] });
    }
    if (pieces && !nouveau) onglets.push({ cle: "pieces", titre: "Pièces et matière", icone: "content_cut", elements: [pieces], compte: function () {
        return pieces.querySelectorAll("#dp-cartes > *, #dp-profils > *").length;
    } });
    if (lignes && !nouveau) onglets.push({ cle: "lignes", titre: "Lignes du devis", icone: "list_alt", elements: [lignes, detail, constructeur], compte: function () {
        var n = 0;
        lignes.querySelectorAll("input[name$='-id']").forEach(function (i) { if (i.value && !/__prefix__/.test(i.name)) n++; });
        return n;
    } });
    if (chrono) onglets.push({ cle: "chrono", titre: "Chronologie", icone: "timeline", elements: [chrono], compte: function () {
        return chrono.querySelectorAll(".chrono-evt:not(.chrono-aujourdhui):not(.chrono-futur)").length;
    } });
    if (onglets.length < 2) return;

    // Tout élément non classé (messages, blocs ajoutés par ailleurs) reste visible dans tous les onglets.
    onglets.forEach(function (o) {
        o.elements.forEach(function (e) { if (e) e.setAttribute("data-onglet", o.cle); });
    });

    var nav = document.createElement("nav");
    nav.className = "devis-onglets";
    nav.setAttribute("role", "tablist");
    var boutons = {};
    onglets.forEach(function (o) {
        var b = document.createElement("button");
        b.type = "button";
        b.setAttribute("role", "tab");
        b.className = "devis-onglet";
        b.innerHTML = '<span class="material-symbols-outlined">' + o.icone + "</span><span>" + o.titre + '</span><span class="devis-onglet-nombre"></span>';
        b.addEventListener("click", function () {
            if (nouveau && o.cle !== "general") { enregistrerPuisOuvrir(o.cle); return; }
            activer(o.cle, true);
        });
        nav.appendChild(b);
        boutons[o.cle] = b;
    });
    form.insertBefore(nav, conteneur);

    function enregistrerPuisOuvrir(cle) {
        // Le devis doit exister pour recevoir des pièces ou des lignes : on l'enregistre (en restant sur la fiche) puis on ouvre l'onglet.
        if (!form.reportValidity()) { activer("general", false); return; }
        try { window.sessionStorage.setItem("devis-onglet-apres-rechargement", cle); } catch (e) { /* ignoré */ }
        var continuer = document.createElement("input");
        continuer.type = "hidden"; continuer.name = "_continue"; continuer.value = "1";
        form.appendChild(continuer);
        if (form.requestSubmit) form.requestSubmit(); else form.submit();
    }

    function majNombres() {
        onglets.forEach(function (o) {
            if (!o.compte) return;
            var n = o.compte();
            var span = boutons[o.cle].querySelector(".devis-onglet-nombre");
            span.textContent = n ? n : "";
            span.style.display = n ? "" : "none";
        });
    }

    function activer(cle, memoriser) {
        conteneur.setAttribute("data-onglet-actif", cle);
        onglets.forEach(function (o) {
            var actif = o.cle === cle;
            boutons[o.cle].classList.toggle("actif", actif);
            boutons[o.cle].setAttribute("aria-selected", actif ? "true" : "false");
        });
        if (memoriser) {
            try { history.replaceState(null, "", "#onglet=" + cle); } catch (e) { /* sans importance */ }
        }
        // Les panneaux dessinés pendant qu'ils étaient masqués (SVG, tailles) se recalent à l'affichage.
        window.dispatchEvent(new Event("resize"));
    }

    function avecErreur() {
        for (var i = 0; i < onglets.length; i++) {
            var els = onglets[i].elements;
            for (var j = 0; j < els.length; j++) {
                if (els[j] && els[j].querySelector(".errorlist, .errors, [aria-invalid='true'], .border-red-500")) return onglets[i].cle;
            }
        }
        return null;
    }

    var voulu = (location.hash.match(/onglet=(\w+)/) || [])[1];
    try {
        var memorise = window.sessionStorage.getItem("devis-onglet-apres-rechargement");
        if (memorise) { window.sessionStorage.removeItem("devis-onglet-apres-rechargement"); if (!nouveau) voulu = memorise; }
    } catch (e) { /* ignoré */ }
    if (!boutons[voulu]) voulu = "general";
    activer(avecErreur() || voulu, false);
    majNombres();
    // Le panneau « Pièces » se remplit par AJAX : on recompte quand il change.
    if (pieces && window.MutationObserver) new MutationObserver(majNombres).observe(pieces, { childList: true, subtree: true });
}
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initOnglets);
else initOnglets();
