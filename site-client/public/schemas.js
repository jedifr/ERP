// Schémas cotés du configurateur : la pièce dessinée à l'échelle avec ses cotes repérées par des lettres (A, B, C…),
// les mêmes lettres que devant les champs de saisie. Purement visuel : le prix et la géométrie de référence viennent de l'ERP.
"use strict";

(function (racine) {
  const fmt = (v) => (Math.round(v * 10) / 10).toLocaleString("fr-FR");
  const echapper = (t) => String(t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

  // Lettre de chaque cote saisie : les paramètres non entiers (les nombres de trous ou de côtés n'ont pas de cote dessinée).
  function lettres(parametres) {
    const l = {};
    let i = 0;
    for (const p of parametres) if (!p.entier && !p.texte) l[p.cle] = String.fromCharCode(65 + i++);
    return l;
  }

  // ------------------------------------------------------------------ dessin (repère mm, y vers le haut ; SVG y vers le bas)
  // Facteur de taille du texte : 1 pour un schéma d'environ 440 px de large ; plus petit quand le schéma s'affiche en
  // grand (écran large), pour que les cotes gardent la même taille à l'écran.
  let facteurTexte = 1;
  const reglerTexte = (f) => { facteurTexte = Number.isFinite(f) && f > 0 ? f : 1; };

  function Dessin(taille) {
    const s = taille * 0.07 * facteurTexte; // hauteur de texte
    const morceaux = [];
    const boite = { x0: Infinity, y0: Infinity, x1: -Infinity, y1: -Infinity };
    const etendre = (x, y) => { boite.x0 = Math.min(boite.x0, x); boite.x1 = Math.max(boite.x1, x); boite.y0 = Math.min(boite.y0, y); boite.y1 = Math.max(boite.y1, y); };
    const pt = ([x, y]) => { etendre(x, y); return `${+x.toFixed(3)},${+(-y).toFixed(3)}`; };
    // attrs : attributs supplémentaires déjà sûrs (nombres, mots-clés), ex. data-i="3" pour l'éditeur de forme libre
    const chemin = (anneaux, classe, attrs = "") => morceaux.push(`<path class="${classe}" fill-rule="evenodd" ${attrs} d="${anneaux.map((a) => "M" + a.map(pt).join("L") + "Z").join(" ")}"/>`);
    const ligne = (a, b, classe = "cote") => morceaux.push(`<path class="${classe}" d="M${pt(a)}L${pt(b)}"/>`);
    const fleche = (pointe, depuis) => {
      const [dx, dy] = [depuis[0] - pointe[0], depuis[1] - pointe[1]];
      const n = Math.hypot(dx, dy) || 1, ux = dx / n, uy = dy / n, l = s * 0.55, e = s * 0.2;
      morceaux.push(`<path class="fleche" d="M${pt(pointe)}L${pt([pointe[0] + ux * l - uy * e, pointe[1] + uy * l + ux * e])}L${pt([pointe[0] + ux * l + uy * e, pointe[1] + uy * l - ux * e])}Z"/>`);
    };
    const texte = ([x, y], t, ancre = "middle") => {
      const largeur = String(t).length * s * 0.58;
      const x0 = ancre === "middle" ? x - largeur / 2 : ancre === "end" ? x - largeur : x;
      etendre(x0, y - s * 0.5); etendre(x0 + largeur, y + s * 0.5);
      morceaux.push(`<text class="cote-texte" x="${+x.toFixed(3)}" y="${+(-y).toFixed(3)}" font-size="${+s.toFixed(3)}" stroke-width="${+(s * 0.3).toFixed(3)}" text-anchor="${ancre}" dominant-baseline="central">${echapper(t)}</text>`);
    };
    const etiquette = (lettre, valeur) => (lettre ? `${lettre} = ${fmt(valeur)}` : fmt(valeur));

    return {
      s, chemin, ligne, texte, etiquette,
      // Cote linéaire entre a et b, décalée de `dec` (signé) perpendiculairement, côté gauche du vecteur a→b.
      lineaire(a, b, dec, lettre, valeur, sansTexte = false) {
        const [dx, dy] = [b[0] - a[0], b[1] - a[1]];
        const n = Math.hypot(dx, dy);
        if (n < 1e-9) return;
        const nx = -dy / n, ny = dx / n, sg = Math.sign(dec) || 1;
        const a2 = [a[0] + nx * dec, a[1] + ny * dec], b2 = [b[0] + nx * dec, b[1] + ny * dec];
        const dep = s * 0.3 * sg;
        ligne(a, [a2[0] + nx * dep, a2[1] + ny * dep], "rappel");
        ligne(b, [b2[0] + nx * dep, b2[1] + ny * dep], "rappel");
        ligne(a2, b2);
        fleche(a2, b2); fleche(b2, a2);
        if (sansTexte) return;
        // texte à côté du trait de cote, sans le chevaucher (plus loin si le trait est vertical)
        const t = etiquette(lettre, valeur), ecart = Math.abs(nx) * (t.length * s * 0.29 + s * 0.4) + Math.abs(ny) * s * 0.75;
        texte([(a2[0] + b2[0]) / 2 + nx * ecart * sg, (a2[1] + b2[1]) / 2 + ny * ecart * sg], t);
      },
      // Diamètre d'un cercle (cx, cy, r) tracé suivant l'angle `angle` (degrés), étiquette au-delà du bord.
      diametre([cx, cy], r, angle, lettre, valeur, prefixe = "Ø") {
        const u = [Math.cos(angle * Math.PI / 180), Math.sin(angle * Math.PI / 180)];
        const a = [cx - u[0] * r, cy - u[1] * r], b = [cx + u[0] * r, cy + u[1] * r];
        ligne(a, b); fleche(a, b); fleche(b, a);
        const bout = [cx + u[0] * (r + s * 1.2), cy + u[1] * (r + s * 1.2)];
        ligne(b, bout);
        texte([bout[0] + (u[0] >= 0 ? s * 0.2 : -s * 0.2), bout[1] + s * 0.6], `${prefixe}${etiquette(lettre, valeur)}`, u[0] >= 0 ? "start" : "end");
      },
      // Repère d'un petit élément (trou, rayon) : trait de rappel incliné depuis `point`, texte au bout.
      repere(point, direction, texteRepere) {
        const u = [Math.cos(direction * Math.PI / 180), Math.sin(direction * Math.PI / 180)];
        const bout = [point[0] + u[0] * s * 2.2, point[1] + u[1] * s * 2.2];
        ligne(point, bout); fleche(point, bout);
        const pied = [bout[0] + (u[0] >= 0 ? s * 0.8 : -s * 0.8), bout[1]];
        ligne(bout, pied);
        texte([pied[0] + (u[0] >= 0 ? s * 0.2 : -s * 0.2), pied[1]], texteRepere, u[0] >= 0 ? "start" : "end");
      },
      // Angle entre la direction 0° (axe horizontal) et `angle`, mesuré au centre, rayon r.
      angle([cx, cy], r, angle, lettre, valeur) {
        ligne([cx, cy], [cx + r, cy], "axe");
        const n = Math.max(6, Math.ceil(Math.abs(angle) / 8));
        const arc = Array.from({ length: n + 1 }, (_, k) => [cx + r * Math.cos(angle * k / n * Math.PI / 180), cy + r * Math.sin(angle * k / n * Math.PI / 180)]);
        morceaux.push(`<path class="cote" d="M${arc.map(pt).join("L")}"/>`);
        const m = (angle / 2) * Math.PI / 180;
        texte([cx + (r + s * 1.4) * Math.cos(m), cy + (r + s * 1.4) * Math.sin(m)], `${etiquette(lettre, valeur)}°`);
      },
      svg(legende, note = "") {
        if (legende) texte([(boite.x0 + boite.x1) / 2, boite.y0 - s * 1.2], legende);
        const m = s * 0.6;
        const vb = [boite.x0 - m, -boite.y1 - m, boite.x1 - boite.x0 + 2 * m, boite.y1 - boite.y0 + 2 * m].map((v) => +v.toFixed(3));
        return { viewBox: vb.join(" "), contenu: morceaux.join(""), note };
      },
    };
  }

  // ------------------------------------------------------------------ géométrie élémentaire (mêmes conventions que l'ERP)
  const cercle = (cx, cy, r, n = 72) => Array.from({ length: n }, (_, k) => [cx + r * Math.cos(2 * Math.PI * k / n), cy + r * Math.sin(2 * Math.PI * k / n)]);
  function rectangle(w, h, r = 0, c = 0) {
    const t = r || c;
    if (!t || t * 2 > Math.min(w, h)) return [[0, 0], [w, 0], [w, h], [0, h]];
    const pts = [];
    for (const [cx, cy, a0] of [[w - t, t, -90], [w - t, h - t, 0], [t, h - t, 90], [t, t, 180]]) {
      const pas = r ? 6 : 1;
      for (let k = 0; k <= pas; k++) {
        const a = (a0 + 90 * k / pas) * Math.PI / 180;
        pts.push([cx + t * Math.cos(a), cy + t * Math.sin(a)]);
      }
    }
    return pts;
  }
  const percages = (cx, cy, n, dc, a0) => Array.from({ length: Math.max(0, n) }, (_, k) => {
    const a = (a0 + 360 * k / n) * Math.PI / 180;
    return [cx + dc / 2 * Math.cos(a), cy + dc / 2 * Math.sin(a)];
  });

  // ------------------------------------------------------------------ formes de la bibliothèque
  const FORMES = {
    rectangle(v, L, d) {
      const { largeur: w, hauteur: h, rayon_angle: r, chanfrein: c } = v;
      d.chemin([rectangle(w, h, r, c)], "piece");
      d.lineaire([0, 0], [w, 0], -d.s * 1.6, L.largeur, w);
      d.lineaire([0, h], [0, 0], -d.s * 1.6, L.hauteur, h);
      if (r > 0) d.repere([w - r + r * Math.SQRT1_2, h - r + r * Math.SQRT1_2], 45, `R ${d.etiquette(L.rayon_angle, r)}`);
      else if (c > 0) d.repere([w - c / 2, h - c / 2], 45, `${d.etiquette(L.chanfrein, c)} à 45°`);
    },
    disque(v, L, d) {
      const r = v.diametre / 2;
      d.chemin([cercle(r, r, r)], "piece");
      d.diametre([r, r], r, 30, L.diametre, v.diametre);
    },
    anneau(v, L, d) {
      const r = v.diametre_ext / 2, ri = v.diametre_int / 2;
      d.chemin([cercle(r, r, r), cercle(r, r, Math.min(ri, r * 0.98))], "piece");
      d.lineaire([0, r], [2 * r, r], -r - d.s * 1.6, L.diametre_ext, v.diametre_ext);
      d.diametre([r, r], ri, 30, L.diametre_int, v.diametre_int);
    },
    oblong(v, L, d) {
      const { longueur: lo, largeur: la } = v, r = la / 2;
      const pts = [];
      for (let k = 0; k <= 18; k++) pts.push([Math.max(lo - r, r) + r * Math.cos((-90 + 10 * k) * Math.PI / 180), r + r * Math.sin((-90 + 10 * k) * Math.PI / 180)]);
      for (let k = 0; k <= 18; k++) pts.push([r + r * Math.cos((90 + 10 * k) * Math.PI / 180), r + r * Math.sin((90 + 10 * k) * Math.PI / 180)]);
      d.chemin([pts], "piece");
      d.lineaire([0, 0], [lo, 0], -d.s * 1.6, L.longueur, lo);
      d.lineaire([0, la], [0, 0], -d.s * 1.6, L.largeur, la);
    },
    equerre(v, L, d) {
      const { largeur: w, hauteur: h, epaisseur_horizontale: ta, epaisseur_verticale: tb } = v;
      d.chemin([[[0, 0], [w, 0], [w, ta], [tb, ta], [tb, h], [0, h]]], "piece");
      d.lineaire([0, 0], [w, 0], -d.s * 1.6, L.largeur, w);
      d.lineaire([0, h], [0, 0], -d.s * 1.6, L.hauteur, h);
      d.lineaire([w, 0], [w, ta], -d.s * 1.6, L.epaisseur_horizontale, ta);
      d.lineaire([tb, h], [0, h], -d.s * 1.6, L.epaisseur_verticale, tb);
    },
    u(v, L, d) {
      const { largeur: w, hauteur: h, epaisseur_fond: tf, epaisseur_branches: tb } = v;
      d.chemin([[[0, 0], [w, 0], [w, h], [w - tb, h], [w - tb, tf], [tb, tf], [tb, h], [0, h]]], "piece");
      d.lineaire([0, 0], [w, 0], -d.s * 1.6, L.largeur, w);
      d.lineaire([0, h], [0, 0], -d.s * 1.6, L.hauteur, h);
      d.lineaire([w, 0], [w, tf], -d.s * 1.6, L.epaisseur_fond, tf);
      d.lineaire([w, h], [w - tb, h], -d.s * 1.6, L.epaisseur_branches, tb);
    },
    trapeze(v, L, d) {
      const { base_grande: gb, base_petite: pb, hauteur: h } = v, e = (gb - pb) / 2;
      d.chemin([[[0, 0], [gb, 0], [gb - e, h], [e, h]]], "piece");
      d.lineaire([0, 0], [gb, 0], -d.s * 1.6, L.base_grande, gb);
      if (pb > 0) d.lineaire([gb - e, h], [e, h], -d.s * 1.6, L.base_petite, pb);
      d.lineaire([gb / 2, 0], [gb / 2, h], 0.001, L.hauteur, h);
    },
    polygone(v, L, d) {
      const n = Math.max(3, Math.min(64, Math.round(v.nb_cotes))), r = v.diametre / 2;
      d.chemin([Array.from({ length: n }, (_, k) => [r + r * Math.cos(Math.PI / 2 + 2 * Math.PI * k / n), r + r * Math.sin(Math.PI / 2 + 2 * Math.PI * k / n)])], "piece");
      d.chemin([cercle(r, r, r)], "axe");
      d.diametre([r, r], r, 90 + 180 / n + 15, L.diametre, v.diametre);
      return `${n} côtés`;
    },
    platine_grille(v, L, d) {
      const { largeur: w, hauteur: h, rayon_angle: r, pas_x: px, pas_y: py, diametre_trou: dt } = v;
      const nx = Math.max(1, Math.round(v.nb_x)), ny = Math.max(1, Math.round(v.nb_y));
      const x0 = (w - (nx - 1) * px) / 2, y0 = (h - (ny - 1) * py) / 2;
      const trous = [];
      for (let i = 0; i < nx; i++) for (let j = 0; j < ny; j++) trous.push(cercle(x0 + i * px, y0 + j * py, dt / 2, 36));
      d.chemin([rectangle(w, h, r), ...trous], "piece");
      d.lineaire([0, 0], [w, 0], -d.s * 1.6, L.largeur, w);
      d.lineaire([0, h], [0, 0], -d.s * 1.6, L.hauteur, h);
      const haut = y0 + (ny - 1) * py, droite = x0 + (nx - 1) * px;
      if (nx > 1) d.lineaire([x0, haut], [x0 + px, haut], -(h - haut) - d.s * 1.6, L.pas_x, px);
      if (ny > 1) d.lineaire([droite, y0], [droite, y0 + py], -(w - droite) - d.s * 1.6, L.pas_y, py);
      d.repere([x0 + dt / 2 * Math.SQRT1_2, y0 + dt / 2 * Math.SQRT1_2], 40, `Ø${d.etiquette(L.diametre_trou, dt)}`);
      if (r > 0) d.repere([w - r + r * Math.SQRT1_2, r - r * Math.SQRT1_2], -40, `R ${d.etiquette(L.rayon_angle, r)}`);
      return `${nx} × ${ny} trous`;
    },
    platine_cercle(v, L, d) {
      const { largeur: w, hauteur: h, rayon_angle: r, diametre_cercle: dc, diametre_trou: dt, angle_depart: a0 } = v;
      const n = Math.round(v.nb_trous), c = [w / 2, h / 2];
      const centres = percages(c[0], c[1], n, dc, a0);
      d.chemin([rectangle(w, h, r), ...centres.map(([x, y]) => cercle(x, y, dt / 2, 36))], "piece");
      d.chemin([cercle(c[0], c[1], dc / 2)], "axe");
      d.lineaire([0, 0], [w, 0], -d.s * 1.6, L.largeur, w);
      d.lineaire([0, h], [0, 0], -d.s * 1.6, L.hauteur, h);
      d.diametre(c, dc / 2, a0 + 180 / Math.max(n, 1), L.diametre_cercle, dc);
      if (centres.length) d.repere([centres[0][0] + dt / 2 * Math.SQRT1_2, centres[0][1] + dt / 2 * Math.SQRT1_2], 40, `Ø${d.etiquette(L.diametre_trou, dt)}`);
      if (a0) d.angle(c, dc / 4, a0, L.angle_depart, a0);
      if (r > 0) d.repere([w - r + r * Math.SQRT1_2, r - r * Math.SQRT1_2], -40, `R ${d.etiquette(L.rayon_angle, r)}`);
      return `${n} trous`;
    },
    bride(v, L, d) {
      const R = v.diametre_ext / 2, c = [R, R], n = Math.round(v.nb_trous);
      const centres = percages(R, R, n, v.diametre_percage, v.angle_depart);
      d.chemin([cercle(R, R, R), ...(v.alesage > 0 ? [cercle(R, R, v.alesage / 2)] : []), ...centres.map(([x, y]) => cercle(x, y, v.diametre_trou / 2, 36))], "piece");
      if (n) d.chemin([cercle(R, R, v.diametre_percage / 2)], "axe");
      d.lineaire([0, R], [2 * R, R], -R - d.s * 1.6, L.diametre_ext, v.diametre_ext);
      if (v.alesage > 0) d.diametre(c, v.alesage / 2, v.angle_depart + 180 / Math.max(n, 1) + 90, L.alesage, v.alesage);
      if (n) {
        d.diametre(c, v.diametre_percage / 2, v.angle_depart + 180 / n, L.diametre_percage, v.diametre_percage);
        d.repere([centres[0][0] + v.diametre_trou / 2 * Math.SQRT1_2, centres[0][1] + v.diametre_trou / 2 * Math.SQRT1_2], 40, `Ø${d.etiquette(L.diametre_trou, v.diametre_trou)}`);
        if (v.angle_depart) d.angle(c, v.diametre_percage / 4, v.angle_depart, L.angle_depart, v.angle_depart);
      }
      return n ? `${n} trous` : "";
    },
    flasque(v, L, d) {
      const R = v.diametre / 2, c = [R, R];
      const anneaux = [cercle(R, R, R)];
      if (v.alesage > 0) anneaux.push(cercle(R, R, v.alesage / 2));
      const cercles = [];
      for (const i of ["1", "2"]) {
        const n = Math.round(v[`nb_trous_${i}`]);
        if (!n) continue;
        const centres = percages(R, R, n, v[`cercle_${i}`], v[`angle_depart_${i}`]);
        anneaux.push(...centres.map(([x, y]) => cercle(x, y, v[`diametre_trou_${i}`] / 2, 36)));
        cercles.push({ i, n, centres });
      }
      d.chemin(anneaux, "piece");
      d.lineaire([0, R], [2 * R, R], -R - d.s * 1.6, L.diametre, v.diametre);
      if (v.alesage > 0) d.diametre(c, v.alesage / 2, 135, L.alesage, v.alesage);
      for (const { i, n, centres } of cercles) {
        const dc = v[`cercle_${i}`], dt = v[`diametre_trou_${i}`], a0 = v[`angle_depart_${i}`];
        d.chemin([cercle(R, R, dc / 2)], "axe");
        d.diametre(c, dc / 2, a0 + 180 / n + (i === "2" ? 90 : 0), L[`cercle_${i}`], dc);
        d.repere([centres[0][0] + dt / 2 * Math.SQRT1_2, centres[0][1] + dt / 2 * Math.SQRT1_2], i === "1" ? 40 : -40, `Ø${d.etiquette(L[`diametre_trou_${i}`], dt)}`);
        if (a0) d.angle(c, dc / 4, a0, L[`angle_depart_${i}`], a0);
      }
      return cercles.map(({ i, n }) => `cercle ${i} : ${n} trous`).join(", ");
    },
  };

  function plane(forme, valeurs) {
    if (!forme || !FORMES[forme.cle]) return null;
    const v = {};
    for (const p of forme.parametres) v[p.cle] = Number.isFinite(valeurs[p.cle]) ? valeurs[p.cle] : Number(p.defaut) || 0;
    const L = lettres(forme.parametres);
    const tailles = forme.parametres.filter((p) => !p.entier && /largeur|hauteur|longueur|diametre|base_grande/.test(p.cle)).map((p) => v[p.cle]);
    const d = Dessin(Math.max(1, ...tailles.filter((t) => t > 0)));
    const legende = FORMES[forme.cle](v, L, d);
    return d.svg(legende);
  }

  // ------------------------------------------------------------------ profil plié : la section, faces A, B, C… et angles
  function profil(faces, angles, longueur) {
    const pts = [[0, 0]];
    let cap = 0;
    faces.forEach((f, i) => {
      const [x, y] = pts[pts.length - 1];
      pts.push([x + Math.max(f, 0) * Math.cos(cap), y + Math.max(f, 0) * Math.sin(cap)]);
      if (i < angles.length) cap += Math.sign(angles[i] || 1) * (180 - Math.min(Math.abs(angles[i]) || 90, 179)) * Math.PI / 180;
    });
    const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
    const d = Dessin(Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys), 1));
    const cx = (Math.max(...xs) + Math.min(...xs)) / 2, cy = (Math.max(...ys) + Math.min(...ys)) / 2;
    for (let i = 0; i < pts.length - 1; i++) d.ligne(pts[i], pts[i + 1], "piece-trait");
    faces.forEach((f, i) => {
      const [a, b] = [pts[i], pts[i + 1]];
      // cote du côté extérieur de la section (à l'opposé du centre)
      const m = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2], n = [-(b[1] - a[1]), b[0] - a[0]];
      const dehors = (m[0] - cx) * n[0] + (m[1] - cy) * n[1] >= 0 ? 1 : -1;
      d.lineaire(a, b, dehors * d.s * 1.6, String.fromCharCode(65 + i), f);
    });
    angles.forEach((a, i) => {
      const p = pts[i + 1], dedans = [cx - p[0], cy - p[1]], n = Math.hypot(...dedans) || 1;
      d.texte([p[0] + dedans[0] / n * d.s * 1.6, p[1] + dedans[1] / n * d.s * 1.6], `${fmt(Math.abs(a))}°`);
    });
    return d.svg("", `Section du profil, cotes extérieures, angles de la pièce. Longueur du profil : ${fmt(longueur || 0)} mm.`);
  }

  // ------------------------------------------------------------------ boîte pliée : le flan déplié, fond et bords
  function boite(longueur, largeur, bords) {
    const [a, b, c, dd] = bords.map((x) => Math.max(x || 0, 0));
    const L0 = Math.max(longueur, 1), l0 = Math.max(largeur, 1);
    const d = Dessin(Math.max(L0 + b + dd, l0 + a + c));
    const rabats = [];
    if (a) rabats.push([[0, -a], [L0, -a], [L0, 0], [0, 0]]);
    if (c) rabats.push([[0, l0], [L0, l0], [L0, l0 + c], [0, l0 + c]]);
    if (b) rabats.push([[L0, 0], [L0 + b, 0], [L0 + b, l0], [L0, l0]]);
    if (dd) rabats.push([[-dd, 0], [0, 0], [0, l0], [-dd, l0]]);
    for (const r of rabats) d.chemin([r], "piece");
    d.chemin([[[0, 0], [L0, 0], [L0, l0], [0, l0]]], "fond-plat");
    // bords sans rabat : arêtes découpées, en trait plein
    if (!a) d.ligne([0, 0], [L0, 0], "arete");
    if (!c) d.ligne([0, l0], [L0, l0], "arete");
    if (!b) d.ligne([L0, 0], [L0, l0], "arete");
    if (!dd) d.ligne([0, 0], [0, l0], "arete");
    // lignes de pli en pointillés, là où un bord existe
    if (a) d.ligne([0, 0], [L0, 0], "pli");
    if (c) d.ligne([0, l0], [L0, l0], "pli");
    if (b) d.ligne([L0, 0], [L0, l0], "pli");
    if (dd) d.ligne([0, 0], [0, l0], "pli");
    d.lineaire([0, l0 * 0.2], [L0, l0 * 0.2], 0.001, "L", longueur);
    d.lineaire([L0 * 0.5, l0], [L0 * 0.5, 0], 0.001, "l", largeur);
    // hauteur des bords : flèches dans le rabat, valeur à l'extérieur
    if (a) { d.lineaire([L0 * 0.75, 0], [L0 * 0.75, -a], 0.001, "A", a, true); d.texte([L0 * 0.75, -a - d.s * 0.8], `A = ${fmt(a)}`); }
    if (c) { d.lineaire([L0 * 0.75, l0 + c], [L0 * 0.75, l0], 0.001, "C", c, true); d.texte([L0 * 0.75, l0 + c + d.s * 0.8], `C = ${fmt(c)}`); }
    if (b) { d.lineaire([L0, l0 * 0.75], [L0 + b, l0 * 0.75], 0.001, "B", b, true); d.texte([L0 + b + d.s * 0.4, l0 * 0.75], `B = ${fmt(b)}`, "start"); }
    if (dd) { d.lineaire([-dd, l0 * 0.75], [0, l0 * 0.75], 0.001, "D", dd, true); d.texte([-dd - d.s * 0.4, l0 * 0.75], `D = ${fmt(dd)}`, "end"); }
    return d.svg("", "Boîte vue de dessus, bords rabattus à plat : L × l pour le fond, A à D pour la hauteur des bords. Pointillés : lignes de pli.");
  }

  // ------------------------------------------------------------------ contour quelconque (fichier DXF) : encombrement coté
  function contour(c, legende = "") {
    if (!c || !c.exterieur || !c.exterieur.length) return null;
    const xs = c.exterieur.map((p) => p[0]), ys = c.exterieur.map((p) => p[1]);
    const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
    const d = Dessin(Math.max(x1 - x0, y1 - y0, 1));
    d.chemin([c.exterieur, ...(c.trous || [])], "piece");
    d.lineaire([x0, y0], [x1, y0], -d.s * 1.6, "", x1 - x0);
    d.lineaire([x0, y1], [x0, y0], -d.s * 1.6, "", y1 - y0);
    return d.svg("", legende);
  }

  racine.Schemas = { lettres, plane, profil, boite, contour, FORMES, Dessin, cercle, rectangle, reglerTexte };
})(typeof window !== "undefined" ? window : globalThis);
