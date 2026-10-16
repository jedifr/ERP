import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const contexte = {};
vm.runInNewContext(readFileSync(new URL("../public/schemas.js", import.meta.url), "utf8"), contexte);
const { Schemas } = contexte;
// Formes du catalogue de l'ERP (copie de /api/web/v1/catalogue/ : à rafraîchir si la bibliothèque change).
const { formes } = JSON.parse(readFileSync(new URL("./catalogue-formes.json", import.meta.url), "utf8"));

test("chaque forme du catalogue a son schéma, avec la lettre de chaque cote", () => {
  for (const f of formes) {
    assert.ok(Schemas.FORMES[f.cle], `pas de schéma pour ${f.cle}`);
    // flasque : le second cercle n'existe qu'avec des trous
    const d = Schemas.plane(f, f.cle === "flasque" ? { nb_trous_2: 4 } : {});
    assert.doesNotMatch(d.viewBox + d.contenu, /NaN|Infinity|undefined/, f.cle);
    const lettres = Schemas.lettres(f.parametres);
    // les cotes visibles avec les valeurs par défaut portent leur lettre sur le schéma
    for (const p of f.parametres) {
      if (!lettres[p.cle] || !p.defaut) continue;
      assert.match(d.contenu, new RegExp(`${lettres[p.cle]} = `), `${f.cle} : cote ${p.cle} absente`);
    }
  }
});

test("lettres : seulement les cotes, pas les nombres de trous", () => {
  const l = Schemas.lettres([{ cle: "largeur" }, { cle: "nb_x", entier: true }, { cle: "pas_x" }]);
  assert.deepEqual({ ...l }, { largeur: "A", pas_x: "B" });
});

test("profil et boîte", () => {
  const p = Schemas.profil([30, 80, 30], [90, -90], 500);
  assert.match(p.contenu, /A = 30.*B = 80.*C = 30/s);
  assert.match(p.note, /500 mm/);
  const b = Schemas.boite(300, 200, [40, 40, 0, 40]);
  assert.match(b.contenu, /L = 300/);
  assert.doesNotMatch(b.contenu, /C = /);
  assert.doesNotMatch(p.contenu + b.contenu, /NaN/);
});

test("saisie vide ou absurde : pas d'erreur", () => {
  const rect = formes.find((f) => f.cle === "rectangle");
  assert.doesNotMatch(Schemas.plane(rect, { largeur: 0, hauteur: -5 }).contenu, /NaN/);
  assert.doesNotMatch(Schemas.profil([0, 0], [0], 0).contenu, /NaN/);
  assert.doesNotMatch(Schemas.boite(0, 0, [0, 0, 0, 0]).contenu, /NaN/);
});
