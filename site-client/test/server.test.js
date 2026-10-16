import { test } from "node:test";
import assert from "node:assert/strict";

import { creerServeur } from "../server.js";

async function demarrer(options, appels = []) {
  const fetchImpl = async (url, init = {}) => {
    appels.push({ url: String(url), init });
    const chemin = new URL(url).pathname;
    if (init.headers["X-Cle-Web"] !== "cle") return new Response(JSON.stringify({ detail: "refusé" }), { status: 403 });
    if (chemin.endsWith("/catalogue/")) return Response.json({ matieres: [{ matiere: "S235", epaisseurs: [2] }], formes: [], minimum_commande_ht: 60, quantite_max: 500 });
    if (chemin.endsWith("/bibliotheque/")) return Response.json({ credit: "game-icons.net, CC BY 3.0", formes: [{ cle: "anchor", nom: "Ancre" }] });
    if (chemin.endsWith("/prix/")) return Response.json({ statut: "ok", prix_total_ht: 12.5, recu: JSON.parse(init.body) });
    if (chemin.endsWith("/dxf/")) return Response.json({ jeton: "a".repeat(64), taille: JSON.parse(init.body).contenu.length });
    if (chemin.endsWith("/demandes/")) return Response.json({ numero: "WEB2610-0001", total_ht: 60 }, { status: 201 });
    return new Response("{}", { status: 404 });
  };
  const serveur = creerServeur({ erpUrl: "http://erp.test", cle: "cle", fetchImpl, ...options });
  await new Promise((r) => serveur.listen(0, r));
  const base = `http://127.0.0.1:${serveur.address().port}`;
  return { base, fermer: () => new Promise((r) => serveur.close(r)) };
}

test("relaie le catalogue avec la clé, une seule fois grâce au cache", async () => {
  const appels = [];
  const { base, fermer } = await demarrer({}, appels);
  for (let i = 0; i < 2; i++) {
    const r = await fetch(`${base}/api/catalogue`);
    assert.equal(r.status, 200);
    assert.equal((await r.json()).matieres[0].matiere, "S235");
  }
  assert.equal(appels.length, 1);
  assert.equal(appels[0].url, "http://erp.test/api/web/v1/catalogue/");
  await fermer();
});

test("relaie la bibliothèque de formes, mise en cache à part du catalogue", async () => {
  const appels = [];
  const { base, fermer } = await demarrer({}, appels);
  for (let i = 0; i < 2; i++) {
    assert.equal((await (await fetch(`${base}/api/bibliotheque`)).json()).formes[0].nom, "Ancre");
    assert.ok((await (await fetch(`${base}/api/catalogue`)).json()).matieres);
  }
  assert.deepEqual(appels.map((a) => a.url), ["http://erp.test/api/web/v1/bibliotheque/", "http://erp.test/api/web/v1/catalogue/"]);
  await fermer();
});

test("relaie un prix et une demande sans exposer la clé", async () => {
  const { base, fermer } = await demarrer({});
  const r = await fetch(`${base}/api/prix`, { method: "POST", body: JSON.stringify({ matiere: "S235", quantite: 3 }) });
  const corps = await r.json();
  assert.equal(corps.prix_total_ht, 12.5);
  assert.deepEqual(corps.recu, { matiere: "S235", quantite: 3 });
  const d = await fetch(`${base}/api/demandes`, { method: "POST", body: "{}" });
  assert.equal(d.status, 201);
  assert.equal((await d.json()).numero, "WEB2610-0001");
  await fermer();
});

test("limite les calculs par visiteur", async () => {
  const { base, fermer } = await demarrer({ debitParMinute: 2 });
  const statuts = [];
  for (let i = 0; i < 3; i++) statuts.push((await fetch(`${base}/api/prix`, { method: "POST", body: "{}" })).status);
  assert.deepEqual(statuts, [200, 200, 429]);
  await fermer();
});

test("ERP absent, clé refusée ou corps illisible : message propre", async () => {
  let s = await demarrer({ erpUrl: "" });
  assert.equal((await fetch(`${s.base}/api/catalogue`)).status, 503);
  await s.fermer();
  s = await demarrer({ cle: "mauvaise" });
  const r = await fetch(`${s.base}/api/catalogue`);
  assert.equal(r.status, 503);
  assert.match((await r.json()).erreur, /indisponible/);
  assert.equal((await fetch(`${s.base}/api/prix`, { method: "POST", body: "{pas du json" })).status, 400);
  await s.fermer();
});

test("sert la page et ses fichiers, jamais en dehors du dossier public", async () => {
  const { base, fermer } = await demarrer({ contact: "devis@example.com" });
  const page = await fetch(`${base}/`);
  assert.equal(page.status, 200);
  assert.match(page.headers.get("content-security-policy"), /default-src 'self'/);
  assert.match(await page.text(), /Configurer une pièce/);
  assert.equal((await fetch(`${base}/app.js`)).headers.get("content-type"), "text/javascript; charset=utf-8");
  assert.equal((await fetch(`${base}/..%2fserver.js`)).status, 404);
  assert.equal((await fetch(`${base}/api/config`).then((r) => r.json())).contact, "devis@example.com");
  await fermer();
});

test("réponse non JSON de l'ERP (hôte refusé) : indisponible", async () => {
  const fetchImpl = async () => new Response("<h1>Bad Request (400)</h1>", { status: 400, headers: { "Content-Type": "text/html" } });
  const serveur = creerServeur({ erpUrl: "http://erp.test", cle: "cle", fetchImpl });
  await new Promise((r) => serveur.listen(0, r));
  const r = await fetch(`http://127.0.0.1:${serveur.address().port}/api/catalogue`);
  assert.equal(r.status, 503);
  await new Promise((r) => serveur.close(r));
});

test("relaie un fichier DXF jusqu'à 2 Mo en base64, refuse au-delà", async () => {
  const { base, fermer } = await demarrer({});
  const contenu = "A".repeat(2 * 1024 * 1024);
  const r = await fetch(`${base}/api/dxf`, { method: "POST", body: JSON.stringify({ nom: "p.dxf", contenu }) });
  assert.equal(r.status, 200);
  assert.equal((await r.json()).taille, contenu.length);
  const trop = await fetch(`${base}/api/dxf`, { method: "POST", body: JSON.stringify({ nom: "p.dxf", contenu: contenu + "A".repeat(300 * 1024) }) });
  assert.equal(trop.status, 413);
  const prix = await fetch(`${base}/api/prix`, { method: "POST", body: JSON.stringify({ x: "A".repeat(300 * 1024) }) });
  assert.equal(prix.status, 413);
  await fermer();
});

test("sert OpenCascade (STEP) : wasm typé, politique élargie pour son seul worker", async () => {
  const { base, fermer } = await demarrer({});
  const page = await fetch(`${base}/`);
  assert.doesNotMatch(page.headers.get("content-security-policy"), /unsafe-eval/);
  const wasm = await fetch(`${base}/vendor/occt-import-js.wasm`);
  assert.equal(wasm.headers.get("content-type"), "application/wasm");
  await wasm.arrayBuffer();
  const worker = await fetch(`${base}/vendor/occt-worker.js`);
  assert.match(worker.headers.get("content-security-policy"), /script-src 'self' 'unsafe-eval' 'wasm-unsafe-eval'/);
  await worker.text();
  await fermer();
});
