/* Schéma des cotes d'une section de profilé : le dessin suit la famille choisie (cornière : a, b, e ; UPN : h, b, tw, tf ; tubes : c, h, b, d, e). */
(function () {
    "use strict";
    var NS = "http://www.w3.org/2000/svg";

    function el(nom, attrs, parent) {
        var e = document.createElementNS(NS, nom);
        Object.keys(attrs || {}).forEach(function (k) { e.setAttribute(k, attrs[k]); });
        if (parent) parent.appendChild(e);
        return e;
    }
    function texte(svg, x, y, contenu, ancre) {
        var t = el("text", { x: x, y: y, "text-anchor": ancre || "middle", "font-size": 13, "font-weight": 600, fill: "currentColor", "font-family": "system-ui, sans-serif" }, svg);
        t.textContent = contenu;
    }
    // Cote : trait avec une flèche à chaque bout et son nom ; `traits` ajoute les traits de rappel (lignes d'attache) depuis la pièce.
    function cote(svg, x1, y1, x2, y2, nom, lx, ly, ancre) {
        el("line", { x1: x1, y1: y1, x2: x2, y2: y2, stroke: "currentColor", "stroke-width": 1, "marker-start": "url(#fleche)", "marker-end": "url(#fleche)" }, svg);
        texte(svg, lx, ly, nom, ancre);
    }
    function rappel(svg, x1, y1, x2, y2) {
        el("line", { x1: x1, y1: y1, x2: x2, y2: y2, stroke: "currentColor", "stroke-width": 0.6, "stroke-dasharray": "3 2", opacity: 0.6 }, svg);
    }
    function forme(svg, d) {
        el("path", { d: d, fill: "rgba(217,98,43,.18)", stroke: "currentColor", "stroke-width": 1.6, "fill-rule": "evenodd", "stroke-linejoin": "round" }, svg);
    }

    // Poutrelle en I (IPE, HEA, HEB) : âme au centre, deux semelles.
    function poutrelle(s) {
        forme(s, "M50 20 H150 V34 H106 V126 H150 V140 H50 V126 H94 V34 H50 Z");
        rappel(s, 50, 20, 34, 20); rappel(s, 50, 140, 34, 140); cote(s, 38, 20, 38, 140, "h", 24, 85);
        rappel(s, 50, 140, 50, 156); rappel(s, 150, 140, 150, 156); cote(s, 50, 152, 150, 152, "b", 100, 170);
        cote(s, 94, 80, 106, 80, "tw", 114, 76, "start");
        rappel(s, 150, 20, 166, 20); rappel(s, 150, 34, 166, 34); cote(s, 162, 20, 162, 34, "tf", 170, 31, "start");
    }

    var DESSINS = {
        corniere: { cotes: "a, b, e", dessin: function (s) {
            forme(s, "M40 20 H60 V120 H160 V140 H40 Z");
            rappel(s, 40, 20, 24, 20); rappel(s, 40, 140, 24, 140); cote(s, 28, 20, 28, 140, "a", 15, 85);
            rappel(s, 40, 140, 40, 156); rappel(s, 160, 140, 160, 156); cote(s, 40, 152, 160, 152, "b", 100, 170);
            rappel(s, 40, 20, 40, 8); rappel(s, 60, 20, 60, 8); cote(s, 40, 12, 60, 12, "e", 50, 6);
            rappel(s, 160, 120, 176, 120); rappel(s, 160, 140, 176, 140); cote(s, 172, 120, 172, 140, "e", 184, 134, "start");
        } },
        upn: { cotes: "h, b, tw, tf", dessin: function (s) {
            forme(s, "M50 20 H150 V34 H64 V126 H150 V140 H50 Z");
            rappel(s, 50, 20, 34, 20); rappel(s, 50, 140, 34, 140); cote(s, 38, 20, 38, 140, "h", 24, 85);
            rappel(s, 50, 140, 50, 156); rappel(s, 150, 140, 150, 156); cote(s, 50, 152, 150, 152, "b", 100, 170);
            cote(s, 50, 84, 64, 84, "tw", 72, 80, "start");
            rappel(s, 150, 20, 166, 20); rappel(s, 150, 34, 166, 34); cote(s, 162, 20, 162, 34, "tf", 170, 31, "start");
        } },
        corniere_inegale: { cotes: "a (grande aile), b (petite aile), e", dessin: function (s) {
            forme(s, "M40 14 H60 V122 H140 V140 H40 Z");
            rappel(s, 40, 14, 24, 14); rappel(s, 40, 140, 24, 140); cote(s, 28, 14, 28, 140, "a", 15, 80);
            rappel(s, 40, 140, 40, 156); rappel(s, 140, 140, 140, 156); cote(s, 40, 152, 140, 152, "b", 90, 170);
            rappel(s, 40, 14, 40, 4); rappel(s, 60, 14, 60, 4); cote(s, 40, 8, 60, 8, "e", 50, 3);
            rappel(s, 140, 122, 156, 122); rappel(s, 140, 140, 156, 140); cote(s, 152, 122, 152, 140, "e", 164, 136, "start");
        } },
        ipe: { cotes: "h, b, tw, tf", dessin: poutrelle },
        hea: { cotes: "h, b, tw, tf", dessin: poutrelle },
        heb: { cotes: "h, b, tw, tf", dessin: poutrelle },
        plat: { cotes: "l (largeur), e (épaisseur)", dessin: function (s) {
            forme(s, "M30 70 H170 V110 H30 Z");
            rappel(s, 30, 110, 30, 140); rappel(s, 170, 110, 170, 140); cote(s, 30, 136, 170, 136, "l", 100, 154);
            rappel(s, 170, 70, 186, 70); rappel(s, 170, 110, 186, 110); cote(s, 182, 70, 182, 110, "e", 194, 94, "start");
        } },
        rond_plein: { cotes: "d", dessin: function (s) {
            forme(s, "M40 85 a60 60 0 1 0 120 0 a60 60 0 1 0 -120 0 Z");
            rappel(s, 40, 85, 40, 156); rappel(s, 160, 85, 160, 156); cote(s, 40, 152, 160, 152, "d", 100, 170);
        } },
        carre_plein: { cotes: "c", dessin: function (s) {
            forme(s, "M45 20 H155 V130 H45 Z");
            rappel(s, 45, 130, 45, 146); rappel(s, 155, 130, 155, 146); cote(s, 45, 142, 155, 142, "c", 100, 160);
        } },
        tube_carre: { cotes: "c, e", dessin: function (s) {
            forme(s, "M40 20 H160 V140 H40 Z M54 34 H146 V126 H54 Z");
            rappel(s, 40, 140, 40, 156); rappel(s, 160, 140, 160, 156); cote(s, 40, 152, 160, 152, "c", 100, 170);
            rappel(s, 40, 20, 40, 8); rappel(s, 54, 20, 54, 8); cote(s, 40, 12, 54, 12, "e", 47, 6);
        } },
        tube_rectangulaire: { cotes: "h, b, e", dessin: function (s) {
            forme(s, "M30 40 H170 V130 H30 Z M43 53 H157 V117 H43 Z");
            rappel(s, 30, 40, 14, 40); rappel(s, 30, 130, 14, 130); cote(s, 18, 40, 18, 130, "h", 8, 88);
            rappel(s, 30, 130, 30, 146); rappel(s, 170, 130, 170, 146); cote(s, 30, 142, 170, 142, "b", 100, 160);
            rappel(s, 30, 40, 30, 28); rappel(s, 43, 40, 43, 28); cote(s, 30, 32, 43, 32, "e", 37, 24);
        } },
        tube_rond: { cotes: "d, e", dessin: function (s) {
            forme(s, "M38 85 a62 62 0 1 0 124 0 a62 62 0 1 0 -124 0 Z M51 85 a49 49 0 1 0 98 0 a49 49 0 1 0 -98 0 Z");
            rappel(s, 38, 85, 38, 156); rappel(s, 162, 85, 162, 156); cote(s, 38, 152, 162, 152, "d", 100, 170);
            cote(s, 149, 85, 162, 85, "e", 178, 89, "start");
        } },
    };

    function dessiner(zone, famille) {
        zone.replaceChildren();
        var d = DESSINS[famille];
        if (!d) return;
        var svg = el("svg", { viewBox: "0 0 200 180", width: 220, height: 198, role: "img", "aria-label": "Schéma des cotes : " + d.cotes }, zone);
        var defs = el("defs", {}, svg);
        var marqueur = el("marker", { id: "fleche", viewBox: "0 0 10 10", refX: 5, refY: 5, markerWidth: 6, markerHeight: 6, orient: "auto-start-reverse" }, defs);
        el("path", { d: "M0 0 L10 5 L0 10 z", fill: "currentColor" }, marqueur);
        d.dessin(svg);
        var legende = document.createElement("p");
        legende.className = "text-xs text-font-subtle-light dark:text-font-subtle-dark";
        legende.textContent = "Cotes à saisir : " + d.cotes + " (en mm).";
        zone.appendChild(legende);
    }

    document.addEventListener("DOMContentLoaded", function () {
        var famille = document.getElementById("id_famille");
        var cotes = document.getElementById("id_dimensions");
        if (!famille || !cotes) return;
        var ligne = cotes.closest(".field-row, .form-row") || cotes.parentElement;
        var zone = document.createElement("div");
        zone.className = "section-schema px-3 pb-3";
        zone.style.cssText = "display:flex;gap:16px;align-items:center;flex-wrap:wrap";
        ligne.insertAdjacentElement("afterend", zone);
        famille.addEventListener("change", function () { dessiner(zone, famille.value); });
        dessiner(zone, famille.value);
    });
})();
