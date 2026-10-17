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
let lotDxf = null; // dessin vectorisé en plusieurs pièces : une fiche par pièce (ficheDxf = la plus grande)
const DXF_MAX = 1536 * 1024;
const STEP_MAX = 30 * 1024 * 1024; // STEP avant compression (il part compressé, 1,5 Mo au plus une fois compressé)
const estStep = () => Boolean(ficheDxf && ficheDxf.format === "step");
let formatsFichier = ["dxf"]; // formats que l'ERP sait lire (catalogue)
let echelleDxf = 1; // agrandissement demandé par le client (1 = taille du fichier)
// Formes décoratives : liste chargée au premier passage sur l'onglet, forme choisie et sa largeur.
let bibliotheque = null; // { credit, licence_url, formes: [{ cle, nom, proportion, rapport, apercu }] }
let formeDeco = null;
const VIGNETTES_MAX = 150;

// Flan d'un STEP : celui que l'ERP vient de chiffrer (outillage de l'atelier), sinon l'aperçu rendu au dépôt.
function flanStep() {
  return dernier && dernier.demande.dxf === ficheDxf.jeton && dernier.resultat.contour ? dernier.resultat.contour : ficheDxf.contour;
}

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
  afficherCredit(catalogue.bibliotheque);
  majEpaisseurs();
  majReception();
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
  majProcedes();
}

// Procédés possibles pour la tôle choisie (le premier est celui par défaut) ; un seul : affiché sans choix.
const NOMS_PROCEDES = { laser: "Laser", jet_eau: "Jet d'eau" };
function majProcedes() {
  const m = catalogue.matieres.find((x) => x.matiere === $("matiere").value);
  const liste = (m && m.procedes && m.procedes[String(Number($("epaisseur").value))]) || [];
  const avant = $("procede").value;
  remplir($("procede"), liste.map((p) => [p, NOMS_PROCEDES[p] || p]), liste.includes(avant) ? avant : liste[0]);
  $("procede").disabled = liste.length < 2;
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
  verrouCotes();
}

// Rayon d'angle OU chanfrein, jamais les deux (l'ERP refuse aussi) : saisir l'un verrouille l'autre à 0.
const EXCLUSIFS = [["rayon_angle", "chanfrein"]];
function verrouCotes() {
  for (const [x, y] of EXCLUSIFS) {
    const a = $(`cote-${x}`), b = $(`cote-${y}`);
    if (!a || !b) continue;
    for (const [champ, autre] of [[a, b], [b, a]]) {
      const bloque = Number(autre.value) > 0;
      if (bloque) champ.value = 0;
      champ.disabled = bloque;
      champ.closest(".champ").title = bloque ? `Remettez « ${autre.labels[0].textContent.replace(/^[A-Z] /, "")} » à 0 pour saisir cette cote.` : "";
    }
  }
}
$("cotes").addEventListener("input", verrouCotes);

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
  if (d.bibliotheque !== undefined) dessin = formeDeco ? Schemas.contour(contourDeco(), formeDeco.nom, true) : null;
  else if (d.lot) dessin = Schemas.contour(contourLot(), vecto.nom, true);
  else if (d.dxf !== undefined && estStep()) dessin = Schemas.contour(flanStep(), `Flan de ${ficheDxf.nom}`, true);
  else if (d.dxf !== undefined) dessin = ficheDxf ? Schemas.contour(contourDxf(), ficheDxf.nom, true) : null;
  else if (!d.pliage) dessin = Schemas.plane(catalogue.formes.find((x) => x.cle === d.forme), d.cotes);
  else if (d.pliage.forme === "profil") dessin = Schemas.profil(d.pliage.faces, d.pliage.angles, d.pliage.longueur);
  else dessin = Schemas.boite(d.pliage.longueur, d.pliage.largeur, d.pliage.bords);
  $("note-schema").textContent = mode3d ? "Faites glisser pour tourner la pièce, molette ou pincement pour zoomer. Vue indicative."
    : d.bibliotheque !== undefined ? (formeDeco ? "A et B : cotes hors tout. Changez la largeur, la hauteur suit." : "Choisissez une forme dans la liste.")
    : d.lot ? `${d.lot.length} pièces découpées séparément, placées comme sur votre dessin ; A et B : cotes hors tout de l'ensemble.`
    : d.dxf !== undefined && estStep() ? (ficheDxf.plis.length ? "Flan déplié de votre pièce, au rayon de pliage de l'atelier ; A et B : cotes hors tout." : "Pièce plane lue dans votre fichier ; A et B : cotes hors tout.")
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
  if (d.dxf !== undefined && estStep()) return { type: "step", jeton: ficheDxf.jeton, octets: ficheDxf.octets, ...commun };
  if (d.dxf !== undefined) return ficheDxf ? { type: "plaque", contour: contourDxf(), ...commun } : null;
  if (d.bibliotheque !== undefined) return formeDeco ? { type: "plaque", contour: contourDeco(), ...commun } : null;
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
  if (!(mode3d && vue3d && catalogue)) return;
  Promise.resolve(vue3d.afficher(modele3d())).catch((e) => { $("note-schema").textContent = e.message; });
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
  if ($("procede").value) base.procede = $("procede").value;
  const type = typePiece();
  if (type === "plane") {
    const cotes = {};
    document.querySelectorAll("#cotes [data-cle]").forEach((i) => { if (i.value !== "") cotes[i.dataset.cle] = Number(i.value); });
    return { ...base, forme: $("forme").value, cotes };
  }
  if (type === "libre") return { ...base, libre: structuredClone(libre) };
  if (type === "bibliotheque") return { ...base, bibliotheque: formeDeco ? { cle: formeDeco.cle, largeur: Number($("biblio-largeur").value) } : null };
  if (type === "dxf" && lotDxf) return { ...base, dxf: lotDxf[0].jeton, lot: lotDxf.map((f) => ({ ...base, dxf: f.jeton })) };
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
  if (demande.dxf === null || demande.bibliotheque === null) {
    afficherErreur(demande.dxf === null ? "Choisissez votre fichier." : "Choisissez une forme.");
    $("etat").className = "etat";
    return;
  }
  const appel = ++numeroAppel;
  const { ok, corps } = await api("/api/prix", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(demande.lot ? { lot: demande.lot, jeu: true } : demande) });
  if (appel !== numeroAppel) return; // une saisie plus récente est en cours
  if (!ok) return afficherErreur(corps.erreur || "Calcul impossible.");
  const resultat = demande.lot ? resultatJeu(corps) : corps;
  dernier = { demande, resultat };
  afficherResultat(resultat);
}

// Prix des pièces d'un dessin vectorisé, chiffrées par l'ERP comme un seul jeu : toutes imbriquées dans la même tôle,
// temps de coupe cumulé, mise en place comptée une fois. Chaque pièce porte sa part du prix du jeu.
function resultatJeu(jeu) {
  const lignes = (jeu.pieces || []).map((r, i) => (r.erreur ? { statut: "refus", refus: [r.erreur], validations: [], prix_total_ht: null, designation: `Pièce ${i + 1}` } : r));
  return {
    ...jeu, lot: lignes, designation: `${lignes.length} pièces de ${vecto.nom || "votre dessin"}`,
    largeur_mm: vecto.resultat ? vecto.resultat.largeurMm : null, hauteur_mm: vecto.resultat ? vecto.resultat.hauteurMm : null,
    contour: lignes[0] && lignes[0].contour,
  };
}

function afficherErreur(message) {
  dernier = null;
  $("etat").textContent = message;
  $("etat").className = "etat refus";
  $("prix-total").textContent = "–";
  $("prix-ttc").textContent = "";
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
  $("prix-ttc").textContent = avecPrix && r.prix_total_ttc != null ? `${euros(r.prix_total_ttc)} € TTC` : "";
  const unite = r.lot ? `le jeu de ${r.lot.length} pièces` : "la pièce";
  $("prix-unitaire").textContent = !avecPrix ? "" : `soit ${euros(r.prix_unitaire_ht)} € HT`
    + (r.prix_unitaire_ttc != null ? ` (${euros(r.prix_unitaire_ttc)} € TTC)` : "") + ` ${unite}`;
  const details = [[r.lot ? "Pièces" : "Pièce", r.designation]];
  if (r.lot) {
    r.lot.slice(0, 8).forEach((l, i) => details.push([`Pièce ${i + 1}`, (l.largeur_mm ? `${mm(l.largeur_mm)} × ${mm(l.hauteur_mm)} mm` : "–")
      + (l.prix_total_ht != null ? ` : ${euros(l.prix_total_ht)} €` : "")]));
    if (r.lot.length > 8) details.push(["…", `et ${r.lot.length - 8} autres`]);
    if (r.nb_feuilles) details.push(["Découpe groupée", `les ${r.lot.length} pièces imbriquées ensemble dans ${r.nb_feuilles > 1 ? `${r.nb_feuilles} tôles` : "la même tôle"}`]);
  }
  if (r.procede) details.push(["Découpe", PROCEDES[r.procede] || r.procede]);
  if (r.largeur_mm) details.push([r.pliage ? "Flan à plat" : r.lot ? "Encombrement du dessin" : "Encombrement", `${mm(r.largeur_mm)} × ${mm(r.hauteur_mm)} mm`]);
  if (r.poids_kg) details.push(["Poids unitaire", `${r.poids_kg.toLocaleString("fr-FR", { maximumFractionDigits: 2 })} kg`]);
  if (r.pliage && r.pliage.angle_mini) details.push(["Angle de pli mini", `${r.pliage.angle_mini}°`]);
  $("details").replaceChildren(...details.flatMap(([t, v]) => [el("dt", { texte: t }), el("dd", { texte: v })]));
  $("messages").replaceChildren(...[...r.refus, ...r.validations, ...(r.modifications || [])].map((m) => el("li", { texte: m })));
  dessinerApercu(r.contour);
  // contour exact de l'ERP à la place de la vignette (bibliothèque) ou du dépôt (flan STEP chiffré avec l'outillage de l'atelier)
  if ((dernier && dernier.demande.bibliotheque) || estStep()) dessinerSchema();
  else majVue3d();
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
    el("td", { texte: l.designation }), el("td", { texte: `${l.demande.matiere} ${mm(l.demande.epaisseur)} mm${l.demande.procede ? ` · ${NOMS_PROCEDES[l.demande.procede] || l.demande.procede}` : ""}` }),
    el("td", { class: "nombre", texte: l.demande.quantite }), el("td", { class: "nombre", texte: `${euros(l.prix_total_ht)} €` }),
    el("td", {}, el("button", { type: "button", class: "retirer", "data-index": i, "aria-label": `Retirer ${l.designation}`, texte: "×" })))));
  const total = panier.reduce((s, l) => s + l.prix_total_ht, 0);
  const minimum = catalogue ? catalogue.minimum_commande_ht : 0;
  $("total-pieces").textContent = `${euros(total)} €`;
  $("ligne-minimum").hidden = total >= minimum;
  $("complement").textContent = `${euros(Math.max(minimum - total, 0))} €`;
  const totalHt = Math.max(total, minimum), taux = catalogue && catalogue.taux_tva != null ? catalogue.taux_tva : null;
  $("total-panier").textContent = `${euros(totalHt)} €`;
  // TVA sur le total du panier, comme sur la facture
  $("ligne-tva").hidden = $("ligne-ttc").hidden = taux == null;
  if (taux != null) {
    const totalTtc = Math.round(totalHt * (100 + taux)) / 100;
    $("libelle-tva").textContent = `TVA ${String(taux).replace(".", ",")} %`;
    $("tva-panier").textContent = `${euros(totalTtc - totalHt)} €`;
    $("total-ttc").textContent = `${euros(totalTtc)} €`;
  }
}

$("ajouter").addEventListener("click", () => {
  if (!dernier || dernier.resultat.prix_total_ht == null) return;
  const r = dernier.resultat;
  // un dessin en plusieurs pièces : une ligne pour le jeu ; ses pièces partent ensemble, marquées du même jeu, et l'ERP les rechiffre ensemble
  if (dernier.demande.lot) {
    const jeu = `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
    panier.push({ demande: { ...dernier.demande, lot: dernier.demande.lot.map((d) => ({ ...d, jeu })) }, designation: r.designation, prix_total_ht: r.prix_total_ht, statut: r.statut });
  } else panier.push({ demande: dernier.demande, designation: r.designation, prix_total_ht: r.prix_total_ht, statut: r.statut });
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

// Retrait à l'atelier : pas d'adresse de livraison ni de port.
const retraitChoisi = () => document.querySelector("input[name=reception]:checked")?.value === "retrait";
function majReception() {
  const retrait = retraitChoisi();
  document.querySelectorAll("#form-demande .livraison").forEach((c) => {
    c.hidden = retrait;
    c.querySelector("input, textarea").required = !retrait;
  });
  const adresse = catalogue && catalogue.retrait ? catalogue.retrait.adresse : "";
  $("adresse-retrait").hidden = !retrait;
  $("adresse-retrait").textContent = adresse ? `Pièces à retirer à : ${adresse}` : "Nous vous indiquons l'adresse et les horaires de retrait par e-mail.";
  $("aide-demande").textContent = retrait
    ? "Nous vérifions chaque demande et vous confirmons la commande et la date de retrait par e-mail."
    : "Nous vérifions chaque demande et vous confirmons la commande, les frais de port et le délai par e-mail.";
}
document.querySelectorAll("input[name=reception]").forEach((r) => r.addEventListener("change", majReception));

$("form-demande").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.currentTarget, retour = $("retour-demande");
  if (!form.reportValidity()) return;
  const client = Object.fromEntries(new FormData(form));
  const retrait = client.reception === "retrait";
  if (retrait) for (const k of ["adresse", "code_postal", "ville"]) delete client[k];
  const bouton = form.querySelector("button[type=submit]");
  bouton.disabled = true;
  retour.className = "retour";
  retour.textContent = "Envoi…";
  const { ok, corps } = await api("/api/demandes", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ client, lignes: panier.flatMap((l) => l.demande.lot || [l.demande]) }),
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
  majReception();
  retour.className = "retour succes";
  retour.textContent = `Merci ! Demande ${corps.numero} reçue (${euros(corps.total_ht)} € HT${corps.total_ttc != null ? `, ${euros(corps.total_ttc)} € TTC` : ""}${retrait ? ", retrait à l'atelier" : " hors port"}). `
    + (retrait ? "Nous vous confirmons la commande et la date de retrait par e-mail." : "Nous vous confirmons la commande, le port et le délai par e-mail.");
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
  const liste = formatsFichier.filter((f) => f !== "stp").map((f) => f.toUpperCase()); // .stp : même format que .step
  const texte = liste.length > 1 ? `${liste.slice(0, -1).join(", ")} ou ${liste[liste.length - 1]}` : liste[0];
  const images = vectorisable();
  $("onglet-fichier").textContent = `Fichier ${liste.join(", ")}` + (images ? " ou image" : "");
  $("titre-depot").textContent = `Choisissez un fichier ${texte}` + (images ? ", ou un dessin (PNG, JPG)" : "");
  $("fichier-dxf").accept = [...formatsFichier, ...(images ? IMAGES : [])].map((f) => `.${f}`).join(",");
  $("aide-depot").textContent = "Une pièce par fichier, cotes en mm, 1,5 Mo au plus."
    + (formatsFichier.includes("step") ? " STEP : pièce plane ou pliée en tôle d'épaisseur constante, 30 Mo au plus, dépliée par nos soins." : "")
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
  lotDxf = null;
  if (!extension || !formatsFichier.includes(extension.toLowerCase())) {
    etat.className = "aide erreur";
    etat.textContent = `Choisissez un fichier ${formatsFichier.map((f) => `.${f}`).join(", ")} (exporté depuis votre logiciel de dessin).`;
    return false;
  }
  const step = ["step", "stp"].includes(extension.toLowerCase());
  if (fichier.size > (step ? STEP_MAX : DXF_MAX)) {
    etat.className = "aide erreur";
    etat.textContent = `Fichier trop gros (${step ? "30" : "1,5"} Mo au plus) : envoyez-le avec une demande de devis.`;
    return false;
  }
  etat.textContent = vectorise ? "Envoi du contour…" : `Lecture de ${fichier.name}…`;
  let contenu, octets = null;
  try {
    if (step) {
      // Le STEP (texte) part compressé ; le navigateur garde le fichier pour la vue 3D.
      octets = new Uint8Array(await fichier.arrayBuffer());
      const compresse = typeof CompressionStream === "function" ? await new Response(new Blob([octets]).stream().pipeThrough(new CompressionStream("gzip"))).blob() : fichier;
      if (compresse.size > DXF_MAX) {
        etat.className = "aide erreur";
        etat.textContent = "Fichier STEP trop lourd pour un prix en ligne : envoyez-le avec une demande de devis.";
        return false;
      }
      contenu = await lireEnBase64(compresse);
      etat.textContent = `Analyse de ${fichier.name} (épaisseur, plis, dépliage)…`;
    } else {
      contenu = await lireEnBase64(fichier);
    }
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
  ficheDxf = vectorise ? { ...corps, vectorise } : octets ? { ...corps, octets } : corps;
  echelleDxf = 1;
  etat.textContent = estStep() ? choisirEpaisseurStep() : "";
  afficherFicheDxf();
  calculerBientot();
  if (estStep() && !mode3d) basculerVue(true); // comme l'aperçu d'un STEP : la pièce en volume d'abord
  return true;
}

// L'épaisseur d'une pièce STEP est celle du fichier : on la choisit (et la matière s'il le faut).
function choisirEpaisseurStep() {
  const e = ficheDxf.epaisseur_mm, egal = (x) => Math.abs(x - e) < 0.05;
  let m = catalogue.matieres.find((x) => x.matiere === $("matiere").value);
  if (!m || !m.epaisseurs.some(egal)) {
    m = catalogue.matieres.find((x) => x.epaisseurs.some(egal));
    if (!m) return `Votre pièce est en ${mm(e)} mm : cette épaisseur n'est pas vendue en ligne, envoyez-la avec une demande de devis.`;
    $("matiere").value = m.matiere;
    majEpaisseurs();
  }
  $("epaisseur").value = String(m.epaisseurs.find(egal));
  majProcedes();
  majMiniDeco();
  return "";
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
  // dessin vectorisé : la taille se règle dans le panneau du dessin ; pièce 3D : fabriquée à la taille du fichier
  $("echelle-dxf").hidden = !f || Boolean(f.vectorise) || estStep();
  majCotesDxf();
  if (!f) return infos.replaceChildren();
  if (estStep()) {
    const [x, y, z] = f.encombrement_mm;
    const lignes = [["Fichier", f.nom], ["Épaisseur", `${mm(f.epaisseur_mm)} mm`], ["Encombrement", `${mm(x)} × ${mm(y)} × ${mm(z)} mm`],
      ["Plis", f.plis.length ? f.plis.map((p) => `${mm(p.angle)}°`).join(", ") : "aucun (pièce plane)"],
      ["Découpes intérieures", String((f.contour.trous || []).length)]];
    if (f.avertissements && f.avertissements.length) lignes.push(["À vérifier", f.avertissements.join(" ")]);
    return infos.replaceChildren(...lignes.flatMap(([t, v]) => [el("dt", { texte: t }), el("dd", { texte: v })]));
  }
  const lignes = lotDxf ? [["Pièces", `${lotDxf.length} fichiers (${lotDxf[0].nom}…)`], ["Taille de la plus grande", `${mm(f.largeur_mm)} × ${mm(f.hauteur_mm)} mm`]]
    : [["Fichier", f.nom], [f.vectorise ? "Taille de la pièce" : "Taille du fichier", `${mm(f.largeur_mm)} × ${mm(f.hauteur_mm)} mm`], ["Découpes intérieures", String((f.contour.trous || []).length)]];
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

// ------------------------------------------------------------------ formes décoratives (game-icons.net, CC BY 3.0)
function afficherCredit(c) {
  if (!c || !c.credit) return;
  const lien = () => el("a", { href: c.licence_url, rel: "license noopener", target: "_blank", texte: c.licence });
  $("biblio-credit").replaceChildren(c.credit.replace(/,? ?licence.*$/i, ""), ", licence ", lien(), ".");
  $("pied-credit").replaceChildren(" · Formes décoratives : ", el("a", { href: c.url, rel: "noopener", target: "_blank", texte: c.source }), ", ", lien());
}

async function chargerBibliotheque() {
  if (bibliotheque) return;
  $("biblio-etat").textContent = "Chargement des formes…";
  const { ok, corps } = await api("/api/bibliotheque");
  if (!ok) {
    $("biblio-etat").textContent = corps.erreur || "Formes indisponibles pour le moment.";
    return;
  }
  bibliotheque = corps;
  for (const f of bibliotheque.formes) f.recherche = sansAccents(`${f.nom} ${f.cle.replace(/-/g, " ")}`);
  afficherCredit(corps);
  afficherVignettes();
}

const sansAccents = (t) => t.normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/œ/g, "oe").toLowerCase();

function vignette(f) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 100 ${+(f.proportion * 100).toFixed(1)}`);
  svg.setAttribute("aria-hidden", "true");
  const chemin = document.createElementNS("http://www.w3.org/2000/svg", "path");
  chemin.setAttribute("d", f.apercu);
  chemin.setAttribute("fill-rule", "evenodd");
  svg.append(chemin);
  return svg;
}

function afficherVignettes() {
  if (!bibliotheque) return;
  const mots = sansAccents($("biblio-recherche").value).split(/\s+/).filter(Boolean);
  const trouvees = bibliotheque.formes.filter((f) => mots.every((m) => f.recherche.includes(m)));
  const montrees = trouvees.slice(0, VIGNETTES_MAX);
  if (formeDeco && !montrees.includes(formeDeco) && trouvees.includes(formeDeco)) montrees.unshift(formeDeco);
  $("biblio-grille").replaceChildren(...montrees.map((f) => {
    const b = el("button", { type: "button", role: "option", "data-cle": f.cle, "aria-selected": String(f === formeDeco), title: f.nom });
    b.append(vignette(f), el("span", { texte: f.nom }));
    return b;
  }));
  $("biblio-etat").textContent = !trouvees.length ? "Aucune forme ne correspond : essayez un autre mot."
    : trouvees.length > montrees.length ? `${trouvees.length} formes : précisez la recherche pour voir les autres.`
      : `${trouvees.length} forme${trouvees.length > 1 ? "s" : ""}.`;
}

// Largeur mini à l'épaisseur choisie : aucun trait ni jour plus fin que l'épaisseur (l'ERP refuse en dessous).
const largeurMiniDeco = () => (formeDeco ? Math.ceil(Number($("epaisseur").value || 0) / formeDeco.rapport - 1e-9) : 1);

function majMiniDeco() {
  if (!formeDeco) return;
  const mini = largeurMiniDeco();
  $("biblio-largeur").min = mini;
  $("biblio-mini").textContent = `En ${mm(Number($("epaisseur").value))} mm, cette forme se découpe à partir de ${mini} mm de large (aucun trait ni jour plus fin que l'épaisseur).`;
}

function contourDeco() {
  if (!formeDeco) return null;
  const largeur = Number($("biblio-largeur").value);
  // contour exact de l'ERP s'il vient d'être chiffré à cette largeur, sinon celui de la vignette
  const d = dernier && dernier.demande.bibliotheque;
  if (d && d.cle === formeDeco.cle && d.largeur === largeur) return dernier.resultat.contour;
  return Schemas.depuisApercu(formeDeco.apercu, largeur > 0 ? largeur : 100);
}

function majHauteurDeco() {
  const l = Number($("biblio-largeur").value);
  $("biblio-hauteur").value = formeDeco && l > 0 ? String(+(l * formeDeco.proportion).toFixed(1)) : "";
}

$("biblio-recherche").addEventListener("input", (ev) => { ev.stopPropagation(); afficherVignettes(); });
$("biblio-grille").addEventListener("click", (ev) => {
  const b = ev.target.closest("button[data-cle]");
  if (!b) return;
  formeDeco = bibliotheque.formes.find((f) => f.cle === b.dataset.cle);
  $("biblio-grille").querySelectorAll("button").forEach((x) => x.setAttribute("aria-selected", String(x === b)));
  $("biblio-nom").textContent = formeDeco.nom;
  $("biblio-choix").hidden = false;
  $("biblio-largeur").value = Math.max(200, largeurMiniDeco());
  majHauteurDeco();
  majMiniDeco();
  calculerBientot();
});
$("biblio-largeur").addEventListener("input", majHauteurDeco);
$("biblio-hauteur").addEventListener("input", (ev) => {
  ev.stopPropagation();
  const h = Number(ev.target.value);
  if (!formeDeco || !(h > 0)) return;
  $("biblio-largeur").value = String(+(h / formeDeco.proportion).toFixed(1));
  calculerBientot();
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
  lotDxf = null;
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
    lissage: Number($("vecto-lissage").value), plusieurs: $("vecto-plusieurs").checked, pxSource: w / vecto.image.width, grossirMm: Number($("vecto-grossir").value) || 0, epaisseurMm: epaisseur,
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
    lotDxf = null;
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
  chemin.setAttribute("d", Vectoriseur.chemin(r, 0, 0, 2));
  svg.replaceChildren(image, chemin);
  const messages = [];
  let info = "";
  if (r.vide) messages.push("Rien à découper : changez le contraste ou cochez « Inverser ».");
  else {
    const trous = r.contours.filter((c) => c.trou).length;
    info = r.pieces.length > 1 ? `${r.pieces.length} pièces séparées, ensemble de ${mm(r.largeurMm)} × ${mm(r.hauteurMm)} mm, ${trous} découpe${trous > 1 ? "s" : ""} intérieure${trous > 1 ? "s" : ""}.`
      : `Pièce de ${mm(r.largeurMm)} × ${mm(r.hauteurMm)} mm, ${trous} découpe${trous > 1 ? "s" : ""} intérieure${trous > 1 ? "s" : ""}.`;
    if (r.ecartes && $("vecto-plusieurs").checked) messages.push(`${r.ecartes} morceau${r.ecartes > 1 ? "x" : ""} trop petit${r.ecartes > 1 ? "s" : ""} pour être découpé${r.ecartes > 1 ? "s" : ""} retiré${r.ecartes > 1 ? "s" : ""}.`);
    else if (r.ecartes) messages.push(`${r.ecartes} morceau${r.ecartes > 1 ? "x" : ""} détaché${r.ecartes > 1 ? "s" : ""} du reste retiré${r.ecartes > 1 ? "s" : ""} (il${r.ecartes > 1 ? "s" : ""} tomberai${r.ecartes > 1 ? "en" : ""}t à la découpe) : reliez-le${r.ecartes > 1 ? "s" : ""} à la pièce dans votre dessin, ou cochez « Découper chaque morceau ».`);
    if (r.combles >= Math.max(1, epaisseur * epaisseur)) messages.push(`Jours plus fins que la tôle (${mm(epaisseur)} mm) comblés.`);
    if (r.retires >= Math.max(1, epaisseur * epaisseur)) messages.push(`Traits plus fins que la tôle (${mm(epaisseur)} mm) retirés : grossissez le trait pour les garder.`);
  }
  $("vecto-largeur-titre").textContent = r.pieces && r.pieces.length > 1 ? "Largeur de l'ensemble (mm)" : "Largeur de la pièce (mm)";
  $("vecto-info").textContent = info;
  $("vecto-messages").replaceChildren(...messages.map((m) => el("li", { texte: m })));
}

async function envoyerVecto() {
  const r = vecto.resultat;
  if (!r || r.vide) return;
  const n = ++vecto.envoi;
  const base = vecto.nom.replace(/\.[^.]+$/, "") || "dessin";
  if (r.pieces.length === 1) {
    const fichier = new File([Vectoriseur.enSvg(r.pieces[0])], `${base}-vectorise.svg`, { type: "image/svg+xml" });
    return envoyerDxf(fichier, { n, epaisseur: Number($("epaisseur").value) || 0 });
  }
  // plusieurs pièces : un fichier SVG par pièce, déposés en un seul appel
  const etat = $("etat-dxf");
  etat.className = "aide";
  etat.textContent = `Envoi des ${r.pieces.length} pièces…`;
  const fichiers = r.pieces.map((p, i) => ({ nom: `${base}-piece-${i + 1}.svg`, contenu: btoa(unescape(encodeURIComponent(Vectoriseur.enSvg(p)))) }));
  const { ok, corps } = await api("/api/dxf", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ fichiers }) });
  if (n !== vecto.envoi) return false; // un contour plus récent est parti entre-temps
  const fiches = ok ? corps.fichiers || [] : [];
  const refusees = fiches.map((f, i) => (f.erreur ? `pièce ${i + 1} : ${f.erreur}` : "")).filter(Boolean);
  if (!ok || refusees.length || fiches.length !== r.pieces.length) {
    etat.className = "aide erreur";
    etat.textContent = ok ? `Pièces refusées (${refusees.join(" ; ")}).` : corps.erreur || "Pièces refusées.";
    return false;
  }
  etat.textContent = "";
  lotDxf = fiches.map((f) => ({ ...f, vectorise: { n } }));
  ficheDxf = lotDxf[0];
  echelleDxf = 1;
  afficherFicheDxf();
  calculerBientot();
  return true;
}

// Pièces du dessin pour le schéma coté, placées comme sur le dessin (repère y vers le haut, comme les contours de l'ERP).
function contourLot() {
  const r = vecto.resultat;
  if (!r || r.vide) return null;
  const H = r.boite[3], x0 = r.boite[0], pts = (c) => c.points.map(([x, y]) => [x - x0, H - y]);
  const pieces = r.pieces.map((p) => ({ exterieur: pts(p.contours.find((c) => !c.trou) || p.contours[0]), trous: p.contours.filter((c) => c.trou).map(pts) }));
  return { ...pieces[0], autres: pieces.slice(1) };
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
  $("bloc-bibliotheque").hidden = type !== "bibliotheque";
  if (type === "bibliotheque") chargerBibliotheque();
  calculerBientot();
}));
$("forme").addEventListener("change", () => { majCotes(); calculerBientot(); });
$("matiere").addEventListener("change", () => { majEpaisseurs(); majMiniDeco(); calculerBientot(); });
$("epaisseur").addEventListener("change", () => { majProcedes(); majMiniDeco(); });
$("modele-profil").addEventListener("change", (e) => { profil = structuredClone(MODELES[e.target.value]); dessinerProfil(); calculerBientot(); });
$("ajout-face").addEventListener("click", () => { lireProfil(); profil.faces.push(30); profil.angles.push(90); dessinerProfil(); calculerBientot(); });
$("retrait-face").addEventListener("click", () => { lireProfil(); profil.faces.pop(); profil.angles.pop(); dessinerProfil(); calculerBientot(); });
document.querySelector(".configurateur").addEventListener("input", calculerBientot);

// ------------------------------------------------------------------ fichier envoyé par decoupe.eu
// decoupe.eu ouvre ?depot=site dans un nouvel onglet : on lui dit qu'on est prêt (h2o-pret), il envoie le fichier
// (h2o-fichier), qui s'importe comme un dépôt du visiteur. Le fichier passe d'onglet à onglet, sans serveur.
const ORIGINES_DEPOT = ["https://decoupe.eu", "https://www.decoupe.eu"];

function recevoirDepuisSite(pret) {
  if (!window.opener || new URLSearchParams(location.search).get("depot") !== "site") return;
  window.addEventListener("message", async (ev) => {
    if (!ORIGINES_DEPOT.includes(ev.origin)) return; // aucun autre site
    const d = ev.data;
    if (!d || d.type !== "h2o-fichier" || !(d.fichier instanceof Blob)) return;
    const fichier = d.fichier instanceof File ? d.fichier : new File([d.fichier], String(d.nom || "piece"));
    const extension = (fichier.name.split(".").pop() || "").toLowerCase();
    if (!["dxf", "svg", "step", "stp"].includes(extension)) return;
    await pret; // formats lisibles connus (catalogue)
    // d.procede (« Jet d'eau », « Laser », « Pliage ») : présélectionné s'il est proposé pour la tôle choisie
    const voulu = { "jet d'eau": "jet_eau", laser: "laser" }[String(d.procede || "").toLowerCase()];
    if (voulu && [...$("procede").options].some((o) => o.value === voulu)) $("procede").value = voulu;
    const onglet = document.querySelector("input[name=type][value=dxf]");
    onglet.checked = true;
    onglet.dispatchEvent(new Event("change"));
    envoyerDxf(fichier);
  });
  for (const origine of ORIGINES_DEPOT) window.opener.postMessage({ type: "h2o-pret" }, origine);
}

recevoirDepuisSite(demarrer());
