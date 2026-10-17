"""Commande d'un devis venu du site : ses ordres de fabrication se créent tout seuls (décision de Marc du 17/10 :
découpe et pliage sont des opérations simples, envoyées sans relecture)."""

import logging

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)


@receiver(post_save, sender="chiffrage.Commande", dispatch_uid="web_ordres_fabrication_auto")
def ordres_fabrication_auto(sender, instance, created, **kwargs):
    if not created or not instance.devis_id:
        return
    from .models import DemandeWeb

    if not DemandeWeb.objects.filter(devis_id=instance.devis_id).exists():
        return

    def creer():  # après la transaction : les lignes de commande sont créées après la commande elle-même
        from chiffrage.moteur import ChiffrageError
        from chiffrage.production import creer_ordres_fabrication

        try:
            creer_ordres_fabrication(instance)
        except ChiffrageError as exc:  # rien à fabriquer, commande annulée : la commande reste valable
            logger.info("Commande %s : pas d'ordre de fabrication automatique (%s)", instance.pk, exc)

    transaction.on_commit(creer)
