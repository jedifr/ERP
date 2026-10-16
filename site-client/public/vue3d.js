// Vue 3D du configurateur (three.js, chargée seulement quand le client ouvre l'onglet 3D).
// Pièce plane : contour calculé par l'ERP extrudé à l'épaisseur. Profil : section extrudée à la longueur.
// Boîte : fond et bords relevés à leur angle. Purement visuel, comme le schéma coté.
import * as THREE from "./vendor/three.module.min.js";
import { OrbitControls } from "./vendor/OrbitControls.js";

const TEINTES = { inox: 0xc6ccd4, alu: 0xd9dde2, acier: 0x6f767f };

function teinte(famille) {
  const f = (famille || "").toLowerCase();
  if (f.includes("inox")) return TEINTES.inox;
  if (f.includes("alu")) return TEINTES.alu;
  return TEINTES.acier;
}

// Forme three.js d'un contour { exterieur, trous } (mm, y vers le haut).
function forme(contour) {
  const s = new THREE.Shape(contour.exterieur.map(([x, y]) => new THREE.Vector2(x, y)));
  for (const t of contour.trous || []) s.holes.push(new THREE.Path(t.map(([x, y]) => new THREE.Vector2(x, y))));
  return s;
}

// Section d'un profil : la ligne extérieure des faces, et la même décalée de l'épaisseur (raccords en onglet).
function sectionProfil(faces, angles, e) {
  const pts = [[0, 0]];
  let cap = 0;
  faces.forEach((f, i) => {
    const [x, y] = pts[pts.length - 1];
    pts.push([x + Math.max(f, 0.1) * Math.cos(cap), y + Math.max(f, 0.1) * Math.sin(cap)]);
    if (i < angles.length) cap += Math.sign(angles[i] || 1) * (180 - Math.min(Math.abs(angles[i]) || 90, 179)) * Math.PI / 180;
  });
  const normales = pts.slice(1).map((b, i) => {
    const a = pts[i], n = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1;
    return [-(b[1] - a[1]) / n, (b[0] - a[0]) / n];
  });
  const decale = pts.map((p, i) => {
    const n1 = normales[Math.max(i - 1, 0)], n2 = normales[Math.min(i, normales.length - 1)];
    const k = 1 + n1[0] * n2[0] + n1[1] * n2[1];
    const m = k > 0.2 ? [(n1[0] + n2[0]) / k, (n1[1] + n2[1]) / k] : n2;
    return [p[0] + m[0] * e, p[1] + m[1] * e];
  });
  return { exterieur: [...pts, ...decale.reverse()], trous: [] };
}

export function creerVue(conteneur) {
  let rendu;
  try {
    rendu = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  } catch {
    conteneur.textContent = "La vue 3D n'est pas disponible sur ce navigateur.";
    return { afficher() {}, detruire() {} };
  }
  rendu.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  conteneur.replaceChildren(rendu.domElement);
  const scene = new THREE.Scene();
  scene.add(new THREE.HemisphereLight(0xffffff, 0x8a929c, 1.6));
  const soleil = new THREE.DirectionalLight(0xffffff, 1.8);
  soleil.position.set(1, 1.5, 2);
  scene.add(soleil);
  const contre = new THREE.DirectionalLight(0xffffff, 0.6);
  contre.position.set(-2, -1, 1);
  scene.add(contre);
  const camera = new THREE.PerspectiveCamera(35, 1, 0.1, 100000);
  camera.up.set(0, 0, 1); // z vers le haut : la tôle est posée à plat
  const controles = new OrbitControls(camera, rendu.domElement);
  controles.enableDamping = true;
  let piece = null;
  let anime = 0;

  const dessiner = () => rendu.render(scene, camera);
  const boucle = () => {
    if (controles.update()) dessiner();
    anime = requestAnimationFrame(boucle);
  };
  controles.addEventListener("change", dessiner);
  const ajuster = () => {
    const l = conteneur.clientWidth || 300, h = conteneur.clientHeight || 225;
    rendu.setSize(l, h, false);
    camera.aspect = l / h;
    camera.updateProjectionMatrix();
    dessiner();
  };
  const observateur = new ResizeObserver(ajuster);
  observateur.observe(conteneur);

  function vider() {
    if (!piece) return;
    scene.remove(piece);
    piece.traverse((o) => { o.geometry?.dispose(); o.material?.dispose(); });
    piece = null;
  }

  function maille(geometrie, couleur) {
    const g = new THREE.Group();
    g.add(new THREE.Mesh(geometrie, new THREE.MeshStandardMaterial({ color: couleur, metalness: 0.55, roughness: 0.4, side: THREE.DoubleSide })));
    g.add(new THREE.LineSegments(new THREE.EdgesGeometry(geometrie, 25), new THREE.LineBasicMaterial({ color: 0x2b3440, transparent: true, opacity: 0.55 })));
    return g;
  }

  // Place la caméra pour voir toute la pièce, en vue 3/4 (ou garde l'orientation choisie si la pièce change peu).
  function cadrer(objet, garderVue) {
    const boite = new THREE.Box3().setFromObject(objet);
    const sphere = boite.getBoundingSphere(new THREE.Sphere());
    const r = Math.max(sphere.radius, 1);
    const direction = garderVue ? camera.position.clone().sub(controles.target).normalize() : new THREE.Vector3(0.9, -1.3, 1.1).normalize();
    controles.target.copy(sphere.center);
    camera.position.copy(sphere.center).addScaledVector(direction, r / Math.sin(THREE.MathUtils.degToRad(camera.fov / 2)) * 1.05);
    camera.near = r / 100;
    camera.far = r * 100;
    camera.updateProjectionMatrix();
    controles.update();
  }

  let derniereCle = "";
  return {
    // modele : { type: "plaque", contour, epaisseur, famille } | { type: "profil", faces, angles, longueur, epaisseur, famille }
    //        | { type: "boite", longueur, largeur, bords: [A, B, C, D], angle, epaisseur, famille }
    afficher(modele) {
      vider();
      if (!modele) return dessiner();
      const e = Math.max(Number(modele.epaisseur) || 1, 0.1);
      const couleur = teinte(modele.famille);
      const groupe = new THREE.Group();
      if (modele.type === "plaque" && modele.contour?.exterieur?.length) {
        groupe.add(maille(new THREE.ExtrudeGeometry(forme(modele.contour), { depth: e, bevelEnabled: false, curveSegments: 4 }), couleur));
      } else if (modele.type === "profil") {
        const geo = new THREE.ExtrudeGeometry(forme(sectionProfil(modele.faces, modele.angles, e)), { depth: Math.max(modele.longueur || 1, 1), bevelEnabled: false });
        const m = maille(geo, couleur);
        m.rotation.x = Math.PI / 2; // section debout, longueur à plat
        groupe.add(m);
      } else if (modele.type === "boite") {
        const L = Math.max(modele.longueur, 1), l = Math.max(modele.largeur, 1);
        const [a, b, c, d] = modele.bords.map((x) => Math.max(x || 0, 0));
        const releve = THREE.MathUtils.degToRad(180 - Math.min(Math.max(modele.angle || 90, 60), 179));
        const fond = maille(new THREE.BoxGeometry(L, l, e), couleur);
        fond.position.set(L / 2, l / 2, e / 2);
        groupe.add(fond);
        // Chaque bord pivote autour de son arête de pli ; dans le repère du pivot, l'arête suit x et le rabat à plat est côté -y.
        const bord = (longueur, hauteur, origine, rotationZ) => {
          if (!hauteur) return;
          const pivot = new THREE.Group();
          pivot.position.set(...origine);
          pivot.rotation.z = rotationZ;
          const plaque = maille(new THREE.BoxGeometry(longueur, hauteur, e), couleur);
          plaque.position.set(longueur / 2, -hauteur / 2, e / 2);
          const charniere = new THREE.Group();
          charniere.rotation.x = -releve; // le rabat (côté -y) se relève vers +z
          charniere.add(plaque);
          pivot.add(charniere);
          groupe.add(pivot);
        };
        bord(L, a, [0, 0, 0], 0); // A : le long de la longueur, côté y = 0
        bord(l, b, [L, 0, 0], Math.PI / 2); // B : côté x = L
        bord(L, c, [L, l, 0], Math.PI); // C : côté y = l
        bord(l, d, [0, l, 0], -Math.PI / 2); // D : côté x = 0
      }
      if (!groupe.children.length) return dessiner();
      piece = groupe;
      scene.add(piece);
      const cle = modele.type;
      cadrer(piece, cle === derniereCle);
      derniereCle = cle;
      ajuster();
      if (!anime) boucle();
    },
    detruire() {
      cancelAnimationFrame(anime);
      anime = 0;
      observateur.disconnect();
      vider();
      controles.dispose();
      rendu.dispose();
    },
  };
}
