# Corrige les commandes existantes créées avant l'ajout de CommandeLigne.devis_ligne
# (0008) : relie devis_ligne quand le rapprochement est sûr (un seul candidat par
# article), et recrée toute ligne de commande manquante par rapport au devis
# d'origine — c'est ce second cas qui explique une commande sans aucune ligne
# visible dans l'admin. Ne touche jamais une quantite_commandee déjà enregistrée.

from django.db import migrations


def synchroniser(apps, schema_editor):
    Commande = apps.get_model("chiffrage", "Commande")
    CommandeLigne = apps.get_model("chiffrage", "CommandeLigne")

    for commande in Commande.objects.all():
        devis_lignes_par_article = {}
        for ligne in commande.devis.lignes.all():
            devis_lignes_par_article.setdefault(ligne.article_id, []).append(ligne)

        for commande_ligne in commande.lignes.filter(devis_ligne__isnull=True):
            candidats = devis_lignes_par_article.get(commande_ligne.article_id) or []
            if len(candidats) == 1:
                commande_ligne.devis_ligne = candidats[0]
                commande_ligne.save(update_fields=["devis_ligne"])

        articles_presents = set(commande.lignes.values_list("article_id", flat=True))
        for ligne in commande.devis.lignes.all():
            if ligne.article_id in articles_presents:
                continue
            CommandeLigne.objects.create(
                commande=commande,
                article_id=ligne.article_id,
                quantite_commandee=ligne.quantite,
                devis_ligne=ligne,
            )


def noop(apps, schema_editor):
    # Pas de retour en arrière : on ne sait pas distinguer une ligne créée par
    # cette migration d'une ligne créée normalement par lancer_en_production
    # après coup.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("chiffrage", "0008_commandeligne_date_livraison_prevue_and_more"),
    ]

    operations = [
        migrations.RunPython(synchroniser, noop),
    ]
