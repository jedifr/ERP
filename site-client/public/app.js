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
let vue3d = null; // chargée au premier clic sur « Vue 3D »
let mode3d = false;
// Forme libre : recette envoyée telle quelle à l'ERP, élément choisi, historique pour annuler / rétablir.
let libre = { base: { forme: "rectangle", largeur: 300, hauteur: 200, rayon_angle: 0 }, elements: [] };
let choisi = -1;
const historique = { etats: [JSON.stringify(libre)], position: 0 };
let ficheDxf = null; // { jeton, nom, contour, largeur_mm, hauteur_mm, avertissements } rendue par l'ERP
const DXF_MAX = 1536 * 1024;
let formatsFichier = ["dxf"]; // formats que l'ERP sait lire (catalogue)
let echelleDxf = 1; // agrandissement demandé par le client (1 = taille du fichier)

// Contour du fichier à l'échelle choisie (affichage seulement : l'ERP refait le calcul).
function contourDxf() {
  if (!ficheDxf) return null;
  const k = echelleDxf, c = ficheDxf.contour;
  return k === 1 ? c : { exterieur: c.exterieur.map(([x, y]) => [x * k, y * k]), trous: (c.trous || []).map((t) => t.map(([x, y]) => [x * k, y * k])) };
}

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
  let r;
  try {
    r = await fetch(chemin, options);
  } catch {
    return { ok: false, status: 0, corps: { erreur: "Connexion impossible : rechargez la page (F5)." } };
  }
  const texte = await r.text();
  try {
    return { ok: r.ok, status: r.status, corps: JSON.parse(texte) };
  } catch {
    // Page HTML à la place de la réponse : en général le contrôle anti-robots de l'hébergeur (Tiger Protect chez o2switch).
    const controle = /security-challenge|o2s-browser-check|Security check/i.test(texte);
    return { ok: false, status: r.status, corps: { erreur: controle
      ? "Le contrôle de sécurité de l'hébergeur a bloqué le calcul : rechargez la page (F5). Si cela continue, demandez un devis par e-mail."
      : "Réponse illisible : rechargez la page (F5)." } };
  }
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
  majFormatsFichier(catalogue.formats_fichier);
  majEpaisseurs();
  majCotes();
  dessinerProfil();
  afficherBase();
  afficherElements();
  majHistorique();
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
  const lettres = f ? Schemas.lettres(f.parametres) : {};
  $("cotes").replaceChildren(...(f ? f.parametres : []).map((p) =>
    el("div", { class: "champ" },
      el("label", { for: `cote-${p.cle}` }, ...(lettres[p.cle] ? [el("span", { class: "lettre", texte: lettres[p.cle] }), " "] : []), p.libelle),
      el("input", { id: `cote-${p.cle}`, "data-cle": p.cle, type: "number", inputmode: "decimal", value: p.defaut, min: p.minimum, max: p.maximum, step: p.entier ? 1 : "any", title: p.aide || false }),
    )));
}

function dessinerProfil() {
  const lignes = [];
  profil.faces.forEach((f, i) => {
    lignes.push(el("div", { class: "champ" },
      el("label", { for: `face-${i}` }, el("span", { class: "lettre", texte: String.fromCharCode(65 + i) }), " Face"),
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

// ------------------------------------------------------------------ schéma coté (immédiat, sans attendre le prix)
function dessinerSchema() {
  if (!catalogue) return;
  const d = demandeCourante();
  const svg = $("schema");
  if (svg.clientWidth) Schemas.reglerTexte(Math.min(1, Math.max(0.4, 440 / svg.clientWidth)));
  if (d.libre) {
    $("note-schema").textContent = mode3d ? "Faites glisser pour tourner la pièce, molette ou pincement pour zoomer. Vue indicative."
      : "Faites glisser un élément pour le placer (Maj : au dixième de mm). Trait plein : matière ajoutée ; pointillés : découpe.";
    EditeurLibre.dessiner(svg, libre, choisi);
    return majVue3d();
  }
  let dessin = null;
  if (d.dxf !== undefined) dessin = ficheDxf ? Schemas.contour(contourDxf(), ficheDxf.nom, true) : null;
  else if (!d.pliage) dessin = Schemas.plane(catalogue.formes.find((x) => x.cle === d.forme), d.cotes);
  else if (d.pliage.forme === "profil") dessin = Schemas.profil(d.pliage.faces, d.pliage.angles, d.pliage.longueur);
  else dessin = Schemas.boite(d.pliage.longueur, d.pliage.largeur, d.pliage.bords);
  $("note-schema").textContent = mode3d ? "Faites glisser pour tourner la pièce, molette ou pincement pour zoomer. Vue indicative."
    : d.dxf !== undefined ? (ficheDxf ? (echelleDxf === 1 ? "Pièce telle que lue dans votre fichier" : `Pièce à ${+(echelleDxf * 100).toFixed(2)} % de votre fichier`) + " ; A et B : cotes hors tout." : "Le dessin de la pièce s'affiche une fois le fichier lu.")
    : (dessin && dessin.note) || "Les lettres du schéma repèrent les cotes à saisir.";
  if (!dessin) return svg.replaceChildren();
  svg.setAttribute("viewBox", dessin.viewBox);
  svg.innerHTML = dessin.contenu;
  majVue3d();
}

// ------------------------------------------------------------------ vue 3D
function modele3d() {
  const d = demandeCourante();
  const m = catalogue.matieres.find((x) => x.matiere === d.matiere);
  const commun = { epaisseur: d.epaisseur, famille: m ? m.famille : "" };
  if (d.dxf !== undefined) return ficheDxf ? { type: "plaque", contour: contourDxf(), ...commun } : null;
  if (d.libre) {
    // dernier contour reconstruit par l'ERP (il suit la saisie à chaque calcul)
    return dernier && dernier.demande.libre ? { type: "plaque", contour: dernier.resultat.contour, ...commun } : null;
  }
  if (!d.pliage) {
    // contour de référence : celui que l'ERP vient de chiffrer
    const contour = dernier && !dernier.demande.pliage && dernier.demande.forme === d.forme ? dernier.resultat.contour : null;
    return contour ? { type: "plaque", contour, ...commun } : null;
  }
  if (d.pliage.forme === "profil") return { type: "profil", ...d.pliage, ...commun };
  return { type: "boite", ...d.pliage, angle: d.pliage.angles[0], ...commun };
}

function majVue3d() {
  if (mode3d && vue3d && catalogue) vue3d.afficher(modele3d());
}

async function basculerVue(en3d) {
  mode3d = en3d;
  $("voir-2d").classList.toggle("actif", !en3d);
  $("voir-3d").classList.toggle("actif", en3d);
  $("voir-2d").setAttribute("aria-pressed", String(!en3d));
  $("voir-3d").setAttribute("aria-pressed", String(en3d));
  $("schema").toggleAttribute("hidden", en3d); // svg : pas de propriété hidden
  $("vue-3d").hidden = !en3d;
  if (en3d && !vue3d) {
    $("vue-3d").textContent = "Chargement de la vue 3D…";
    try {
      const { creerVue } = await import("./vue3d.js");
      vue3d = creerVue($("vue-3d"));
    } catch {
      $("vue-3d").textContent = "La vue 3D n'a pas pu se charger.";
      return;
    }
  }
  dessinerSchema();
}
$("voir-2d").addEventListener("click", () => basculerVue(false));
// Le schéma change de largeur (fenêtre redimensionnée, plein écran) : on le redessine pour garder des cotes lisibles.
let largeurSchema = 0;
new ResizeObserver(() => {
  const l = $("schema").clientWidth;
  if (!l || Math.abs(l - largeurSchema) < 20) return;
  largeurSchema = l;
  requestAnimationFrame(dessinerSchema);
}).observe($("schema"));
$("voir-3d").addEventListener("click", () => basculerVue(true));

// ------------------------------------------------------------------ demande de prix
function demandeCourante() {
  const base = { matiere: $("matiere").value, epaisseur: Number($("epaisseur").value), quantite: Number($("quantite").value) };
  const type = typePiece();
  if (type === "plane") {
    const cotes = {};
    document.querySelectorAll("#cotes [data-cle]").forEach((i) => { if (i.value !== "") cotes[i.dataset.cle] = Number(i.value); });
    return { ...base, forme: $("forme").value, cotes };
  }
  if (type === "libre") return { ...base, libre: structuredClone(libre) };
  if (type === "dxf") return { ...base, dxf: ficheDxf ? ficheDxf.jeton : null, ...(echelleDxf !== 1 ? { echelle: echelleDxf } : {}) };
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
  dessinerSchema();
  clearTimeout(minuterie);
  $("etat").textContent = "Calcul en cours…";
  $("etat").className = "etat";
  minuterie = setTimeout(calculer, 400);
}

async function calculer() {
  if (!catalogue || !$("matiere").value) return;
  const demande = demandeCourante();
  if (demande.dxf === null) {
    afficherErreur("Choisissez votre fichier.");
    $("etat").className = "etat";
    return;
  }
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
  majVue3d();
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

// ------------------------------------------------------------------ forme libre
const nombre = (v) => (v === "" || v == null ? "" : Number(v));

function retenir() {
  const etat = JSON.stringify(libre);
  if (etat === historique.etats[historique.position]) return majHistorique();
  historique.etats.splice(historique.position + 1, Infinity, etat);
  if (historique.etats.length > 100) historique.etats.shift();
  historique.position = historique.etats.length - 1;
  majHistorique();
}
function majHistorique() {
  $("annuler").disabled = historique.position <= 0;
  $("refaire").disabled = historique.position >= historique.etats.length - 1;
}
function revenir(pas) {
  const p = historique.position + pas;
  if (p < 0 || p >= historique.etats.length) return;
  historique.position = p;
  libre = JSON.parse(historique.etats[p]);
  if (choisi >= libre.elements.length) choisi = libre.elements.length - 1;
  afficherBase();
  afficherElements();
  majHistorique();
  calculerBientot();
}

function afficherBase() {
  const b = libre.base;
  $("libre-forme").value = b.forme;
  document.querySelectorAll("#bloc-libre [data-base]").forEach((c) => { c.hidden = c.dataset.base !== b.forme; });
  document.querySelectorAll("#bloc-libre [data-base-cle]").forEach((i) => {
    if (b[i.dataset.baseCle] !== undefined) i.value = b[i.dataset.baseCle];
  });
}

function lireBase() {
  const forme = $("libre-forme").value, v = (id) => nombre($(id).value);
  libre.base = forme === "disque" ? { forme, diametre: v("libre-diametre") }
    : { forme, largeur: v("libre-largeur"), hauteur: v("libre-hauteur"), rayon_angle: v("libre-rayon") || 0 };
}

function afficherElements() {
  const { TYPES } = EditeurLibre;
  $("elements").replaceChildren(...libre.elements.map((e, i) => {
    const champ = (cle, libelle, attrs = {}) => el("div", { class: "champ" },
      el("label", { for: `el-${i}-${cle}`, texte: libelle }),
      el("input", { id: `el-${i}-${cle}`, "data-el": i, "data-champ": cle, type: "number", inputmode: "decimal", step: "any", value: e[cle], ...attrs }));
    return el("li", { class: `element-ligne${i === choisi ? " choisi" : ""}`, "data-ligne": i },
      el("div", { class: "element-tete" },
        el("span", { class: "lettre", texte: String(i + 1) }),
        el("strong", { texte: TYPES[e.type].libelle }),
        el("select", { "data-el": i, "data-champ": "mode", "aria-label": `Élément ${i + 1} : retirer ou ajouter` },
          el("option", { value: "retirer", texte: "Découpe", selected: e.mode !== "ajouter" }),
          el("option", { value: "ajouter", texte: "Ajout de matière", selected: e.mode === "ajouter" })),
        el("button", { type: "button", class: "dupliquer", "data-dupliquer": i, title: "Dupliquer", "aria-label": `Dupliquer l'élément ${i + 1}`, texte: "⧉" }),
        el("button", { type: "button", class: "retirer", "data-supprimer": i, title: "Supprimer", "aria-label": `Supprimer l'élément ${i + 1}`, texte: "×" })),
      el("div", { class: "element-champs" },
        champ("x", "X centre"), champ("y", "Y centre"),
        ...TYPES[e.type].champs.map(([cle, libelle]) => champ(cle, libelle, cle === "nb_cotes" ? { min: 3, max: 64, step: 1 } : cle === "angle" ? {} : { min: 0 }))));
  }));
  if (!libre.elements.length) $("elements").append(el("li", { class: "aide vide", texte: "Aucun élément : la plaque est découpée telle quelle." }));
}

function choisir(i) {
  if (choisi === i) return;
  choisi = i;
  document.querySelectorAll("#elements [data-ligne]").forEach((l) => l.classList.toggle("choisi", Number(l.dataset.ligne) === i));
  dessinerSchema();
}

$("libre-forme").addEventListener("change", () => { lireBase(); afficherBase(); retenir(); calculerBientot(); });
$("bloc-libre").addEventListener("input", (ev) => {
  const t = ev.target;
  if (t.dataset.baseCle) lireBase();
  else if (t.dataset.el !== undefined) {
    const e = libre.elements[t.dataset.el];
    e[t.dataset.champ] = t.dataset.champ === "mode" ? t.value : nombre(t.value);
    choisir(Number(t.dataset.el));
  }
});
$("bloc-libre").addEventListener("change", (ev) => { if (ev.target.dataset.baseCle || ev.target.dataset.el !== undefined) retenir(); });
$("bloc-libre").addEventListener("focusin", (ev) => { if (ev.target.dataset.el !== undefined) choisir(Number(ev.target.dataset.el)); });
$("bloc-libre").addEventListener("click", (ev) => {
  const b = ev.target.closest("button");
  if (!b) return;
  if (b.dataset.ajout) {
    if (libre.elements.length >= 200) return;
    libre.elements.push(EditeurLibre.nouvelElement(b.dataset.ajout, libre));
    choisi = libre.elements.length - 1;
  } else if (b.dataset.supprimer !== undefined) {
    libre.elements.splice(Number(b.dataset.supprimer), 1);
    choisi = Math.min(choisi, libre.elements.length - 1);
  } else if (b.dataset.dupliquer !== undefined) {
    const copie = structuredClone(libre.elements[b.dataset.dupliquer]);
    const pas = Math.max(5, Math.round(Math.min(...EditeurLibre.dimensions(libre.base)) / 20));
    copie.x = +(Number(copie.x) + pas).toFixed(1);
    copie.y = +(Number(copie.y) - pas).toFixed(1);
    libre.elements.push(copie);
    choisi = libre.elements.length - 1;
  } else if (b.id === "annuler") return revenir(-1);
  else if (b.id === "refaire") return revenir(1);
  else return;
  afficherElements();
  retenir();
  calculerBientot();
});
document.addEventListener("keydown", (ev) => {
  if (typePiece() !== "libre" || !(ev.ctrlKey || ev.metaKey) || ev.target.matches("input, textarea")) return;
  const touche = ev.key.toLowerCase();
  if (touche === "z" && !ev.shiftKey) revenir(-1);
  else if (touche === "y" || (touche === "z" && ev.shiftKey)) revenir(1);
  else return;
  ev.preventDefault();
});

EditeurLibre.brancher($("schema"), {
  actif: () => typePiece() === "libre" && !mode3d,
  element: (i) => libre.elements[i],
  choisir,
  deplacer(i, x, y) {
    // centre gardé dans l'encombrement de la plaque : un élément peut déborder (encoche), pas s'en détacher
    const [W, H] = EditeurLibre.dimensions(libre.base);
    x = Math.min(Math.max(x, 0), W);
    y = Math.min(Math.max(y, 0), H);
    const e = libre.elements[i];
    e.x = x;
    e.y = y;
    const ix = $(`el-${i}-x`), iy = $(`el-${i}-y`);
    if (ix) ix.value = x;
    if (iy) iy.value = y;
    calculerBientot();
  },
  fin: () => retenir(),
});

// ------------------------------------------------------------------ fichier DXF : lu par l'ERP, qui garde l'original
function lireEnBase64(fichier) {
  return new Promise((ok, echec) => {
    const r = new FileReader();
    r.onload = () => ok(String(r.result).split(",", 2)[1] || "");
    r.onerror = () => echec(r.error);
    r.readAsDataURL(fichier);
  });
}

function majFormatsFichier(formats) {
  if (Array.isArray(formats) && formats.length) formatsFichier = formats;
  const liste = formatsFichier.map((f) => f.toUpperCase());
  const texte = liste.length > 1 ? `${liste.slice(0, -1).join(", ")} ou ${liste[liste.length - 1]}` : liste[0];
  const images = vectorisable();
  $("onglet-fichier").textContent = `Fichier ${liste.join(", ")}` + (images ? " ou image" : "");
  $("titre-depot").textContent = `Choisissez un fichier ${texte}` + (images ? ", ou un dessin (PNG, JPG)" : "");
  $("fichier-dxf").accept = [...formatsFichier, ...(images ? IMAGES : [])].map((f) => `.${f}`).join(",");
  $("aide-depot").textContent = "Une pièce par fichier, cotes en mm, 1,5 Mo au plus."
    + (formatsFichier.includes("svg") ? " SVG : convertissez les textes en tracés." : "")
    + (images ? " Un dessin ou une photo (fond clair, dessin foncé) est vectorisé ici." : "")
    + (formatsFichier.includes("dwg") ? "" : " Le DWG n'est pas lu en ligne : exportez-le en DXF.");
}

async function envoyerDxf(fichier, vectorise = null) {
  if (!fichier) return false;
  const etat = $("etat-dxf");
  etat.className = "aide";
  const extension = (fichier.name.match(/\.([a-z0-9]+)$/i) || [])[1];
  if (!vectorise && extension && vectorisable() && IMAGES.includes(extension.toLowerCase())) return ouvrirVecto(fichier);
  if (!vectorise) fermerVecto();
  if (!extension || !formatsFichier.includes(extension.toLowerCase())) {
    etat.className = "aide erreur";
    etat.textContent = `Choisissez un fichier ${formatsFichier.map((f) => `.${f}`).join(", ")} (exporté depuis votre logiciel de dessin).`;
    return false;
  }
  if (fichier.size > DXF_MAX) {
    etat.className = "aide erreur";
    etat.textContent = "Fichier trop gros (1,5 Mo au plus) : envoyez-le avec une demande de devis.";
    return false;
  }
  etat.textContent = vectorise ? "Envoi du contour…" : `Lecture de ${fichier.name}…`;
  let contenu;
  try {
    contenu = await lireEnBase64(fichier);
  } catch {
    etat.className = "aide erreur";
    etat.textContent = "Le fichier n'a pas pu être lu.";
    return false;
  }
  const { ok, corps } = await api("/api/dxf", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ nom: fichier.name, contenu }) });
  if (vectorise && vectorise.n !== vecto.envoi) return false; // un contour plus récent est parti entre-temps
  if (!ok) {
    etat.className = "aide erreur";
    etat.textContent = corps.erreur || "Fichier refusé.";
    return false;
  }
  ficheDxf = vectorise ? { ...corps, vectorise } : corps;
  echelleDxf = 1;
  etat.textContent = "";
  afficherFicheDxf();
  calculerBientot();
  return true;
}

function majCotesDxf(sauf) {
  if (!ficheDxf) return;
  const arrondi = (v) => String(+v.toFixed(2));
  if (sauf !== "largeur") $("dxf-largeur").value = arrondi(ficheDxf.largeur_mm * echelleDxf);
  if (sauf !== "hauteur") $("dxf-hauteur").value = arrondi(ficheDxf.hauteur_mm * echelleDxf);
  if (sauf !== "pourcent") $("dxf-echelle").value = arrondi(echelleDxf * 100);
}

function afficherFicheDxf() {
  const f = ficheDxf, infos = $("infos-dxf");
  infos.hidden = !f;
  $("echelle-dxf").hidden = !f || Boolean(f.vectorise); // dessin vectorisé : la taille se règle dans le panneau du dessin
  majCotesDxf();
  if (!f) return infos.replaceChildren();
  const lignes = [["Fichier", f.nom], [f.vectorise ? "Taille de la pièce" : "Taille du fichier", `${mm(f.largeur_mm)} × ${mm(f.hauteur_mm)} mm`], ["Découpes intérieures", String((f.contour.trous || []).length)]];
  if (f.avertissements && f.avertissements.length) lignes.push(["À vérifier", f.avertissements.join(" ")]);
  infos.replaceChildren(...lignes.flatMap(([t, v]) => [el("dt", { texte: t }), el("dd", { texte: v })]));
}

$("echelle-dxf").addEventListener("input", (ev) => {
  const champ = ev.target.dataset.echelle, v = Number(ev.target.value);
  if (!ficheDxf || !champ || !(v > 0)) return;
  const k = champ === "largeur" ? v / ficheDxf.largeur_mm : champ === "hauteur" ? v / ficheDxf.hauteur_mm : v / 100;
  echelleDxf = Math.min(Math.max(+k.toFixed(4), 0.01), 100);
  majCotesDxf(champ);
});
$("dxf-taille-fichier").addEventListener("click", () => {
  echelleDxf = 1;
  majCotesDxf();
  calculerBientot();
});
$("fichier-dxf").addEventListener("change", (ev) => { envoyerDxf(ev.target.files[0]); ev.target.value = ""; });
const depot = $("depot-dxf");
depot.addEventListener("dragover", (ev) => { ev.preventDefault(); depot.classList.add("survol"); });
depot.addEventListener("dragleave", () => depot.classList.remove("survol"));
depot.addEventListener("drop", (ev) => {
  ev.preventDefault();
  depot.classList.remove("survol");
  envoyerDxf(ev.dataTransfer.files[0]);
});

// ------------------------------------------------------------------ dessin vectorisé dans le navigateur (public/vectoriseur.js)
// Le contour obtenu part à l'ERP en SVG (cotes en mm) comme un fichier du client : l'ERP le contrôle, le garde et le chiffre.
const IMAGES = ["png", "jpg", "jpeg", "webp", "gif", "bmp"];
const IMAGE_MAX = 25 * 1024 * 1024;
const vecto = { image: null, url: "", nom: "", ratio: 1, fraction: 1, gris: null, cle: "", resultat: null, seuilAuto: true, minuterie: 0, envoi: 0 };
const vectorisable = () => formatsFichier.includes("svg") && typeof Vectoriseur !== "undefined";

async function ouvrirVecto(fichier) {
  const etat = $("etat-dxf");
  if (fichier.size > IMAGE_MAX) {
    etat.className = "aide erreur";
    etat.textContent = "Image trop lourde (25 Mo au plus).";
    return false;
  }
  etat.textContent = `Lecture de ${fichier.name}…`;
  let image;
  try {
    image = await createImageBitmap(fichier);
  } catch {
    etat.className = "aide erreur";
    etat.textContent = "Image illisible : enregistrez-la en PNG ou JPG.";
    return false;
  }
  fermerVecto();
  etat.textContent = "";
  // aperçu en data: (la politique de sécurité du site refuse les blob:), réduit pour rester léger
  const k = Math.min(1, 800 / Math.max(image.width, image.height)), toile = document.createElement("canvas");
  toile.width = Math.max(1, Math.round(image.width * k));
  toile.height = Math.max(1, Math.round(image.height * k));
  const ctx = toile.getContext("2d");
  ctx.fillStyle = "#fff";
  ctx.fillRect(0, 0, toile.width, toile.height);
  ctx.drawImage(image, 0, 0, toile.width, toile.height);
  Object.assign(vecto, { image, url: toile.toDataURL("image/jpeg", 0.8), nom: fichier.name, ratio: image.height / image.width, fraction: 1, gris: null, cle: "", seuilAuto: true });
  ficheDxf = null;
  afficherFicheDxf();
  $("vecto-nom").textContent = fichier.name;
  $("vecto-inverser").checked = false;
  $("vecto-grossir").value = "0";
  $("vecto-lissage").value = "1";
  $("bloc-vecto").hidden = false;
  tracerVecto(true);
  return true;
}

function fermerVecto() {
  if (vecto.image) vecto.image.close();
  Object.assign(vecto, { image: null, url: "", gris: null, resultat: null });
  clearTimeout(vecto.minuterie);
  $("bloc-vecto").hidden = true;
}

// Niveaux de gris de l'image à la taille de travail (le transparent compte comme du blanc).
function grisVecto(w, h) {
  const cle = `${w}x${h}`;
  if (vecto.cle === cle) return vecto.gris;
  const toile = document.createElement("canvas");
  toile.width = w;
  toile.height = h;
  const ctx = toile.getContext("2d", { willReadFrequently: true });
  ctx.fillStyle = "#fff";
  ctx.fillRect(0, 0, w, h);
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(vecto.image, 0, 0, w, h);
  const px = ctx.getImageData(0, 0, w, h).data, gris = new Uint8Array(w * h);
  for (let i = 0; i < gris.length; i++) gris[i] = (px[4 * i] * 77 + px[4 * i + 1] * 150 + px[4 * i + 2] * 29) >> 8;
  Object.assign(vecto, { cle, gris });
  return gris;
}

// Vectorise avec les réglages du panneau ; `envoyer` : le contour part à l'ERP (après une pause de saisie).
function tracerVecto(envoyer, reprise = false) {
  if (!vecto.image) return;
  const voulue = Math.min(Math.max(Number($("vecto-largeur").value) || 200, 5), 3000);
  // la largeur saisie est celle de la pièce : l'image est mise à l'échelle d'après la part qu'occupe la pièce
  const largeurImage = voulue / vecto.fraction, hauteurImage = largeurImage * vecto.ratio;
  const pxParMm = Vectoriseur.resolution(largeurImage, hauteurImage);
  const w = Math.max(2, Math.round(largeurImage * pxParMm)), h = Math.max(2, Math.round(hauteurImage * pxParMm));
  const epaisseur = Number($("epaisseur").value) || 0;
  const r = Vectoriseur.vectoriser({ gris: grisVecto(w, h), w, h }, pxParMm, {
    seuil: vecto.seuilAuto ? undefined : Number($("vecto-seuil").value), inverser: $("vecto-inverser").checked,
    lissage: Number($("vecto-lissage").value), grossirMm: Number($("vecto-grossir").value) || 0, epaisseurMm: epaisseur,
  });
  if (vecto.seuilAuto) $("vecto-seuil").value = String(Math.round(r.seuil));
  if (!r.vide && r.largeurMm > 0) vecto.fraction = Math.min(Math.max(r.largeurMm / largeurImage, 0.01), 1);
  // la pièce n'occupe pas toute l'image : on refait le contour une fois à la bonne échelle
  if (!r.vide && !reprise && Math.abs(r.largeurMm - voulue) > voulue * 0.005) return tracerVecto(envoyer, true);
  vecto.resultat = r;
  afficherVecto(r, largeurImage, hauteurImage, epaisseur);
  clearTimeout(vecto.minuterie);
  if (r.vide && ficheDxf) {
    ficheDxf = null;
    afficherFicheDxf();
    calculerBientot();
  }
  if (envoyer && !r.vide) vecto.minuterie = setTimeout(envoyerVecto, 700);
}

function afficherVecto(r, L, H, epaisseur) {
  const svg = $("vecto-apercu"), NS = "http://www.w3.org/2000/svg";
  svg.setAttribute("viewBox", `0 0 ${L} ${H}`);
  const image = document.createElementNS(NS, "image");
  image.setAttribute("href", vecto.url);
  image.setAttribute("width", L);
  image.setAttribute("height", H);
  image.setAttribute("preserveAspectRatio", "none");
  const chemin = document.createElementNS(NS, "path");
  chemin.setAttribute("class", "piece");
  chemin.setAttribute("fill-rule", "evenodd");
  chemin.setAttribute("d", r.contours.map((c) => "M" + c.points.map(([x, y]) => `${x.toFixed(2)} ${y.toFixed(2)}`).join("L") + "Z").join(""));
  svg.replaceChildren(image, chemin);
  const messages = [];
  let info = "";
  if (r.vide) messages.push("Rien à découper : changez le contraste ou cochez « Inverser ».");
  else {
    const trous = r.contours.filter((c) => c.trou).length;
    info = `Pièce de ${mm(r.largeurMm)} × ${mm(r.hauteurMm)} mm, ${trous} découpe${trous > 1 ? "s" : ""} intérieure${trous > 1 ? "s" : ""}.`;
    if (r.ecartes) messages.push(`${r.ecartes} morceau${r.ecartes > 1 ? "x" : ""} détaché${r.ecartes > 1 ? "s" : ""} du reste retiré${r.ecartes > 1 ? "s" : ""} (il${r.ecartes > 1 ? "s" : ""} tomberai${r.ecartes > 1 ? "en" : ""}t à la découpe) : reliez-le${r.ecartes > 1 ? "s" : ""} à la pièce dans votre dessin, ou grossissez le trait.`);
    if (r.combles >= Math.max(1, epaisseur * epaisseur)) messages.push(`Jours plus fins que la tôle (${mm(epaisseur)} mm) comblés.`);
    if (r.retires >= Math.max(1, epaisseur * epaisseur)) messages.push(`Traits plus fins que la tôle (${mm(epaisseur)} mm) retirés : grossissez le trait pour les garder.`);
  }
  $("vecto-info").textContent = info;
  $("vecto-messages").replaceChildren(...messages.map((m) => el("li", { texte: m })));
}

async function envoyerVecto() {
  const r = vecto.resultat;
  if (!r || r.vide) return;
  const n = ++vecto.envoi;
  const base = vecto.nom.replace(/\.[^.]+$/, "") || "dessin";
  const fichier = new File([Vectoriseur.enSvg(r)], `${base}-vectorise.svg`, { type: "image/svg+xml" });
  await envoyerDxf(fichier, { n, epaisseur: Number($("epaisseur").value) || 0 });
}

$("bloc-vecto").addEventListener("input", (ev) => {
  if (ev.target.id === "vecto-seuil") vecto.seuilAuto = false;
  clearTimeout(vecto.minuterie);
  vecto.minuterie = setTimeout(() => tracerVecto(true), ev.target.type === "range" ? 120 : 400);
});
// la règle « ni trait ni jour plus fin que la tôle » dépend de l'épaisseur : on refait le contour
$("epaisseur").addEventListener("change", () => { if (vecto.image) tracerVecto(true); });
$("matiere").addEventListener("change", () => setTimeout(() => { if (vecto.image) tracerVecto(true); })); // après la liste des épaisseurs

// ------------------------------------------------------------------ saisie
document.querySelectorAll("input[name=type]").forEach((r) => r.addEventListener("change", () => {
  const type = typePiece();
  $("bloc-plane").hidden = type !== "plane";
  $("bloc-profil").hidden = type !== "profil";
  $("bloc-boite").hidden = type !== "boite";
  $("bloc-libre").hidden = type !== "libre";
  $("bloc-dxf").hidden = type !== "dxf";
  calculerBientot();
}));
$("forme").addEventListener("change", () => { majCotes(); calculerBientot(); });
$("matiere").addEventListener("change", () => { majEpaisseurs(); calculerBientot(); });
$("modele-profil").addEventListener("change", (e) => { profil = structuredClone(MODELES[e.target.value]); dessinerProfil(); calculerBientot(); });
$("ajout-face").addEventListener("click", () => { lireProfil(); profil.faces.push(30); profil.angles.push(90); dessinerProfil(); calculerBientot(); });
$("retrait-face").addEventListener("click", () => { lireProfil(); profil.faces.pop(); profil.angles.pop(); dessinerProfil(); calculerBientot(); });
document.querySelector(".configurateur").addEventListener("input", calculerBientot);

demarrer();
