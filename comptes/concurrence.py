"""Protection contre les écrasements : deux onglets (ou deux personnes) ouvrent la même fiche,
l'un enregistre, l'autre enregistre ensuite sa version périmée — et efface silencieusement le
travail du premier. Verrouillage optimiste : la fiche porte, dans un champ caché, le numéro de
version qu'elle a affiché (dernière entrée de l'historique) ; à l'enregistrement, si la version
en base a changé entre-temps, l'enregistrement est refusé avec le nom de l'auteur et l'heure.

Réservé aux modèles qui ont un historique (simple-history). Limite connue : seule la fiche
parente est versionnée, pas ses lignes en tableau (le recalcul en direct des lignes de devis
les enregistre au fil de la saisie ; les compter ferait échouer à tort).
"""

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

CHAMP = "version_verrou"


def _dernier_historique(instance):
    historique = getattr(type(instance), "history", None)
    if historique is None or instance.pk is None:
        return None
    return historique.filter(**{instance._meta.pk.name: instance.pk}).order_by("-history_id").first()


def version_de(instance):
    dernier = _dernier_historique(instance)
    return str(dernier.history_id) if dernier is not None else ""


def _message_conflit(instance):
    dernier = _dernier_historique(instance)
    auteur = getattr(dernier, "history_user", None)
    quand = timezone.localtime(dernier.history_date).strftime("%d/%m/%Y à %H:%M") if dernier else "?"
    qui = f"par {auteur}" if auteur else "par un autre utilisateur"
    return (
        f"Cette fiche a été modifiée {qui} le {quand}, après l'ouverture de votre page. "
        "Votre enregistrement a été refusé pour ne pas effacer ces changements : rechargez la fiche "
        "(en copiant au besoin votre saisie), puis recommencez."
    )


class VerrouOptimisteMixin:
    """À placer avant ModelAdmin : ajoute un champ caché de version aux fiches de modification."""

    def get_fields(self, request, obj=None):
        champs = list(super().get_fields(request, obj))
        if obj is not None and hasattr(type(obj), "history") and CHAMP not in champs:
            champs.append(CHAMP)
        return champs

    def get_form(self, request, obj=None, **kwargs):
        if obj is None or not hasattr(type(obj), "history"):
            return super().get_form(request, obj, **kwargs)
        version_affichee = version_de(obj)

        def clean(form):
            cleaned = super(form_verrou, form).clean()
            soumise = form.data.get(form.add_prefix(CHAMP), "")
            if form.instance.pk and soumise and soumise != version_de(form.instance):
                raise ValidationError(_message_conflit(form.instance))
            return cleaned

        base = kwargs.pop("form", None) or self.form
        form_verrou = type(
            "FormVerrou",
            (base,),
            {CHAMP: forms.CharField(required=False, widget=forms.HiddenInput, initial=version_affichee, label=""), "clean": clean},
        )
        return super().get_form(request, obj, form=form_verrou, **kwargs)
