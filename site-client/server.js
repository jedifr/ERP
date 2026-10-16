// Site client DÉCOUPE H2O : sert la page du configurateur et relaie trois appels vers l'API web de l'ERP.
// La clé de l'ERP reste ici, jamais dans le navigateur. Aucune dépendance : Node 20+ (http, fetch).
//
// Variables d'environnement :
//   ERP_URL            adresse de l'ERP vue depuis ce serveur (tunnel ou réseau privé), ex. http://erp.interne:8000
//   ERP_WEB_CLE_API    même valeur que dans le .env de l'ERP
//   PORT               port d'écoute (3000)
//   DEBIT_PAR_MINUTE   appels de prix par visiteur et par minute (60)
//   CONFIANCE_PROXY    1 si un reverse proxy placé devant pose X-Forwarded-For (adresse du visiteur)
//   CONTACT_EMAIL      adresse affichée pour les demandes de devis (fichier DXF, autre forme)

import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { extname, join, normalize } from "node:path";
import { fileURLToPath } from "node:url";

const RACINE = join(fileURLToPath(new URL(".", import.meta.url)), "public");
const TYPES = { ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".ico": "image/x-icon", ".png": "image/png" };
const TAILLE_MAX = 64 * 1024;
const CATALOGUE_TTL_MS = 5 * 60 * 1000;

export function creerServeur({ erpUrl, cle, debitParMinute = 60, confianceProxy = false, contact = "", fetchImpl = fetch } = {}) {
  const compteurs = new Map();
  let catalogue = null;

  function adresse(req) {
    if (confianceProxy && req.headers["x-forwarded-for"]) return String(req.headers["x-forwarded-for"]).split(",")[0].trim();
    return req.socket.remoteAddress || "inconnue";
  }

  function limite(req) {
    const maintenant = Date.now(), ip = adresse(req);
    const c = compteurs.get(ip);
    if (!c || maintenant - c.debut > 60_000) {
      compteurs.set(ip, { debut: maintenant, n: 1 });
      if (compteurs.size > 10_000) for (const [k, v] of compteurs) if (maintenant - v.debut > 60_000) compteurs.delete(k);
      return false;
    }
    c.n += 1;
    return c.n > debitParMinute;
  }

  async function erp(chemin, options = {}) {
    if (!erpUrl || !cle) return { status: 503, corps: { erreur: "Configurateur indisponible (ERP non configuré)." } };
    try {
      const reponse = await fetchImpl(new URL(`/api/web/v1/${chemin}`, erpUrl), {
        ...options, headers: { "Content-Type": "application/json", "X-Cle-Web": cle, ...(options.headers || {}) }, signal: AbortSignal.timeout(30_000),
      });
      const corps = await reponse.json().catch(() => null);
      // Clé refusée, ERP en panne ou réponse non JSON (hôte refusé par Django, page d'erreur d'un proxy) : indisponible.
      if (corps === null || reponse.status === 403 || reponse.status >= 500) return { status: 503, corps: { erreur: "Configurateur momentanément indisponible." } };
      return { status: reponse.status, corps };
    } catch {
      return { status: 503, corps: { erreur: "Configurateur momentanément indisponible." } };
    }
  }

  function json(res, status, corps) {
    res.writeHead(status, { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" });
    res.end(JSON.stringify(corps));
  }

  async function lireCorps(req) {
    let taille = 0;
    const morceaux = [];
    for await (const m of req) {
      taille += m.length;
      if (taille > TAILLE_MAX) throw new Error("trop gros");
      morceaux.push(m);
    }
    return JSON.parse(Buffer.concat(morceaux).toString("utf-8") || "{}");
  }

  async function fichier(res, chemin) {
    const relatif = normalize(chemin === "/" ? "/index.html" : chemin).replace(/^([/\\])+/, "");
    if (relatif.startsWith("..")) return json(res, 404, { erreur: "Introuvable." });
    try {
      const contenu = await readFile(join(RACINE, relatif));
      res.writeHead(200, {
        "Content-Type": TYPES[extname(relatif)] || "application/octet-stream",
        "Cache-Control": relatif === "index.html" ? "no-cache" : "public, max-age=300",
        "X-Content-Type-Options": "nosniff", "Referrer-Policy": "same-origin",
        "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'",
      });
      res.end(contenu);
    } catch {
      json(res, 404, { erreur: "Introuvable." });
    }
  }

  return createServer(async (req, res) => {
    const { pathname } = new URL(req.url, "http://site");
    try {
      if (pathname === "/api/config" && req.method === "GET") return json(res, 200, { contact });
      if (pathname === "/api/catalogue" && req.method === "GET") {
        if (!catalogue || Date.now() - catalogue.date > CATALOGUE_TTL_MS) {
          const r = await erp("catalogue/");
          if (r.status !== 200) return json(res, r.status, r.corps);
          catalogue = { date: Date.now(), corps: r.corps };
        }
        return json(res, 200, catalogue.corps);
      }
      if ((pathname === "/api/prix" || pathname === "/api/demandes") && req.method === "POST") {
        if (limite(req)) return json(res, 429, { erreur: "Trop de calculs d'affilée : patientez une minute." });
        let corps;
        try {
          corps = await lireCorps(req);
        } catch {
          return json(res, 400, { erreur: "Demande illisible." });
        }
        const r = await erp(pathname === "/api/prix" ? "prix/" : "demandes/", { method: "POST", body: JSON.stringify(corps) });
        return json(res, r.status, r.corps);
      }
      if (pathname.startsWith("/api/")) return json(res, 404, { erreur: "Introuvable." });
      if (req.method !== "GET" && req.method !== "HEAD") return json(res, 405, { erreur: "Méthode non admise." });
      return fichier(res, pathname);
    } catch {
      return json(res, 500, { erreur: "Erreur du site." });
    }
  });
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const port = Number(process.env.PORT || 3000);
  creerServeur({
    erpUrl: process.env.ERP_URL, cle: process.env.ERP_WEB_CLE_API, debitParMinute: Number(process.env.DEBIT_PAR_MINUTE || 60),
    confianceProxy: process.env.CONFIANCE_PROXY === "1", contact: process.env.CONTACT_EMAIL || "",
  }).listen(port, () => console.log(`Site client sur http://localhost:${port} (ERP : ${process.env.ERP_URL || "non configuré"})`));
}
