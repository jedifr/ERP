// Lecture d'un fichier STEP par OpenCascade (occt-import-js) dans un worker : le code d'occt-import-js crée des fonctions
// à la volée (embind), ce que la politique de sécurité de la page interdit ; ce worker reçoit sa propre politique (server.js).
importScripts("occt-import-js.js");

let occt = null;
onmessage = async (ev) => {
  try {
    occt = occt || (await occtimportjs({ locateFile: (f) => f }));
    const r = occt.ReadStepFile(ev.data.octets, ev.data.parametres);
    if (!r.success || !r.meshes.length) throw new Error("STEP illisible");
    postMessage({ maillages: r.meshes.map((m) => ({ position: m.attributes.position.array, normale: m.attributes.normal ? m.attributes.normal.array : null, index: m.index.array })) });
  } catch (e) {
    postMessage({ erreur: String(e && e.message || e) });
  }
};
