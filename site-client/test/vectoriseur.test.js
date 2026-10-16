import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const contexte = {};
vm.runInNewContext(readFileSync(new URL("../public/vectoriseur.js", import.meta.url), "utf8"), contexte);
const { Vectoriseur } = contexte;

// Image de travail à 4 px/mm : `dessin(x, y)` en mm dit si le point est noir.
const PX = 4;
function image(largeurMm, hauteurMm, dessin) {
  const w = largeurMm * PX, h = hauteurMm * PX, gris = new Float32Array(w * h);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) gris[y * w + x] = dessin((x + 0.5) / PX, (y + 0.5) / PX) ? 0 : 255;
  return { gris, w, h };
}
const aireMm = (r) => r.contours.reduce((s, c) => s + Vectoriseur._interne.aire(c.points), 0);

test("disque percé : un contour extérieur, un trou, cotes et aire justes", () => {
  const img = image(120, 120, (x, y) => { const d = Math.hypot(x - 60, y - 60); return d <= 50 && d >= 20; });
  const r = Vectoriseur.vectoriser(img, PX, { epaisseurMm: 2 });
  assert.equal(r.contours.length, 2);
  assert.equal(r.contours.filter((c) => c.trou).length, 1);
  assert.ok(Math.abs(r.largeurMm - 100) < 1, `largeur ${r.largeurMm}`);
  const attendu = Math.PI * (50 * 50 - 20 * 20);
  assert.ok(Math.abs(aireMm(r) - attendu) / attendu < 0.02, `aire ${aireMm(r)} pour ${attendu}`);
  assert.equal(r.ecartes, 0);
});

test("règle de l'épaisseur : un jour plus fin que la tôle est comblé, un trait plus fin est retiré", () => {
  // plaque 100 × 60 fendue d'une fente de 1 mm, prolongée d'une languette de 1 mm de large
  const img = image(140, 80, (x, y) => {
    const plaque = x >= 10 && x <= 110 && y >= 10 && y <= 70 && !(x >= 30 && x <= 90 && y >= 39.5 && y <= 40.5);
    const languette = x > 110 && x <= 130 && y >= 39.5 && y <= 40.5;
    return plaque || languette;
  });
  const fin = Vectoriseur.vectoriser(img, PX, { epaisseurMm: 0 });
  assert.equal(fin.contours.length, 2, "sans règle la fente est un trou");
  assert.ok(fin.largeurMm > 115, "sans règle la languette reste");
  const r = Vectoriseur.vectoriser(img, PX, { epaisseurMm: 3 });
  assert.equal(r.contours.length, 1, "fente de 1 mm comblée en tôle de 3 mm");
  assert.ok(Math.abs(r.largeurMm - 100) < 1.5, `languette retirée, largeur ${r.largeurMm}`);
  assert.ok(r.combles > 0 && r.retires > 0);
});

test("un seul morceau : le plus grand est gardé, les autres sont comptés", () => {
  const img = image(200, 100, (x, y) => (x >= 10 && x <= 110 && y >= 10 && y <= 90) || Math.hypot(x - 160, y - 50) <= 20 || Math.hypot(x - 190, y - 10) <= 4);
  const r = Vectoriseur.vectoriser(img, PX, { epaisseurMm: 1 });
  assert.equal(r.contours.length, 1);
  assert.equal(r.ecartes, 2);
  assert.ok(Math.abs(r.largeurMm - 100) < 1);
});

test("inverser : le fond clair devient la matière ; image blanche = rien à découper", () => {
  const img = image(60, 60, (x, y) => Math.hypot(x - 30, y - 30) <= 10);
  const r = Vectoriseur.vectoriser(img, PX, { inverser: true, seuil: 128 });
  assert.equal(r.contours.length, 2, "carré avec un trou rond");
  assert.ok(Math.abs(r.largeurMm - 60) < 1);
  assert.ok(Vectoriseur.vectoriser(image(20, 20, () => false), PX, { seuil: 128 }).vide);
});

test("grossir le trait : un dessin au trait fin devient découpable", () => {
  // cercle tracé au trait de 0,5 mm : retiré en 2 mm, gardé une fois grossi de 2 mm
  const img = image(80, 80, (x, y) => Math.abs(Math.hypot(x - 40, y - 40) - 30) <= 0.25 || (Math.abs(x - 40) <= 0.25 && Math.abs(y - 40) <= 30));
  assert.ok(Vectoriseur.vectoriser(img, PX, { epaisseurMm: 2, seuil: 128 }).vide);
  const r = Vectoriseur.vectoriser(img, PX, { epaisseurMm: 2, grossirMm: 2, seuil: 128 });
  assert.equal(r.contours.length, 3, "anneau barré : un extérieur, deux demi-disques évidés");
});

test("SVG en mm, sans valeur invalide", () => {
  const r = Vectoriseur.vectoriser(image(50, 30, (x, y) => x >= 5 && x <= 45 && y >= 5 && y <= 25), PX, {});
  const svg = Vectoriseur.enSvg(r);
  assert.match(svg, /width="40(\.\d+)?mm" height="20(\.\d+)?mm"/);
  assert.doesNotMatch(svg, /NaN|Infinity|undefined/);
  assert.equal((svg.match(/Z/g) || []).length, 1);
});

test("seuil d'Otsu entre deux niveaux", () => {
  const s = Vectoriseur.seuilOtsu(Float32Array.from([...Array(50).fill(30), ...Array(50).fill(220)]));
  assert.ok(s > 30 && s < 220);
});

test("image basse résolution agrandie : bords droits sans escalier, angles gardés vifs", () => {
  // parallélogramme dessiné sur 60 × 30 pixels puis agrandi 5 fois (pixels en gros carrés, comme un petit logo)
  const S = 5, w = 60 * S, h = 30 * S, gris = new Float32Array(w * h);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    const sx = Math.floor(x / S) + 0.5, sy = Math.floor(y / S) + 0.5;
    gris[y * w + x] = sy >= 5 && sy <= 25 && sx >= 5 + (25 - sy) && sx <= 35 + (25 - sy) ? 0 : 255;
  }
  const r = Vectoriseur.vectoriser({ gris, w, h }, 2, { lissage: 1, pxSource: S, seuil: 128 });
  assert.equal(r.contours.length, 1);
  const c = r.contours[0];
  assert.ok(c.sommets.length <= 8, `${c.sommets.length} sommets : l'escalier des pixels est resté`);
  assert.ok(c.coins.filter(Boolean).length >= 4, "les quatre angles du parallélogramme restent vifs");
  assert.match(Vectoriseur.chemin(r), /^M[^NaZ]*Z$/);
});
