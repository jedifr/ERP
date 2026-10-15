// Point d'entrée pour un hébergement cPanel « Setup Node.js App » (Phusion Passenger, ex. o2switch) : le gestionnaire
// charge ce fichier en CommonJS ; il importe le serveur (module ES) et l'écoute sur le port que Passenger fournit.
import("./server.js").then(({ creerServeur }) => {
  creerServeur({
    erpUrl: process.env.ERP_URL, cle: process.env.ERP_WEB_CLE_API, debitParMinute: Number(process.env.DEBIT_PAR_MINUTE || 60),
    confianceProxy: process.env.CONFIANCE_PROXY !== "0", contact: process.env.CONTACT_EMAIL || "",
  }).listen(process.env.PORT || 3000);
});
