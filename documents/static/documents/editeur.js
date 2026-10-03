/* Éditeur visuel des modèles de documents (GrapesJS). Les données viennent de #doc-config. */
(function () {
  "use strict";
  const cfg = JSON.parse(document.getElementById("doc-config").textContent);
  const $ = (id) => document.getElementById(id);
  const csrf = (document.cookie.match(/csrftoken=([^;]+)/) || [])[1] || "";

  // --- GrapesJS ---------------------------------------------------------------------------------------------
  const cssCanvas = URL.createObjectURL(new Blob([cfg.cssCanvas], { type: "text/css" }));
  const options = {
    container: "#gjs", height: "100%", width: "auto", fromElement: false, storageManager: false,
    panels: { defaults: [] }, deviceManager: { devices: [] },
    blockManager: { appendTo: "#panneau-blocs" },
    layerManager: { appendTo: "#panneau-calques" },
    selectorManager: { appendTo: "#selecteurs" },
    traitManager: { appendTo: "#proprietes" },
    styleManager: {
      appendTo: "#styles",
      sectors: [
        { name: "Texte", open: true, properties: ["font-family", "font-size", "font-weight", "color", "text-align", "line-height"] },
        { name: "Dimensions", open: false, properties: ["width", "height", "max-width", "margin", "padding"] },
        { name: "Fond et bordures", open: false, properties: ["background-color", "border", "border-radius", "border-bottom", "border-top"] },
        { name: "Disposition", open: false, properties: ["display", "flex-direction", "justify-content", "align-items", "gap", "float"] },
        { name: "Position", open: false, properties: ["position", "top", "bottom", "left", "right"] },
      ],
    },
    assetManager: { embedAsBase64: true, upload: false },
    canvas: { styles: [cssCanvas] },
  };
  if (cfg.projet) {
    try { options.projectData = JSON.parse(cfg.projet); } catch (e) { options.components = cfg.html; options.style = cfg.css; }
  } else {
    options.components = cfg.html;
    options.style = cfg.css;
  }
  const editor = grapesjs.init(options);

  // --- blocs ------------------------------------------------------------------------------------------------
  const blocs = editor.Blocks;
  const colonnes = (n) => '<div style="display:flex;gap:6mm">' + '<div style="flex:1">Texte</div>'.repeat(n) + "</div>";
  [
    ["texte", "Texte", '<div>Texte à modifier</div>'],
    ["titre", "Titre", '<h2 style="font-size:14pt;margin:0 0 3mm 0">Titre</h2>'],
    ["col2", "2 colonnes", colonnes(2)],
    ["col3", "3 colonnes", colonnes(3)],
    ["image", "Image", { type: "image", style: { "max-width": "60mm" } }],
    ["trait", "Séparateur", '<hr style="border:0;border-top:0.5pt solid #9ca3af;margin:4mm 0">'],
    ["espace", "Espace", '<div style="height:8mm"></div>'],
    ["cadre", "Cadre", '<div style="border:0.6pt solid #6b7280;padding:3mm;min-height:20mm">Contenu</div>'],
  ].forEach(([id, label, contenu]) => blocs.add("base-" + id, { label, category: "Mise en page", content: contenu }));
  cfg.blocs.forEach((b) => blocs.add("doc-" + b.id, { label: b.label, category: "Éléments du document", content: b.contenu }));
  cfg.variables.forEach((g) =>
    g.variables.forEach((v) =>
      blocs.add("var-" + v.chemin, {
        label: v.libelle, category: "Variable : " + g.groupe,
        content: { type: "text", content: "{{ " + v.chemin + " }}", style: { display: "inline-block" } },
      })
    )
  );

  // --- propriétés « afficher si » / « répéter sur » sur tout élément ---------------------------------------
  const TRAITS = [
    { type: "text", name: "data-if", label: "Afficher si" },
    { type: "text", name: "data-repeat", label: "Répéter sur" },
    { type: "text", name: "data-as", label: "Nom de l'élément" },
  ];
  editor.on("component:create", (c) => {
    if (!c.addTrait || !c.getTrait) return;
    TRAITS.forEach((t) => { if (!c.getTrait(t.name)) c.addTrait(t); });
  });

  // --- interface --------------------------------------------------------------------------------------------
  document.querySelectorAll("[data-onglet]").forEach((b) => b.addEventListener("click", () => {
    document.querySelectorAll("[data-onglet]").forEach((x) => x.classList.toggle("actif", x === b));
    $("panneau-blocs").hidden = b.dataset.onglet !== "blocs";
    $("panneau-calques").hidden = b.dataset.onglet !== "calques";
  }));
  document.querySelectorAll("[data-onglet-d]").forEach((b) => b.addEventListener("click", () => {
    document.querySelectorAll("[data-onglet-d]").forEach((x) => x.classList.toggle("actif", x === b));
    $("panneau-style").hidden = b.dataset.ongletD !== "style";
    $("panneau-proprietes").hidden = b.dataset.ongletD !== "proprietes";
  }));

  let message = null;
  function dire(texte, erreur) {
    const el = $("doc-message");
    el.textContent = texte; el.classList.toggle("erreur", !!erreur); el.hidden = false;
    clearTimeout(message); message = setTimeout(() => { el.hidden = true; }, erreur ? 9000 : 3500);
  }

  function contenu() {
    const html = editor.getHtml({ cleanId: true }).replace(/^\s*<body[^>]*>/i, "").replace(/<\/body>\s*$/i, "");
    return { html, css: editor.getCss({ avoidProtected: true }) };
  }

  async function appel(url, corps) {
    return fetch(url, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
      body: JSON.stringify(corps),
    });
  }

  $("chk-actif").checked = !!cfg.actif;

  async function enregistrer() {
    const c = contenu();
    const reponse = await appel(cfg.urls.enregistrer, {
      ...c, projet: JSON.stringify(editor.getProjectData()), actif: $("chk-actif").checked,
    });
    const donnees = await reponse.json().catch(() => ({}));
    if (!reponse.ok) { dire(donnees.detail || "Enregistrement impossible.", true); return; }
    editor.clearDirtyCount && editor.clearDirtyCount();
    $("doc-etat").textContent = "Version " + donnees.version + (donnees.actif ? " — actif" : " — inactif (PDF d'origine)");
    let texte = "Modèle enregistré" + (donnees.actif ? " et activé." : ".");
    if (donnees.inconnues && donnees.inconnues.length) {
      texte += " Variables inconnues (vides dans le PDF) : " + donnees.inconnues.join(", ");
    }
    dire(texte, donnees.inconnues && donnees.inconnues.length);
  }

  async function apercu() {
    const reponse = await appel(cfg.urls.apercu, { ...contenu(), objet: $("sel-objet").value });
    if (!reponse.ok) {
      const donnees = await reponse.json().catch(() => ({}));
      dire(donnees.detail || "Aperçu impossible.", true);
      return;
    }
    const url = URL.createObjectURL(await reponse.blob());
    $("doc-apercu-cadre").src = url;
    $("doc-apercu-lien").href = url;
    $("doc-apercu").hidden = false;
  }

  async function modeleParDefaut() {
    if (!confirm("Remplacer la mise en page actuelle par le modèle par défaut ? (rien n'est enregistré tant que vous ne cliquez pas sur Enregistrer)")) return;
    const donnees = await (await fetch(cfg.urls.defaut, { credentials: "same-origin" })).json();
    editor.setComponents(donnees.html);
    editor.setStyle(donnees.css);
    dire("Modèle par défaut chargé : cliquez sur Enregistrer pour le conserver.");
  }

  $("btn-enregistrer").addEventListener("click", enregistrer);
  $("btn-apercu").addEventListener("click", apercu);
  $("btn-defaut").addEventListener("click", modeleParDefaut);
  $("btn-annuler").addEventListener("click", () => editor.UndoManager.undo());
  $("btn-refaire").addEventListener("click", () => editor.UndoManager.redo());
  $("btn-fermer-apercu").addEventListener("click", () => { $("doc-apercu").hidden = true; $("doc-apercu-cadre").src = "about:blank"; });
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "s") { e.preventDefault(); enregistrer(); }
  });
  window.addEventListener("beforeunload", (e) => {
    if (editor.getDirtyCount && editor.getDirtyCount() > 0) { e.preventDefault(); e.returnValue = ""; }
  });

  fetch(cfg.urls.objets, { credentials: "same-origin" }).then((r) => r.json()).then((d) => {
    d.objets.forEach((o) => {
      const opt = document.createElement("option");
      opt.value = o.id; opt.textContent = "Aperçu : " + o.label;
      $("sel-objet").appendChild(opt);
    });
  });
  window.editeurDocument = editor; // pratique pour les tests et le dépannage
})();
