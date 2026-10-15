// Configurateur DÉCOUPE H2O : le navigateur décrit la pièce, l'ERP calcule tout (prix, faisabilité, flan).
"use strict";

const $ = (id) => document.getElementById(id);
const euros = (v) => v.toLocaleString("fr-FR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const mm = (v) => v.toLocaleString("fr-FR", { maximumFractionDigits: 1 });
const CLE_PANIER = "dh2o-panier";
const MODELES = {
  L: { faces: [40, 60], angles: [90] },
  U: { faces: [30, 80, 30], angles: [90, 90] },
  Z: { faces: [30, 60, 30], angles: [90, -90] },
  omega: { faces: [20, 30, 50, 30, 20], angles: [-90, 90, 90, -90] },
  libre: { faces: [50, 100], angles: [90] },
};

let catalogue = null;
let profil = structuredClone(MODELES.L);
let dernier = null; // { demande, resultat } du dernier prix affiché
let minuterie = null;
let numeroAppel = 0;

// ------------------------------------------------------------------ panier (navigateur seulement ; l'ERP recalcule tout)
function lirePanier() {
  try {
    const p = JSON.parse(localStorage.getItem(CLE_PANIER) || "[]");
    return Array.isArray(p) ? p : [];
  } catch {
    return [];
  }
}
let panier = lirePanier();
function enregistrerPanier() {
  try {
    localStorage.setItem(CLE_PANIER, JSON.stringify(panier));
  } catch { /* navigation privée : le panier vit le temps de la page */ }
}

// ------------------------------------------------------------------ outils DOM
function el(tag, attrs = {}, ...enfants) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "texte") n.textContent = v;
    else if (v !== false && v != null) n.setAttribute(k, v === true ? "" : v);
  }
  for (const e of enfants) n.append(e);
  return n;
}
function remplir(select, options, valeur) {
  select.replaceChildren(...options.map(([v, l]) => el("option", { value: v, texte: l, selected: String(v) === String(valeur) })));
}
const typePiece = () => document.querySelector("input[name=type]:checked").value;

// ------------------------------------------------------------------ chargement
async function api(chemin, options) {
  const r = await fetch(chemin, options);
  const corps = await r.json().catch(() => ({ erreur: "Réponse illisible." }));
  return { ok: r.ok, status: r.status, corps };
}

async function demarrer() {
  api("/api/config").then(({ ok, corps }) => {
    if (ok && corps.contact) $("lien-devis").href = `mailto:${corps.contact}`;
  });
  const { ok, corps } = await api("/api/catalogue");
  if (!ok) {
    $("indispo").textContent = corps.erreur || "Configurateur indisponible.";
    $("indispo").hidden = false;
    return;
  }
  catalogue = corps;
  if (!catalogue.matieres.length) {
    $("indispo").textContent = "Aucune matière n'est encore proposée en ligne.";
    $("indispo").hidden = false;
  }
  const groupes = new Map();
  for (const f of catalogue.formes) {
    if (!groupes.has(f.groupe)) groupes.set(f.groupe, el("optgroup", { label: f.groupe || "Formes" }));
    groupes.get(f.groupe).append(el("option", { value: f.cle, texte: f.libelle }));
  }
  $("forme").replaceChildren(...groupes.values());
  remplir($("matiere"), catalogue.matieres.map((m) => [m.matiere, m.matiere]));
  $("quantite").max = catalogue.quantite_max;
  majEpaisseurs();
  majCotes();
  dessinerProfil();
  afficherPanier();
  calculerBientot();
}

function majEpaisseurs() {
  const m = catalogue.matieres.find((x) => x.matiere === $("matiere").value);
  const avant = $("epaisseur").value;
  remplir($("epaisseur"), (m ? m.epaisseurs : []).map((e) => [e, `${mm(e)} mm`]), avant);
}

function majCotes() {
  const f = catalogue.formes.find((x) => x.cle === $("forme").value);
  $("desc-forme").textContent = f ? f.description : "";
  $("cotes").replaceChildren(...(f ? f.parametres : []).map((p) =>
    el("div", { class: "champ" },
      el("label", { for: `cote-${p.cle}`, texte: p.libelle }),
      el("input", { id: `cote-${p.cle}`, "data-cle": p.cle, type: "number", inputmode: "decimal", value: p.defaut, min: p.minimum, max: p.maximum, step: p.entier ? 1 : "any", title: p.aide || false }),
    )));
}

function dessinerProfil() {
  const lignes = [];
  profil.faces.forEach((f, i) => {
    lignes.push(el("div", { class: "champ" },
      el("label", { for: `face-${i}`, texte: `Face ${String.fromCharCode(65 + i)}` }),
      el("input", { id: `face-${i}`, "data-face": i, type: "number", min: 1, inputmode: "decimal", value: f })));
    if (i < profil.angles.length) {
      const a = profil.angles[i];
      lignes.push(el("div", { class: "pli" },
        el("label", { for: `angle-${i}`, texte: `Pli ${i + 1}` }),
        el("input", { id: `angle-${i}`, "data-angle": i, type: "number", min: 30, max: 179, inputmode: "decimal", value: Math.abs(a), "aria-label": `Angle du pli ${i + 1} en degrés` }),
        el("select", { "data-sens": i, "aria-label": `Sens du pli ${i + 1}` },
          el("option", { value: "1", texte: "vers le haut", selected: a > 0 }), el("option", { value: "-1", texte: "vers le bas", selected: a < 0 }))));
    }
  });
  $("faces").replaceChildren(...lignes);
  $("retrait-face").disabled = profil.faces.length <= 2;
  $("ajout-face").disabled = profil.faces.length >= 8;
}

function lireProfil() {
  document.querySelectorAll("[data-face]").forEach((i) => { profil.faces[i.dataset.face] = Number(i.value); });
  document.querySelectorAll("[data-angle]").forEach((i) => {
    const sens = Number(document.querySelector(`[data-sens="${i.dataset.angle}"]`).value);
    profil.angles[i.dataset.angle] = Number(i.value) * sens;
  });
}

// ------------------------------------------------------------------ demande de prix
function demandeCourante() {
  const base = { matiere: $("matiere").value, epaisseur: Number($("epaisseur").value), quantite: Number($("quantite").value) };
  const type = typePiece();
  if (type === "plane") {
    const cotes = {};
    document.querySelectorAll("#cotes [data-cle]").forEach((i) => { if (i.value !== "") cotes[i.dataset.cle] = Number(i.value); });
    return { ...base, forme: $("forme").value, cotes };
  }
  if (type === "profil") {
    lireProfil();
    return { ...base, pliage: { forme: "profil", faces: [...profil.faces], angles: [...profil.angles], longueur: Number($("longueur-pli").value) } };
  }
  const angle = Number($("angle-bords").value) || 90;
  return {
    ...base, pliage: {
      forme: "boite", longueur: Number($("fond-l").value), largeur: Number($("fond-w").value),
      bords: ["bord-a", "bord-b", "bord-c", "bord-d"].map((id) => Number($(id).value) || 0), angles: [angle, angle, angle, angle],
    },
  };
}

function calculerBientot() {
  clearTimeout(minuterie);
  $("etat").textContent = "Calcul en cours…";
  $("etat").className = "etat";
  minuterie = setTimeout(calculer, 400);
}

async function calculer() {
  if (!catalogue || !$("matiere").value) return;
  const demande = demandeCourante();
  const appel = ++numeroAppel;
  const { ok, corps } = await api("/api/prix", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(demande) });
  if (appel !== numeroAppel) return; // une saisie plus récente est en cours
  if (!ok) return afficherErreur(corps.erreur || "Calcul impossible.");
  dernier = { demande, resultat: corps };
  afficherResultat(corps);
}

function afficherErreur(message) {
  dernier = null;
  $("etat").textContent = message;
  $("etat").className = "etat refus";
  $("prix-total").textContent = "–";
  $("prix-unitaire").textContent = "";
  $("details").replaceChildren();
  $("messages").replaceChildren();
  $("apercu").replaceChildren();
  $("ajouter").disabled = true;
}

const PROCEDES = { laser: "Laser fibre", jet_eau: "Jet d'eau" };
function afficherResultat(r) {
  const etats = { ok: "Prix ferme", validation: "Prix indicatif : votre pièce sera vérifiée par notre atelier", refus: "Pas de prix en ligne pour cette pièce" };
  $("etat").textContent = etats[r.statut];
  $("etat").className = `etat ${r.statut}`;
  const avecPrix = r.prix_total_ht != null;
  $("prix-total").textContent = avecPrix ? euros(r.prix_total_ht) : "–";
  $("prix-unitaire").textContent = avecPrix ? `soit ${euros(r.prix_unitaire_ht)} € HT la pièce` : "";
  const details = [["Pièce", r.designation]];
  if (r.procede) details.push(["Découpe", PROCEDES[r.procede] || r.procede]);
  if (r.largeur_mm) details.push([r.pliage ? "Flan à plat" : "Encombrement", `${mm(r.largeur_mm)} × ${mm(r.hauteur_mm)} mm`]);
  if (r.poids_kg) details.push(["Poids unitaire", `${r.poids_kg.toLocaleString("fr-FR", { maximumFractionDigits: 2 })} kg`]);
  if (r.pliage && r.pliage.angle_mini) details.push(["Angle de pli mini", `${r.pliage.angle_mini}°`]);
  $("details").replaceChildren(...details.flatMap(([t, v]) => [el("dt", { texte: t }), el("dd", { texte: v })]));
  $("messages").replaceChildren(...[...r.refus, ...r.validations].map((m) => el("li", { texte: m })));
  dessinerApercu(r.contour);
  $("ajouter").disabled = !avecPrix;
}

function dessinerApercu(contour) {
  const svg = $("apercu");
  if (!contour || !contour.exterieur || !contour.exterieur.length) return svg.replaceChildren();
  const xs = contour.exterieur.map((p) => p[0]), ys = contour.exterieur.map((p) => p[1]);
  const minx = Math.min(...xs), maxx = Math.max(...xs), miny = Math.min(...ys), maxy = Math.max(...ys);
  const marge = Math.max(maxx - minx, maxy - miny) * 0.04;
  svg.setAttribute("viewBox", `${minx - marge} ${-maxy - marge} ${maxx - minx + 2 * marge} ${maxy - miny + 2 * marge}`);
  const d = [contour.exterieur, ...(contour.trous || [])].map((a) => "M" + a.map((p) => `${p[0]},${-p[1]}`).join("L") + "Z").join(" ");
  const chemin = document.createElementNS("http://www.w3.org/2000/svg", "path");
  chemin.setAttribute("d", d);
  chemin.setAttribute("fill-rule", "evenodd");
  chemin.setAttribute("vector-effect", "non-scaling-stroke");
  svg.replaceChildren(chemin);
}

// ------------------------------------------------------------------ panier
function afficherPanier() {
  $("nb-panier").textContent = panier.length;
  const vide = panier.length === 0;
  $("panier-vide").hidden = !vide;
  $("table-panier").hidden = vide;
  $("form-demande").hidden = vide;
  if (vide) return;
  const corps = $("table-panier").querySelector("tbody");
  corps.replaceChildren(...panier.map((l, i) => el("tr", {},
    el("td", { texte: l.designation }), el("td", { texte: `${l.demande.matiere} ${mm(l.demande.epaisseur)} mm` }),
    el("td", { class: "nombre", texte: l.demande.quantite }), el("td", { class: "nombre", texte: `${euros(l.prix_total_ht)} €` }),
    el("td", {}, el("button", { type: "button", class: "retirer", "data-index": i, "aria-label": `Retirer ${l.designation}`, texte: "×" })))));
  const total = panier.reduce((s, l) => s + l.prix_total_ht, 0);
  const minimum = catalogue ? catalogue.minimum_commande_ht : 0;
  $("total-pieces").textContent = `${euros(total)} €`;
  $("ligne-minimum").hidden = total >= minimum;
  $("complement").textContent = `${euros(Math.max(minimum - total, 0))} €`;
  $("total-panier").textContent = `${euros(Math.max(total, minimum))} €`;
}

$("ajouter").addEventListener("click", () => {
  if (!dernier || dernier.resultat.prix_total_ht == null) return;
  const r = dernier.resultat;
  panier.push({ demande: dernier.demande, designation: r.designation, prix_total_ht: r.prix_total_ht, statut: r.statut });
  enregistrerPanier();
  afficherPanier();
  $("ajouter").textContent = "Ajouté ✓";
  setTimeout(() => { $("ajouter").textContent = "Ajouter au panier"; }, 1500);
});

$("table-panier").addEventListener("click", (e) => {
  const b = e.target.closest("button.retirer");
  if (!b) return;
  panier.splice(Number(b.dataset.index), 1);
  enregistrerPanier();
  afficherPanier();
});

$("form-demande").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.currentTarget, retour = $("retour-demande");
  if (!form.reportValidity()) return;
  const client = Object.fromEntries(new FormData(form));
  const bouton = form.querySelector("button[type=submit]");
  bouton.disabled = true;
  retour.className = "retour";
  retour.textContent = "Envoi…";
  const { ok, corps } = await api("/api/demandes", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ client, lignes: panier.map((l) => l.demande) }),
  });
  bouton.disabled = false;
  if (!ok) {
    retour.className = "retour erreur";
    retour.textContent = corps.erreur || "Envoi impossible, réessayez.";
    return;
  }
  panier = [];
  enregistrerPanier();
  afficherPanier();
  $("form-demande").hidden = false;
  form.reset();
  retour.className = "retour succes";
  retour.textContent = `Merci ! Demande ${corps.numero} reçue (${euros(corps.total_ht)} € HT hors port). Nous vous confirmons la commande, le port et le délai par e-mail.`;
});

// ------------------------------------------------------------------ saisie
document.querySelectorAll("input[name=type]").forEach((r) => r.addEventListener("change", () => {
  const type = typePiece();
  $("bloc-plane").hidden = type !== "plane";
  $("bloc-profil").hidden = type !== "profil";
  $("bloc-boite").hidden = type !== "boite";
  calculerBientot();
}));
$("forme").addEventListener("change", () => { majCotes(); calculerBientot(); });
$("matiere").addEventListener("change", () => { majEpaisseurs(); calculerBientot(); });
$("modele-profil").addEventListener("change", (e) => { profil = structuredClone(MODELES[e.target.value]); dessinerProfil(); calculerBientot(); });
$("ajout-face").addEventListener("click", () => { lireProfil(); profil.faces.push(30); profil.angles.push(90); dessinerProfil(); calculerBientot(); });
$("retrait-face").addEventListener("click", () => { lireProfil(); profil.faces.pop(); profil.angles.pop(); dessinerProfil(); calculerBientot(); });
document.querySelector(".configurateur").addEventListener("input", calculerBientot);

demarrer();
