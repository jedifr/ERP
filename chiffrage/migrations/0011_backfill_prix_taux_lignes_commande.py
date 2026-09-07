# Avant 0010, prix_vente_unitaire/taux_tva sur une CommandeLigne n'étaient pas
# des champs mais des propriétés qui lisaient devis_ligne — sans backfill, toute
# ligne existante se retrouverait avec ces nouveaux champs vides après migration.
# Reprend la valeur du devis pour toute ligne déjà reliée (devis_ligne non nul) :
# c'est exactement la valeur de départ que production.lancer_en_production leur
# aurait donnée si elles avaient été créées après ce changement.

from django.db import migrations


def backfill(apps, schema_editor):
    CommandeLigne = apps.get_model("chiffrage", "CommandeLigne")
    for ligne in CommandeLigne.objects.filter(devis_ligne__isnull=False, prix_vente_unitaire__isnull=True):
        devis_ligne = ligne.devis_ligne
        if devis_ligne.prix_vente_matiere is None:
            continue
        prix_vente_operations = sum(
            op.prix_vente or 0 for op in devis_ligne.operations.all()
        )
        prix_vente_total = devis_ligne.prix_vente_matiere + prix_vente_operations
        prix_vente_unitaire = prix_vente_total / devis_ligne.quantite if devis_ligne.quantite else None
        ligne.prix_vente_unitaire = prix_vente_unitaire
        ligne.taux_tva_id = devis_ligne.taux_tva_id
        ligne.save(update_fields=["prix_vente_unitaire", "taux_tva"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("chiffrage", "0010_commandeligne_designation_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill, noop),
    ]
