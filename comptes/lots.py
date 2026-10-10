"""Modification et création par lots.

Principe commun à tous les écrans « Modifier par lots… » : on choisit des objets dans une liste, on coche les champs à changer et
leur nouvelle valeur (remplacer, augmenter de x %, ajouter x), un **aperçu avant → après** s'affiche pour chaque objet (les cas
impossibles ou invalides sont signalés, rien n'est écrit), puis « Appliquer » enregistre tout dans une transaction. Chaque lot est
tracé dans `LotModification` (qui, quand, anciennes valeurs) et peut être annulé tant que les valeurs n'ont pas bougé depuis.

Les champs modifiables d'un modèle se déclarent par des `ChampLot` ; un calcul propre (conversion €/kg ↔ €/m², par exemple)
se branche par `calcul`. Les modèles qui gardent des périodes (tarifs, gammes) passent par `appliquer_periode` : l'ancienne ligne est
close la veille de la date d'effet et une nouvelle ligne la remplace, l'historique reste intact."""

import datetime
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import ProtectedError
from django.db.utils import IntegrityError
from django.utils import timezone

from .models import LotModification

MODES = (("fixe", "Remplacer par"), ("pct", "Augmenter de (%)"), ("ajout", "Ajouter"))


class Inapplicable(Exception):
    """Ce champ ne peut pas être calculé pour cet objet (pas de valeur actuelle, conversion impossible…) : l'objet est ignoré."""


@dataclass
class ChampLot:
    nom: str  # champ du modèle (clé pour `calcul` quand il remplace le calcul par défaut)
    libelle: str
    genre: str = "nombre"  # nombre | choix | tristate | fk | m2m | texte | date
    aide: str = ""
    choix: object = None  # [(valeur, libellé)] ou fonction sans argument
    queryset: object = None  # fonction sans argument -> QuerySet (fk, m2m)
    modes: tuple = ("fixe", "pct", "ajout")
    unites: object = None  # [(code, libellé)] : unité dans laquelle la valeur est saisie (transmise à `calcul`)
    calcul: object = None  # (objet, valeur, mode, unite) -> {champ: nouvelle valeur}
    vide_permis: bool = False  # fk / choix : « (aucun) » est une valeur valide

    def liste_choix(self):
        return self.choix() if callable(self.choix) else (self.choix or [])

    def objets(self):
        return list(self.queryset()) if self.queryset else []


def _champ(modele, nom):
    return modele._meta.get_field(nom)


def _json(valeur):
    if isinstance(valeur, Decimal):
        return str(valeur)
    if isinstance(valeur, (datetime.date, datetime.datetime)):
        return valeur.isoformat()
    return valeur


def _valeur_champ(objet, nom):
    champ = _champ(type(objet), nom)
    if champ.many_to_many:
        return sorted(str(p) for p in getattr(objet, nom).values_list("pk", flat=True)) if objet.pk else []
    return _json(champ.value_from_object(objet))


def _egal(a, b):
    try:
        return Decimal(str(a)) == Decimal(str(b))
    except (InvalidOperation, ValueError):
        return a == b


def _vers_python(champ, valeur):
    if valeur is None:
        return None
    if champ.is_relation and not champ.many_to_many:
        return champ.target_field.to_python(valeur)
    return champ.to_python(valeur)


def _arrondi(champ, valeur):
    type_champ = champ.get_internal_type()
    if type_champ == "DecimalField":
        return Decimal(str(valeur)).quantize(Decimal(1).scaleb(-champ.decimal_places), rounding=ROUND_HALF_UP)
    if type_champ == "FloatField":
        return round(float(valeur), 6)
    return valeur


def _nombre(texte):
    try:
        return Decimal(str(texte).replace(",", ".").strip())
    except InvalidOperation:
        raise Inapplicable("valeur numérique invalide")


def calculer(spec, objet, valeur, mode="fixe", unite=None):
    """Nouvelles valeurs {champ: valeur} de `objet` pour ce champ de lot (lève Inapplicable)."""
    if spec.calcul is not None:
        return spec.calcul(objet, valeur, mode, unite)
    modele = type(objet)
    champ = _champ(modele, spec.nom)
    if spec.genre == "nombre":
        v = _nombre(valeur)
        ancien = getattr(objet, spec.nom)
        if mode != "fixe" and ancien is None:
            raise Inapplicable(f"{spec.libelle} : pas de valeur actuelle")
        ancien_d = Decimal(str(ancien)) if ancien is not None else None
        nouveau = v if mode == "fixe" else ancien_d * (1 + v / 100) if mode == "pct" else ancien_d + v
        return {spec.nom: _arrondi(champ, nouveau)}
    if spec.genre == "tristate":
        return {spec.nom: {"oui": True, "non": False}.get(valeur)}
    if spec.genre == "fk":
        if valeur in (None, ""):
            if not spec.vide_permis:
                raise Inapplicable(f"{spec.libelle} : choisissez une valeur")
            return {spec.nom: None}
        cible = champ.related_model.objects.filter(pk=valeur).first()
        if cible is None:
            raise Inapplicable(f"{spec.libelle} : valeur inconnue")
        return {spec.nom: cible}
    if spec.genre == "m2m":
        return {spec.nom: sorted(str(p) for p in (valeur or []))}
    if spec.genre == "date":
        try:
            return {spec.nom: datetime.date.fromisoformat(valeur) if valeur else None}
        except ValueError:
            raise Inapplicable(f"{spec.libelle} : date invalide")
    if valeur == "" and spec.vide_permis:
        return {spec.nom: None if champ.null else ""}
    return {spec.nom: _vers_python(champ, valeur)}


def _afficher(objet, nom, valeur):
    champ = _champ(type(objet), nom)
    if champ.many_to_many:
        cibles = champ.related_model.objects.filter(pk__in=valeur or [])
        return ", ".join(str(c) for c in cibles) or "—"
    if valeur is None or valeur == "":
        return "—"
    if champ.choices:
        return dict(champ.flatchoices).get(valeur, str(valeur))
    if isinstance(valeur, bool):
        return "Oui" if valeur else "Non"
    if hasattr(valeur, "pk"):
        return str(valeur)
    if champ.is_relation:
        return str(champ.related_model.objects.filter(pk=valeur).first() or valeur)
    if isinstance(valeur, Decimal):
        return format(valeur.normalize(), "f")
    return str(valeur)


def _noms_modifies(resultats):
    return [n for r in resultats for n in r]


def preparer(modele, queryset, choix, periode=None):
    """Aperçu : une ligne par objet {objet, changements [(libellé, avant, après)], nouveau {champ: valeur}, erreur, ignore}.
    `choix` : [(ChampLot, valeur, mode, unité)] des champs cochés. Rien n'est enregistré."""
    lignes = []
    for objet in queryset:
        ligne = {"objet": objet, "changements": [], "nouveau": {}, "erreur": "", "ignore": ""}
        if periode is not None:
            if objet.date_debut > periode or (objet.date_fin is not None and objet.date_fin < periode):
                ligne["ignore"] = "pas en vigueur à cette date"
            elif getattr(objet, "origine", "") == "decoupe":
                ligne["ignore"] = "calculée depuis la pièce"
        if ligne["ignore"]:
            lignes.append(ligne)
            continue
        try:
            for spec, valeur, mode, unite in choix:
                ligne["nouveau"].update(calculer(spec, objet, valeur, mode, unite))
        except Inapplicable as exc:
            ligne["ignore"] = str(exc)
        else:
            libelles = {s.nom: s.libelle for s, *_ in choix}
            for nom, nouveau in ligne["nouveau"].items():
                champ = _champ(modele, nom)
                avant = _valeur_champ(objet, nom)
                apres = nouveau if champ.many_to_many else _json(nouveau.pk if hasattr(nouveau, "pk") else nouveau)
                if _egal(avant, apres):
                    continue
                libelle = libelles.get(nom) or str(champ.verbose_name).capitalize()
                ligne["changements"].append((libelle, _afficher(objet, nom, avant if champ.many_to_many else getattr(objet, nom)), _afficher(objet, nom, nouveau)))
            if not ligne["changements"]:
                ligne["ignore"] = "rien à changer"
            elif periode is None:
                ligne["erreur"] = _valider(objet, ligne["nouveau"])
        lignes.append(ligne)
    return lignes


def _poser(objet, nouveau):
    for nom, valeur in nouveau.items():
        champ = _champ(type(objet), nom)
        if champ.many_to_many:
            continue
        setattr(objet, nom, valeur)


def _valider(objet, nouveau):
    """Message d'erreur si l'objet, une fois modifié, est invalide ; l'objet est remis en l'état."""
    modele = type(objet)
    avant = {n: getattr(objet, _champ(modele, n).attname) for n in nouveau if not _champ(modele, n).many_to_many}
    try:
        _poser(objet, nouveau)
        objet.full_clean()
    except ValidationError as exc:
        return " ; ".join(exc.messages)
    finally:
        for n, v in avant.items():
            setattr(objet, _champ(modele, n).attname, v)
    return ""


def _enregistrer_lot(utilisateur, modele, description, modifications, crees=()):
    return LotModification.objects.create(
        utilisateur=utilisateur if getattr(utilisateur, "pk", None) else None, modele=modele._meta.label,
        description=description[:255], modifications=modifications, crees=[list(c) for c in crees],
    )


@transaction.atomic
def appliquer(modele, lignes, utilisateur, description):
    """Enregistre les lignes valides de `preparer` (ni ignorées ni en erreur) et trace le lot. Renvoie (lot, nombre)."""
    modifications = []
    for ligne in lignes:
        if ligne["ignore"] or ligne["erreur"]:
            continue
        objet, nouveau = ligne["objet"], ligne["nouveau"]
        avant = {n: _valeur_champ(objet, n) for n in nouveau}
        _poser(objet, nouveau)
        objet.save()
        for nom, valeur in nouveau.items():
            if _champ(modele, nom).many_to_many:
                getattr(objet, nom).set(valeur)
        apres = {n: _valeur_champ(objet, n) for n in nouveau}
        modifications.append({"pk": str(objet.pk), "avant": avant, "apres": apres})
    if not modifications:
        return None, 0
    return _enregistrer_lot(utilisateur, modele, description, modifications), len(modifications)


@transaction.atomic
def appliquer_periode(modele, queryset, choix, date_effet, utilisateur, description):
    """Modification avec effet à `date_effet` pour un modèle à périodes (date_debut, date_fin) : la ligne active est close la veille et
    remplacée par une copie modifiée qui commence à `date_effet` (modifiée sur place si elle commence ce jour-là). Renvoie
    (lot, nombre, ignorés [(objet, motif)])."""
    veille = date_effet - datetime.timedelta(days=1)
    modifications, crees, ignores = [], [], []
    for objet in queryset:
        if objet.date_debut > date_effet or (objet.date_fin is not None and objet.date_fin < date_effet):
            ignores.append((objet, "pas en vigueur à cette date"))
            continue
        if getattr(objet, "origine", "") == "decoupe":
            ignores.append((objet, "calculée depuis la pièce"))
            continue
        try:
            nouveau = {}
            for spec, valeur, mode, unite in choix:
                nouveau.update(calculer(spec, objet, valeur, mode, unite))
        except Inapplicable as exc:
            ignores.append((objet, str(exc)))
            continue
        avant = {n: _valeur_champ(objet, n) for n in nouveau}
        if all(_egal(avant[n], _json(v.pk if hasattr(v, "pk") else v)) for n, v in nouveau.items()):
            ignores.append((objet, "rien à changer"))
            continue
        try:
            with transaction.atomic():  # une ligne invalide est ignorée sans défaire les autres
                if objet.date_debut == date_effet:
                    _poser(objet, nouveau)
                    objet.full_clean()
                    objet.save()
                    modifications.append({"pk": str(objet.pk), "avant": avant, "apres": {n: _valeur_champ(objet, n) for n in nouveau}})
                    continue
                ancienne_fin = objet.date_fin
                objet.date_fin = veille
                objet.save(update_fields=["date_fin"])
                copie = type(objet).objects.get(pk=objet.pk)
                copie.pk = None
                copie._state.adding = True
                copie.date_debut, copie.date_fin = date_effet, ancienne_fin
                _poser(copie, nouveau)
                copie.full_clean()
                copie.save()
                modifications.append({"pk": str(objet.pk), "avant": {"date_fin": _json(ancienne_fin)}, "apres": {"date_fin": veille.isoformat()}})
                crees.append((modele._meta.label, str(copie.pk)))
        except ValidationError as exc:
            objet.refresh_from_db()
            ignores.append((objet, " ; ".join(exc.messages)))
    if not modifications:
        return None, 0, ignores
    lot = _enregistrer_lot(utilisateur, modele, description, modifications, crees)
    return lot, len(modifications), ignores


@transaction.atomic
def annuler(lot):
    """Annule un lot : remet les anciennes valeurs (seulement si elles n'ont pas changé depuis) et supprime les objets créés
    s'ils ne sont référencés nulle part. Renvoie (objets restaurés, objets non restaurés [motif])."""
    if lot.annule_le:
        return 0, ["lot déjà annulé"]
    modele = apps.get_model(lot.modele)
    faits, refus = 0, []
    for etiquette, cle in reversed(lot.crees):  # le dernier créé d'abord : une tôle avant sa matière
        cible = apps.get_model(etiquette).objects.filter(pk=cle).first()
        if cible is None:
            continue
        try:
            with transaction.atomic():
                cible.delete()
            faits += 1
        except (ProtectedError, IntegrityError):
            refus.append(f"{cible} : utilisé ailleurs, conservé")
    for entree in lot.modifications:
        objet = modele.objects.filter(pk=entree["pk"]).first()
        if objet is None:
            refus.append(f"{entree['pk']} : supprimé depuis")
            continue
        if any(not _egal(_valeur_champ(objet, n), v) for n, v in entree["apres"].items()):
            refus.append(f"{objet} : modifié depuis, laissé tel quel")
            continue
        for nom, valeur in entree["avant"].items():
            champ = _champ(modele, nom)
            if champ.many_to_many:
                continue
            setattr(objet, champ.attname, _vers_python(champ, valeur))
        objet.save()
        for nom, valeur in entree["avant"].items():
            if _champ(modele, nom).many_to_many:
                getattr(objet, nom).set(valeur)
        faits += 1
    lot.annule_le = timezone.now()
    lot.save(update_fields=["annule_le"])
    return faits, refus
