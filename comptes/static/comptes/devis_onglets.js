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

    var onglets = [{ cle: "general", titre: "Général", icone: "assignment", elements: [saisie, recap] }];
    if (pieces) onglets.push({ cle: "pieces", titre: "Pièces et matière", icone: "content_cut", elements: [pieces], compte: function () {
        return pieces.querySelectorAll("#dp-cartes > *, #dp-profils > *").length;
    } });
    if (lignes) onglets.push({ cle: "lignes", titre: "Lignes du devis", icone: "list_alt", elements: [lignes], compte: function () {
        var n = 0;
        lignes.querySelectorAll("input[name$='-id']").forEach(function (i) { if (i.value && !/__prefix__/.test(i.name)) n++; });
        return n;
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
        b.addEventListener("click", function () { activer(o.cle, true); });
        nav.appendChild(b);
        boutons[o.cle] = b;
    });
    form.insertBefore(nav, conteneur);

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
    if (!boutons[voulu]) voulu = "general";
    activer(avecErreur() || voulu, false);
    majNombres();
    // Le panneau « Pièces » se remplit par AJAX : on recompte quand il change.
    if (pieces && window.MutationObserver) new MutationObserver(majNombres).observe(pieces, { childList: true, subtree: true });
}
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initOnglets);
else initOnglets();
