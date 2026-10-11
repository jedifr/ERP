// Les PDF (devis, AR, bon de livraison, fiches de fabrication…) s'ouvrent dans un nouvel onglet : le document reste ouvert.
// - Liens et boutons d'action dont l'adresse se termine par « …pdf/ » : ouverts dans un nouvel onglet.
// - « Enregistrer, valider et ouvrir le PDF » : l'onglet est ouvert au clic (seul moment où le navigateur l'autorise),
//   le document s'enregistre et se recharge dans l'onglet courant, puis le PDF s'affiche dans l'onglet réservé.
(function () {
    "use strict";

    const NOM_ONGLET = "dp-pdf";
    const CLE_ATTENTE = "dp-pdf-attente";

    function estLienPdf(a) {
        try {
            const url = new URL(a.href, window.location.origin);
            return url.origin === window.location.origin && /^\/admin\/.*pdf\/?$/.test(url.pathname);
        } catch (e) {
            return false;
        }
    }

    document.addEventListener("click", function (e) {
        const a = e.target.closest && e.target.closest("a[href]");
        if (a && estLienPdf(a)) {
            a.target = "_blank";
            a.rel = "noopener";
            return;
        }
        const bouton = e.target.closest && e.target.closest('button[name="_enregistrer_valider"], input[name="_enregistrer_valider"]');
        if (!bouton) return;
        const formulaire = bouton.form || document.getElementById(bouton.getAttribute("form"));
        if (!formulaire) return;
        const onglet = window.open("about:blank", NOM_ONGLET);
        if (!onglet) return; // fenêtres bloquées : comportement habituel (PDF dans l'onglet courant)
        try {
            onglet.document.title = "PDF en préparation…";
            onglet.document.body.style.cssText = "font-family:system-ui,sans-serif;color:#6b7280;padding:2rem";
            onglet.document.body.textContent = "Enregistrement du document et préparation du PDF…";
        } catch (err) { /* onglet déjà inaccessible */ }
        if (!formulaire.querySelector('input[name="_nouvel_onglet"]')) {
            const marque = document.createElement("input");
            marque.type = "hidden";
            marque.name = "_nouvel_onglet";
            marque.value = "1";
            formulaire.appendChild(marque);
        }
        try { window.sessionStorage.setItem(CLE_ATTENTE, "1"); } catch (err) { /* stockage indisponible */ }
    }, true);

    function apresChargement() {
        const params = new URLSearchParams(window.location.search);
        const pdf = params.get("ouvrir_pdf");
        let attente = false;
        try { attente = window.sessionStorage.getItem(CLE_ATTENTE) === "1"; window.sessionStorage.removeItem(CLE_ATTENTE); } catch (err) { /* ignoré */ }
        if (pdf && pdf.startsWith("/admin/") && !pdf.startsWith("//")) {
            params.delete("ouvrir_pdf");
            const reste = params.toString();
            window.history.replaceState(null, "", window.location.pathname + (reste ? "?" + reste : "") + window.location.hash);
            const onglet = window.open(pdf, NOM_ONGLET);
            if (!onglet) window.location.href = pdf; // aucun onglet réservé : on ouvre le PDF ici
        } else if (attente) {
            // Le document n'a pas été validé (erreurs, devis sous le coût…) : l'onglet réservé n'a plus d'objet.
            const onglet = window.open("", NOM_ONGLET);
            if (onglet) onglet.close();
        }
    }

    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", apresChargement);
    else apresChargement();
})();
