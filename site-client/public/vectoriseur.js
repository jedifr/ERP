/* Vectorisation d'un dessin (photo, scan, image PNG / JPG) en contour découpable, entièrement dans le navigateur.
 *
 * Étapes : niveaux de gris (le transparent compte comme du blanc) → lissage (flou) → seuil (foncé = matière, ou
 * l'inverse) → trait grossi si demandé → règle de découpe : ni jour ni trait plus fin que l'épaisseur de la tôle
 * (fermeture puis ouverture morphologiques au disque de diamètre = épaisseur) → on ne garde que le plus grand morceau
 * d'un seul tenant (une pièce par fichier) → contours suivis sur les bords des pixels, simplifiés (Douglas-Peucker).
 *
 * Le résultat part à l'ERP en SVG (cotes en mm) par le même chemin qu'un fichier envoyé par le client : l'ERP le lit,
 * le contrôle, le garde pour la fabrication et le chiffre. Aucun appel réseau ici ; testé sous Node (test/vectoriseur.test.js). */
(function (racine) {
  "use strict";

  const PX_MAX = 1000; // plus grand côté de l'image de travail, en pixels
  const PX_PAR_MM_MAX = 8; // au-delà, aucun gain pour la découpe

  // Résolution de travail : assez fine pour l'épaisseur, bornée pour rester rapide sur un téléphone.
  function resolution(largeurMm, hauteurMm) {
    return Math.min(PX_PAR_MM_MAX, PX_MAX / Math.max(largeurMm, hauteurMm, 1));
  }

  // Seuil d'Otsu sur un histogramme 0..255 (sépare au mieux le dessin du fond).
  function seuilOtsu(gris) {
    const histo = new Float64Array(256);
    for (let i = 0; i < gris.length; i++) { const v = (gris[i] + 0.5) | 0; histo[v < 0 ? 0 : v > 255 ? 255 : v]++; }
    const total = gris.length;
    let somme = 0;
    for (let v = 0; v < 256; v++) somme += v * histo[v];
    let sommeB = 0, poidsB = 0, meilleur = 0, seuil = 128;
    for (let v = 0; v < 256; v++) {
      poidsB += histo[v];
      if (!poidsB) continue;
      const poidsF = total - poidsB;
      if (!poidsF) break;
      sommeB += v * histo[v];
      const mB = sommeB / poidsB, mF = (somme - sommeB) / poidsF;
      const ecart = poidsB * poidsF * (mB - mF) * (mB - mF);
      if (ecart > meilleur) { meilleur = ecart; seuil = v + 0.5; }
    }
    return seuil;
  }

  // Flou par trois passes de moyenne glissante (≈ gaussien de rayon r).
  function flouter(gris, w, h, r) {
    r = Math.round(r);
    if (r < 1) return gris;
    let a = Float32Array.from(gris), b = new Float32Array(a.length);
    // une passe le long des lignes (pas 1) ou des colonnes (pas w), bords prolongés
    const passe = (src, dst, n, m, pas, saut) => {
      for (let j = 0; j < m; j++) {
        const o = j * saut, dernier = o + (n - 1) * pas;
        let s = 0;
        for (let i = -r; i <= r; i++) s += src[i < 0 ? o : i >= n ? dernier : o + i * pas];
        for (let i = 0; i < n; i++) {
          dst[o + i * pas] = s / (2 * r + 1);
          const entre = i + r + 1, sort = i - r;
          s += src[entre >= n ? dernier : o + entre * pas] - src[sort < 0 ? o : o + sort * pas];
        }
      }
    };
    for (let k = 0; k < 3; k++) { passe(a, b, w, h, 1, w); passe(b, a, h, w, w, 1); }
    return a;
  }

  // Carré de la distance euclidienne de chaque pixel au plus proche pixel « vrai » (Felzenszwalb & Huttenlocher).
  function distance2(masque, w, h) {
    const INF = 1e20, d = new Float64Array(w * h);
    for (let i = 0; i < d.length; i++) d[i] = masque[i] ? 0 : INF;
    const n = Math.max(w, h), f = new Float64Array(n), z = new Float64Array(n + 1), v = new Int32Array(n), sortie = new Float64Array(n);
    const ligne = (len) => {
      let k = 0;
      v[0] = 0; z[0] = -INF; z[1] = INF;
      for (let q = 1; q < len; q++) {
        const inter = () => ((f[q] + q * q) - (f[v[k]] + v[k] * v[k])) / (2 * q - 2 * v[k]);
        let s = inter();
        while (s <= z[k]) { k--; s = inter(); }
        k++; v[k] = q; z[k] = s; z[k + 1] = INF;
      }
      k = 0;
      for (let q = 0; q < len; q++) {
        while (z[k + 1] < q) k++;
        sortie[q] = (q - v[k]) * (q - v[k]) + f[v[k]];
      }
    };
    for (let x = 0; x < w; x++) {
      for (let y = 0; y < h; y++) f[y] = d[y * w + x];
      ligne(h);
      for (let y = 0; y < h; y++) d[y * w + x] = sortie[y];
    }
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) f[x] = d[y * w + x];
      ligne(w);
      for (let x = 0; x < w; x++) d[y * w + x] = sortie[x];
    }
    return d;
  }

  // Dilatation / érosion par un disque de rayon r (pixels), via la distance : exactes et en temps linéaire.
  function dilater(m, w, h, r) {
    if (r <= 0) return m;
    const d = distance2(m, w, h), out = new Uint8Array(m.length);
    for (let i = 0; i < m.length; i++) out[i] = d[i] <= r * r ? 1 : 0;
    return out;
  }
  function eroder(m, w, h, r) {
    if (r <= 0) return m;
    const inverse = new Uint8Array(m.length);
    for (let i = 0; i < m.length; i++) inverse[i] = m[i] ? 0 : 1;
    const d = distance2(inverse, w, h), out = new Uint8Array(m.length);
    for (let i = 0; i < m.length; i++) out[i] = d[i] > r * r ? 1 : 0;
    return out;
  }

  // Composantes 4-connexes du masque ; rend le plus grand morceau seul et le nombre de morceaux écartés.
  function plusGrandMorceau(m, w, h) {
    const etiq = new Int32Array(m.length), pile = new Int32Array(m.length), tailles = [0];
    for (let depart = 0; depart < m.length; depart++) {
      if (!m[depart] || etiq[depart]) continue;
      const e = tailles.length;
      let haut = 0, n = 0;
      pile[haut++] = depart;
      etiq[depart] = e;
      while (haut) {
        const p = pile[--haut], x = p % w;
        n++;
        if (x > 0 && m[p - 1] && !etiq[p - 1]) { etiq[p - 1] = e; pile[haut++] = p - 1; }
        if (x < w - 1 && m[p + 1] && !etiq[p + 1]) { etiq[p + 1] = e; pile[haut++] = p + 1; }
        if (p >= w && m[p - w] && !etiq[p - w]) { etiq[p - w] = e; pile[haut++] = p - w; }
        if (p < m.length - w && m[p + w] && !etiq[p + w]) { etiq[p + w] = e; pile[haut++] = p + w; }
      }
      tailles.push(n);
    }
    let garde = 0;
    for (let e = 1; e < tailles.length; e++) if (tailles[e] > (tailles[garde] || 0)) garde = e;
    const out = new Uint8Array(m.length);
    if (garde) for (let i = 0; i < m.length; i++) out[i] = etiq[i] === garde ? 1 : 0;
    return { masque: out, ecartes: Math.max(tailles.length - 2, 0), pixels: tailles[garde] || 0 };
  }

  // Contours sur les bords des pixels, matière à droite du sens de parcours (écran, y vers le bas).
  // Aux points selles on tourne vers le pixel courant : deux pixels qui ne se touchent que par un coin restent séparés.
  function contours(m, w, h) {
    const W = w + 1, sorties = new Uint8Array(W * (h + 1)); // bits : 1 est, 2 sud, 4 ouest, 8 nord
    const DX = [1, 0, -1, 0], DY = [0, 1, 0, -1];
    const plein = (x, y) => x >= 0 && y >= 0 && x < w && y < h && m[y * w + x] === 1;
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        if (!m[y * w + x]) continue;
        if (!plein(x, y - 1)) sorties[y * W + x] |= 1;
        if (!plein(x + 1, y)) sorties[y * W + x + 1] |= 2;
        if (!plein(x, y + 1)) sorties[(y + 1) * W + x + 1] |= 4;
        if (!plein(x - 1, y)) sorties[(y + 1) * W + x] |= 8;
      }
    }
    const boucles = [];
    for (let depart = 0; depart < sorties.length; depart++) {
      while (sorties[depart]) {
        const dirDepart = [0, 1, 2, 3].find((d) => sorties[depart] & (1 << d));
        const points = [];
        let v = depart, dir = dirDepart;
        for (;;) {
          sorties[v] &= ~(1 << dir);
          points.push([v % W, Math.floor(v / W)]);
          v += DX[dir] + DY[dir] * W;
          if (v === depart) break;
          const choix = [(dir + 1) % 4, dir, (dir + 3) % 4].find((d) => sorties[v] & (1 << d));
          if (choix === undefined) break; // ne devrait pas arriver : chaque sommet a autant d'entrées que de sorties
          dir = choix;
        }
        if (points.length >= 4) boucles.push(points);
      }
    }
    return boucles;
  }

  // Aire signée (repère écran) : > 0 pour un parcours horaire à l'écran.
  function aire(points) {
    let s = 0;
    for (let i = 0, j = points.length - 1; i < points.length; j = i++) s += points[j][0] * points[i][1] - points[i][0] * points[j][1];
    return s / 2;
  }

  // Douglas-Peucker sur une boucle fermée (coupée en deux aux points les plus éloignés).
  function simplifier(points, eps) {
    if (points.length < 4) return points;
    const dp = (pts) => {
      const garde = new Uint8Array(pts.length);
      garde[0] = garde[pts.length - 1] = 1;
      const pile = [[0, pts.length - 1]];
      while (pile.length) {
        const [a, b] = pile.pop();
        const [ax, ay] = pts[a], [bx, by] = pts[b];
        const dx = bx - ax, dy = by - ay, l = Math.hypot(dx, dy) || 1;
        let max = -1, im = -1;
        for (let i = a + 1; i < b; i++) {
          const d = Math.abs(dy * (pts[i][0] - ax) - dx * (pts[i][1] - ay)) / l;
          if (d > max) { max = d; im = i; }
        }
        if (max > eps) { garde[im] = 1; pile.push([a, im], [im, b]); }
      }
      return pts.filter((_, i) => garde[i]);
    };
    let loin = 0, dmax = -1;
    for (let i = 1; i < points.length; i++) {
      const d = Math.hypot(points[i][0] - points[0][0], points[i][1] - points[0][1]);
      if (d > dmax) { dmax = d; loin = i; }
    }
    const a = dp(points.slice(0, loin + 1)), b = dp([...points.slice(loin), points[0]]);
    return [...a.slice(0, -1), ...b.slice(0, -1)];
  }

  // Boucle de sommets de pixels → points : milieu de chaque segment droit (l'escalier des pixels devient une pente),
  // plus les vrais angles (deux segments d'au moins 2 pixels) gardés tels quels pour que les coins restent vifs.
  function lisser(boucle) {
    const n = boucle.length, segments = [];
    const dirige = (i) => { const p = boucle[i], q = boucle[(i + 1) % n]; return (q[0] - p[0]) + 2 * (q[1] - p[1]); };
    let debut = 0;
    while (debut < n && dirige((debut + n - 1) % n) === dirige(debut)) debut++; // commencer sur un changement de direction
    debut %= n;
    for (let k = 0; k < n;) {
      const i = (debut + k) % n, d = dirige(i);
      let l = 1;
      while (k + l < n && dirige((debut + k + l) % n) === d) l++;
      segments.push({ depart: boucle[i], fin: boucle[(i + l) % n], l });
      k += l;
    }
    const points = [];
    segments.forEach((s, i) => {
      const avant = segments[(i + segments.length - 1) % segments.length];
      if (avant.l >= 2 && s.l >= 2) points.push(s.depart);
      points.push([(s.depart[0] + s.fin[0]) / 2, (s.depart[1] + s.fin[1]) / 2]);
    });
    return points;
  }

  /* image : { gris: Float32Array|Uint8Array (0 = noir, 255 = blanc), w, h } à la résolution `pxParMm`.
   * options : { seuil (0..255, sinon Otsu), inverser, lissage (0..4), grossirMm, epaisseurMm }.
   * Rend { contours: [{ points: [[x, y] mm, repère écran], trou }], largeurMm, hauteurMm, seuil, ecartes, combles, retires, vide }. */
  function vectoriser(image, pxParMm, options = {}) {
    const { w, h } = image;
    const lissage = Math.max(0, Math.min(4, Number(options.lissage) || 0));
    const gris = flouter(image.gris, w, h, lissage * Math.max(1, pxParMm * 0.2));
    const seuil = Number.isFinite(options.seuil) ? options.seuil : seuilOtsu(gris);
    // marge blanche autour : les morphologies et le suivi de contour ne butent jamais sur le bord de l'image
    const rRegle = Math.max(0, (Number(options.epaisseurMm) || 0) * pxParMm / 2);
    const rGros = Math.max(0, (Number(options.grossirMm) || 0) * pxParMm / 2);
    const marge = Math.ceil(2 * rRegle + rGros) + 2, W = w + 2 * marge, H = h + 2 * marge;
    let m = new Uint8Array(W * H);
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        const fonce = gris[y * w + x] < seuil;
        m[(y + marge) * W + x + marge] = fonce !== Boolean(options.inverser) ? 1 : 0;
      }
    }
    m = dilater(m, W, H, rGros);
    // règle de découpe : jours trop fins comblés (fermeture), traits trop fins retirés (ouverture)
    const avant = m.reduce((s, v) => s + v, 0);
    const ferme = eroder(dilater(m, W, H, rRegle), W, H, rRegle);
    const apresFermeture = ferme.reduce((s, v) => s + v, 0);
    const ouvert = dilater(eroder(ferme, W, H, rRegle), W, H, rRegle);
    const apresOuverture = ouvert.reduce((s, v) => s + v, 0);
    const morceau = plusGrandMorceau(ouvert, W, H);
    const pxCarre = pxParMm * pxParMm;
    const resultat = {
      contours: [], largeurMm: 0, hauteurMm: 0, seuil, ecartes: morceau.ecartes,
      combles: Math.max(0, apresFermeture - avant) / pxCarre, retires: Math.max(0, apresFermeture - apresOuverture) / pxCarre,
      vide: !morceau.pixels,
    };
    if (resultat.vide) return resultat;
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const boucle of contours(morceau.masque, W, H)) {
      const points = simplifier(lisser(boucle), 0.6).map(([x, y]) => [(x - marge) / pxParMm, (y - marge) / pxParMm]);
      if (points.length < 3) continue;
      for (const [x, y] of points) { minX = Math.min(minX, x); minY = Math.min(minY, y); maxX = Math.max(maxX, x); maxY = Math.max(maxY, y); }
      resultat.contours.push({ points, trou: aire(points) < 0 });
    }
    resultat.largeurMm = maxX - minX;
    resultat.hauteurMm = maxY - minY;
    resultat.boite = [minX, minY, maxX, maxY];
    return resultat;
  }

  // SVG en millimètres (largeur / hauteur en mm : l'ERP en tire l'échelle sans deviner).
  function enSvg(resultat) {
    const [x0, y0] = resultat.boite, W = resultat.largeurMm, H = resultat.hauteurMm;
    const f = (v) => String(+v.toFixed(3));
    const d = resultat.contours.map((c) => "M" + c.points.map(([x, y]) => `${f(x - x0)} ${f(y - y0)}`).join("L") + "Z").join("");
    return `<?xml version="1.0" encoding="UTF-8"?>\n<svg xmlns="http://www.w3.org/2000/svg" width="${f(W)}mm" height="${f(H)}mm" viewBox="0 0 ${f(W)} ${f(H)}">`
      + `<path fill="#000" fill-rule="evenodd" d="${d}"/></svg>\n`;
  }

  racine.Vectoriseur = { resolution, seuilOtsu, vectoriser, enSvg, _interne: { dilater, eroder, plusGrandMorceau, contours, simplifier, aire } };
})(typeof window !== "undefined" ? window : globalThis);
