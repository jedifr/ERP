"""Prépare le passage en production : vide les documents de test (devis, commandes, livraisons, ordres de fabrication, factures,
écritures, achats, sous-traitance, pièces et imbrications) et remet leurs compteurs à zéro, en gardant les données de référence
(clients et fournisseurs, articles, matières, postes, gammes, paramètres de coupe, formats de tôle, plan comptable, société…).

Par défaut : simple liste de ce qui serait supprimé (rien n'est modifié). Pour supprimer : `--confirmer --sauvegarde-faite`
(lancez d'abord `./sauvegarder-nas.sh`), puis tapez SUPPRIMER (ou ajoutez `--oui`)."""

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

# Du plus dépendant au plus indépendant (les liens PROTECT l'exigent).
DOCUMENTS = [
    "soustraitance.RetourSousTraitance", "soustraitance.EnvoiSousTraitance",
    "comptabilite.LigneEcriture", "comptabilite.EcritureComptable",
    "facturation.RelanceFacture", "facturation.FactureLigne", "facturation.Facture",
    "chiffrage.OperationOF", "chiffrage.ComposantOF", "chiffrage.OrdreFabrication",
    "chiffrage.LivraisonLigne", "chiffrage.Livraison",
    "chiffrage.CommandeLigneModification", "chiffrage.CommandeLigne", "chiffrage.Commande",
    "decoupe.PieceDecoupe", "decoupe.PieceProfile", "decoupe.ImbricationPlacement", "decoupe.ImbricationLigne", "decoupe.ImbricationJob",
    "chiffrage.DevisLigneOperation", "chiffrage.DevisLigne", "chiffrage.Devis",
    "achats.ReceptionLigne", "achats.Reception", "achats.FactureFournisseur", "achats.LigneCommandeFournisseur", "achats.CommandeFournisseur",
]
STOCK = [
    "stock.AlerteStock", "stock.MouvementStock", "stock.Transfert", "stock.InventaireLigne", "stock.Inventaire", "stock.Lot",
]
# Compteurs de codification des documents (pas ceux des tiers, emplacements et articles : ce sont des données de référence).
COMPTEURS = [
    "devis", "commande", "ordre_fabrication", "commande_fournisseur", "reception", "livraison", "facture", "facture_fournisseur",
    "envoi_sous_traitance", "retour_sous_traitance",
]


class Command(BaseCommand):
    help = "Vide les documents de test et remet les compteurs à zéro (liste seulement, sauf --confirmer)."

    def add_arguments(self, parser):
        parser.add_argument("--confirmer", action="store_true", help="Supprime vraiment (sinon : simple liste).")
        parser.add_argument("--sauvegarde-faite", action="store_true", help="Atteste qu'une sauvegarde a été faite juste avant (./sauvegarder-nas.sh).")
        parser.add_argument("--oui", action="store_true", help="Ne demande pas de taper SUPPRIMER.")
        parser.add_argument("--stock", action="store_true", help="Vide aussi le stock (lots, mouvements, transferts, inventaires) : à faire si le stock de départ sera ressaisi.")
        parser.add_argument("--articles-fabriques", action="store_true", help="Supprime aussi les articles fabriqués (créés depuis des devis) avec leur nomenclature et leur gamme.")
        parser.add_argument("--journaux", action="store_true", help="Vide aussi le journal des connexions.")

    def modeles(self, options):
        etiquettes = list(DOCUMENTS) + (STOCK if options["stock"] else [])
        if options["journaux"]:
            etiquettes.append("comptes.EvenementConnexion")
        modeles = [apps.get_model(e) for e in etiquettes]
        if options["articles_fabriques"]:
            modeles += [apps.get_model("technique.Gamme"), apps.get_model("technique.Nomenclature")]
        return modeles

    def apercu(self, options):
        lignes = []
        for modele in self.modeles(options):
            lignes.append((modele._meta.label, modele.objects.count()))
        if options["articles_fabriques"]:
            Article = apps.get_model("technique.Article")
            lignes.append(("technique.Article (fabriqués)", Article.objects.filter(nature="fabrique").count()))
        return lignes

    def handle(self, *args, **options):
        lignes = self.apercu(options)
        self.stdout.write("Données qui seraient supprimées :" if not options["confirmer"] else "Suppression de :")
        for etiquette, nombre in lignes:
            self.stdout.write(f"  {etiquette:<45} {nombre:>7}")
        RegleCodification = apps.get_model("codification.RegleCodification")
        self.stdout.write(f"Compteurs remis à zéro : {', '.join(COMPTEURS)}")
        self.stdout.write("Conservé : tiers, adresses, contacts, articles (hors fabriqués sauf --articles-fabriques), matières, familles, postes, tarifs, gammes types, "
                          "paramètres et vitesses de coupe, formats de tôle, règles, plan comptable, journaux, société, modèles de documents, utilisateurs.")
        if not options["stock"]:
            self.stdout.write("Stock conservé (ajoutez --stock pour le vider).")
        if not options["confirmer"]:
            self.stdout.write(self.style.WARNING("Rien n'a été modifié. Relancez avec --confirmer --sauvegarde-faite pour supprimer."))
            return
        if not options["sauvegarde_faite"]:
            raise CommandError("Faites d'abord une sauvegarde (./sauvegarder-nas.sh) puis ajoutez --sauvegarde-faite.")
        if not options["oui"]:
            if input("Tapez SUPPRIMER pour confirmer : ").strip() != "SUPPRIMER":
                raise CommandError("Annulé.")
        with transaction.atomic():
            self.supprimer(options)
            RegleCodification.objects.filter(entite__in=COMPTEURS).update(compteur_actuel=0, annee_compteur=None)
        self.stdout.write(self.style.SUCCESS("Documents de test supprimés, compteurs remis à zéro."))

    def supprimer(self, options):
        Devis, Facture = apps.get_model("chiffrage.Devis"), apps.get_model("facturation.Facture")
        Devis.objects.update(devis_origine=None)  # révisions et avoirs se référencent entre eux (PROTECT)
        Facture.objects.update(facture_origine=None)
        for modele in self.modeles(options):
            modele.objects.all().delete()
            historique = getattr(modele, "history", None)
            if historique is not None:
                historique.all().delete()
        if options["articles_fabriques"]:
            Article = apps.get_model("technique.Article")
            Article.objects.filter(nature="fabrique").delete()
