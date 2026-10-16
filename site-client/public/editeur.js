// Éditeur de forme libre : une plaque (rectangle ou disque) et des éléments (trous, oblongs, rectangles, polygones) à
// retirer ou à ajouter, placés par leur centre en X / Y depuis le coin bas-gauche de la plaque. On les déplace à la
// souris ou au doigt ; les cotes X / Y de l'élément choisi s'affichent. L'ERP reconstruit et contrôle la pièce.
"use strict";

(function (racine) {
  const TYPES = {
    rond: { libelle: "Trou rond", champs: [["diametre", "Ø", 20]] },
    oblong: { libelle: "Oblong", champs: [["longueur", "Longueur", 60], ["largeur", "Largeur", 20], ["angle", "Angle (°)", 0]] },
    rectangle: { libelle: "Rectangle", champs: [["largeur", "Largeur", 60], ["hauteur", "Hauteur", 40], ["rayon", "Rayon", 0], ["angle", "Angle (°)", 0]] },
    polygone: { libelle: "Polygone", champs: [["nb_cotes", "Côtés", 6], ["diametre", "Ø", 40], ["angle", "Angle (°)", 0]] },
  };

  const tourner = (pts, angle, x, y) => {
    const a = (angle || 0) * Math.PI / 180, c = Math.cos(a), s = Math.sin(a);
    return pts.map(([px, py]) => [x + px * c - py * s, y + px * s + py * c]);
  };

  // Contour (y vers le haut) d'un élément, mêmes conventions que l'ERP (web/services/libre.py).
  function contourElement(e) {
    const { cercle, rectangle } = racine.Schemas;
    const n = (v, d = 0) => (Number.isFinite(Number(v)) ? Number(v) : d);
    let pts;
    if (e.type === "rond") pts = cercle(0, 0, Math.max(n(e.diametre), 0.5) / 2, 48);
    else if (e.type === "oblong") {
      let lo = Math.max(n(e.longueur), 0.5), la = Math.max(n(e.largeur), 0.5);
      if (lo < la) [lo, la] = [la, lo];
      const r = la / 2;
      pts = [];
      for (let k = 0; k <= 18; k++) pts.push([lo / 2 - r + r * Math.cos((-90 + 10 * k) * Math.PI / 180), r * Math.sin((-90 + 10 * k) * Math.PI / 180)]);
      for (let k = 0; k <= 18; k++) pts.push([-lo / 2 + r + r * Math.cos((90 + 10 * k) * Math.PI / 180), r * Math.sin((90 + 10 * k) * Math.PI / 180)]);
    } else if (e.type === "rectangle") {
      const w = Math.max(n(e.largeur), 0.5), h = Math.max(n(e.hauteur), 0.5);
      pts = rectangle(w, h, Math.min(n(e.rayon), Math.min(w, h) / 2)).map(([x, y]) => [x - w / 2, y - h / 2]);
    } else {
      const k = Math.min(Math.max(Math.round(n(e.nb_cotes, 6)), 3), 64), r = Math.max(n(e.diametre), 0.5) / 2;
      pts = Array.from({ length: k }, (_, i) => [r * Math.cos(Math.PI / 2 + 2 * Math.PI * i / k), r * Math.sin(Math.PI / 2 + 2 * Math.PI * i / k)]);
    }
    return tourner(pts, e.angle, n(e.x), n(e.y));
  }

  function contourBase(b) {
    const { cercle, rectangle } = racine.Schemas;
    if (b.forme === "disque") {
      const r = Math.max(Number(b.diametre) || 1, 1) / 2;
      return cercle(r, r, r, 96);
    }
    const w = Math.max(Number(b.largeur) || 1, 1), h = Math.max(Number(b.hauteur) || 1, 1);
    return rectangle(w, h, Math.min(Number(b.rayon_angle) || 0, Math.min(w, h) / 2));
  }

  const dimensionsBase = (b) => (b.forme === "disque" ? [Number(b.diametre) || 1, Number(b.diametre) || 1] : [Number(b.largeur) || 1, Number(b.hauteur) || 1]);

  // Dessin de la recette dans le svg ; `selection` : indice de l'élément choisi (ses cotes X / Y sont tracées).
  function dessiner(svg, recette, selection) {
    const [W, H] = dimensionsBase(recette.base);
    const d = racine.Schemas.Dessin(Math.max(W, H));
    d.chemin([contourBase(recette.base)], "piece");
    recette.elements.forEach((e, i) => {
      const classe = `element ${e.mode === "ajouter" ? "ajout" : "retrait"}${i === selection ? " choisi" : ""}`;
      d.chemin([contourElement(e)], classe, `data-i="${i}"`);
    });
    if (recette.base.forme === "disque") d.lineaire([0, H / 2], [W, H / 2], -H / 2 - d.s * 1.6, "Ø", W);
    else {
      d.lineaire([0, 0], [W, 0], -d.s * 1.6, "", W);
      d.lineaire([0, H], [0, 0], -d.s * 1.6, "", H);
    }
    const e = recette.elements[selection];
    if (e) {
      const x = Number(e.x) || 0, y = Number(e.y) || 0;
      d.ligne([x - d.s * 0.6, y], [x + d.s * 0.6, y], "axe");
      d.ligne([x, y - d.s * 0.6], [x, y + d.s * 0.6], "axe");
      d.lineaire([0, y], [x, y], 0.001, "X", x);
      d.lineaire([x, 0], [x, y], 0.001, "Y", y);
    }
    const rendu = d.svg("", "");
    svg.setAttribute("viewBox", rendu.viewBox);
    svg.innerHTML = rendu.contenu;
  }

  // Déplacement à la souris / au doigt. rappels : choisir(i), deplacer(i, x, y) pendant le geste, fin(i) au lâcher.
  function brancher(svg, rappels) {
    let geste = null;
    const versMm = (ev) => {
      const p = svg.createSVGPoint();
      p.x = ev.clientX;
      p.y = ev.clientY;
      const m = p.matrixTransform(svg.getScreenCTM().inverse());
      return [m.x, -m.y];
    };
    svg.addEventListener("pointerdown", (ev) => {
      const cible = ev.target.closest("[data-i]");
      if (!cible || !rappels.actif()) return;
      const i = Number(cible.dataset.i);
      const [mx, my] = versMm(ev);
      const e = rappels.element(i);
      geste = { i, dx: (Number(e.x) || 0) - mx, dy: (Number(e.y) || 0) - my, bouge: false };
      svg.setPointerCapture(ev.pointerId);
      rappels.choisir(i);
      ev.preventDefault();
    });
    svg.addEventListener("pointermove", (ev) => {
      if (!geste) return;
      const [mx, my] = versMm(ev);
      const pas = ev.shiftKey ? 0.1 : 1; // Maj : au dixième de millimètre
      const x = Math.round((mx + geste.dx) / pas) * pas, y = Math.round((my + geste.dy) / pas) * pas;
      geste.bouge = true;
      rappels.deplacer(geste.i, +x.toFixed(1), +y.toFixed(1));
    });
    const lacher = () => {
      if (geste && geste.bouge) rappels.fin(geste.i);
      geste = null;
    };
    svg.addEventListener("pointerup", lacher);
    svg.addEventListener("pointercancel", lacher);
  }

  function nouvelElement(type, recette) {
    const [W, H] = dimensionsBase(recette.base);
    // premier emplacement libre d'une grille 3 × 3 (centre d'abord), pour ne pas empiler les éléments
    const places = [[0.5, 0.5], [0.25, 0.5], [0.75, 0.5], [0.5, 0.25], [0.5, 0.75], [0.25, 0.25], [0.75, 0.25], [0.25, 0.75], [0.75, 0.75]];
    const libre = (fx, fy) => recette.elements.every((a) => Math.hypot((Number(a.x) || 0) - W * fx, (Number(a.y) || 0) - H * fy) > Math.min(W, H) / 8);
    const [fx, fy] = places.find(([px, py]) => libre(px, py)) || places[0];
    const e = { type, mode: "retirer", x: +(W * fx).toFixed(1), y: +(H * fy).toFixed(1) };
    for (const [cle, , defaut] of TYPES[type].champs) e[cle] = defaut;
    // éléments par défaut à l'échelle de la plaque
    const echelle = Math.min(W, H) / 200;
    for (const cle of ["diametre", "longueur", "largeur", "hauteur"]) if (e[cle]) e[cle] = Math.max(1, Math.round(e[cle] * Math.min(Math.max(echelle, 0.2), 3)));
    return e;
  }

  racine.EditeurLibre = { TYPES, dessiner, brancher, nouvelElement, contourElement, contourBase, dimensions: dimensionsBase };
})(typeof window !== "undefined" ? window : globalThis);
