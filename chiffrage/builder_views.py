import datetime
import json

from django import forms
from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from commercial.models import Adresse, Contact, TauxTVA, Tiers
from technique.models import Article, PosteTravail

from .builder import ajouter_ligne_devis, creer_article_fabrique, erreur_lisible
from .models import Commande, CommandeLigne, Devis, DevisLigne
from .moteur import ChiffrageError, calculer_ligne, previsualiser_ligne, previsualiser_ligne_commande, resoudre_taux_tva
from .production import lancer_en_production


@staff_member_required
@require_http_methods(["GET", "POST"])
def devis_builder_view(request, numero):
    devis = get_object_or_404(Devis, pk=numero)

    if request.method == "POST":
        return _traiter_ajout_ligne(request, devis)

    context = admin.site.each_context(request)
    context.update(
        {
            "title": f"Constructeur de devis — {devis.numero}",
            "devis": devis,
            "lignes": devis.lignes.select_related("article").prefetch_related("operations__poste"),
            "postes": PosteTravail.objects.all().order_by("nom"),
            "opts": Devis._meta,
        }
    )
    return TemplateResponse(request, "chiffrage/devis_builder.html", context)


@staff_member_required
@require_http_methods(["POST"])
def convertir_en_commande_view(request, numero):
    """Bouton "Convertir en commande" (object-tools de la fiche Devis) :
    appelle lancer_en_production directement depuis la fiche déjà ouverte,
    en un clic — même logique que l'action d'admin "Lancer en production"
    de la liste des devis, juste plus directe. L'ordre des lignes de la
    commande créée reprend celui des lignes du devis (DevisLigne.ordre,
    modifiable par glisser-déposer sur cette même fiche)."""
    devis = get_object_or_404(Devis, pk=numero)
    url_devis = reverse("admin:chiffrage_devis_change", args=[numero])

    try:
        commande = lancer_en_production(devis)
    except ChiffrageError as exc:
        messages.error(request, str(exc))
        return redirect(url_devis)

    messages.success(request, f"Commande {commande.numero} créée.")
    return redirect(reverse("admin:chiffrage_commande_change", args=[commande.pk]))


def _traiter_ajout_ligne(request, devis):
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"detail": "JSON invalide."}, status=400)

    try:
        quantite = float(payload["quantite"])
    except (KeyError, TypeError, ValueError):
        return JsonResponse({"detail": "Quantité manquante ou invalide."}, status=400)

    # Tout ou rien : si le prix de la ligne ne peut pas être calculé (ex.
    # matière choisie sans coût unitaire renseigné), on ne doit RIEN valider
    # — ni le nouvel article, ni sa nomenclature/gamme, ni la ligne de devis
    # — plutôt que de laisser passer une ligne au chiffrage inconnu avec un
    # simple avertissement. calculer_ligne() (et non calculer_devis()) pour
    # ne juger que la ligne qu'on est en train d'ajouter, indépendamment de
    # l'état d'éventuelles autres lignes déjà présentes sur ce devis.
    try:
        with transaction.atomic():
            if payload.get("nouvel_article"):
                article = _creer_article_depuis_payload(payload["nouvel_article"])
            else:
                reference = payload.get("article_existant")
                try:
                    article = Article.objects.get(pk=reference)
                except Article.DoesNotExist:
                    raise ChiffrageError(f"Article « {reference} » introuvable.")

            ligne = ajouter_ligne_devis(devis, article, quantite)
            calculer_ligne(devis, ligne)
    except ChiffrageError as exc:
        return JsonResponse({"detail": str(exc)}, status=400)

    ligne.refresh_from_db()

    return JsonResponse(
        {
            "ok": True,
            "ligne_id": ligne.id,
            "article": article.reference,
            "quantite": ligne.quantite,
            "cout_matiere_calcule": ligne.cout_matiere_calcule,
            "prix_vente_matiere": ligne.prix_vente_matiere,
            "prix_vente_operations": ligne.prix_vente_operations,
            "prix_vente_total": ligne.prix_vente_total,
            "prix_vente_unitaire": ligne.prix_vente_unitaire,
            "prix_vente_ttc": ligne.prix_vente_ttc,
            "montant_total_ht": devis.montant_total_ht,
            "montant_total_ttc": devis.montant_total_ttc,
        }
    )


def _creer_article_depuis_payload(data):
    try:
        reference = data["reference"]
    except KeyError:
        raise ChiffrageError("Référence du nouvel article manquante.")

    composants = []
    for c in data.get("composants", []):
        try:
            composant_article = Article.objects.get(pk=c["article_composant"])
        except Article.DoesNotExist:
            raise ChiffrageError(f"Composant « {c.get('article_composant')} » introuvable.")
        composants.append(
            {
                "article_composant": composant_article,
                "quantite": c.get("quantite"),
                "longueur_mm": c.get("longueur_mm") or None,
                "largeur_mm": c.get("largeur_mm") or None,
            }
        )

    etapes = []
    for e in data.get("etapes", []):
        try:
            poste = PosteTravail.objects.get(pk=e["poste"])
        except PosteTravail.DoesNotExist:
            raise ChiffrageError(f"Poste « {e.get('poste')} » introuvable.")
        date_debut = e.get("date_debut") or datetime.date.today().isoformat()
        etapes.append(
            {
                "poste": poste,
                "ordre": e.get("ordre"),
                "temps_fixe": e.get("temps_fixe") or None,
                "temps_variable": e.get("temps_variable") or None,
                "cout_forfaitaire": e.get("cout_forfaitaire") or None,
                "date_debut": datetime.date.fromisoformat(date_debut),
            }
        )

    return creer_article_fabrique(
        reference=reference,
        libelle=(data.get("libelle") or "").strip(),
        taux_marge_defaut=data.get("taux_marge_defaut") or None,
        composants=composants,
        etapes=etapes,
    )


class _ValeurInvalide(Exception):
    def __init__(self, message):
        self.message = message


def _parse_float_optionnel(payload, champ, message_erreur):
    """Lit `champ` dans `payload` s'il est présent : None/"" -> None, sinon float().
    Lève _ValeurInvalide si la conversion échoue. Ne touche rien si absent du payload
    (permet de ne modifier que les champs effectivement envoyés)."""
    if champ not in payload:
        return None, False
    valeur = payload[champ]
    try:
        return (float(valeur) if valeur not in (None, "") else None), True
    except (TypeError, ValueError):
        raise _ValeurInvalide(message_erreur)


def _parse_fk_optionnel(payload, champ, queryset, message_erreur):
    """Lit l'identifiant de `champ` dans `payload` s'il est présent : None/"" ->
    None (pas de relation), sinon résout l'objet via `queryset`. Lève
    _ValeurInvalide si l'identifiant est invalide ou introuvable — évite de
    laisser passer un id inexistant jusqu'à l'IntegrityError au save() (une
    FK n'est pas vérifiée par full_clean()). Ne touche rien si `champ` est
    absent du payload."""
    if champ not in payload:
        return None, False
    valeur = payload[champ]
    if valeur in (None, ""):
        return None, True
    try:
        return queryset.get(pk=valeur), True
    except (queryset.model.DoesNotExist, ValueError, TypeError):
        raise _ValeurInvalide(message_erreur)


@staff_member_required
@require_http_methods(["POST"])
def recalculer_ligne_view(request, numero, ligne_id):
    """Met à jour une ligne de devis (quantité / taux de marge / prix unitaire
    forcé) et recalcule le devis en direct — utilisé par le JS de la fiche
    Devis (recalcul en temps réel, sans passer par l'action admin
    "Recalculer le chiffrage")."""
    devis = get_object_or_404(Devis, pk=numero)
    ligne = get_object_or_404(DevisLigne, pk=ligne_id, devis=devis)

    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"detail": "JSON invalide."}, status=400)

    try:
        quantite, fourni = _parse_float_optionnel(payload, "quantite", "Quantité invalide.")
        if fourni:
            ligne.quantite = quantite

        taux, fourni = _parse_float_optionnel(payload, "taux_marge_matiere_applique", "Taux de marge invalide.")
        if fourni:
            ligne.taux_marge_matiere_applique = taux

        prix_force, fourni = _parse_float_optionnel(
            payload, "prix_vente_unitaire_force", "Prix unitaire forcé invalide."
        )
        if fourni:
            ligne.prix_vente_unitaire_force = prix_force

        taux_tva, fourni = _parse_fk_optionnel(
            payload, "taux_tva", TauxTVA.objects.all(), "Taux de TVA introuvable."
        )
        if fourni:
            ligne.taux_tva = taux_tva
    except _ValeurInvalide as exc:
        return JsonResponse({"detail": exc.message}, status=400)

    try:
        ligne.full_clean()
    except Exception as exc:
        return JsonResponse({"detail": erreur_lisible(exc)}, status=400)
    ligne.save()

    try:
        calculer_ligne(devis, ligne)
    except ChiffrageError as exc:
        return JsonResponse({"detail": str(exc)}, status=400)

    ligne.refresh_from_db()
    return JsonResponse(
        {
            "ok": True,
            "cout_matiere_calcule": ligne.cout_matiere_calcule,
            "prix_vente_matiere": ligne.prix_vente_matiere,
            "taux_marge_matiere_applique": ligne.taux_marge_matiere_applique,
            "prix_vente_operations": ligne.prix_vente_operations,
            "prix_vente_total": ligne.prix_vente_total,
            "prix_vente_unitaire": ligne.prix_vente_unitaire,
            "prix_vente_ttc": ligne.prix_vente_ttc,
            "montant_matiere_ht": devis.montant_matiere_ht,
            "montant_operations_ht": devis.montant_operations_ht,
            "montant_total_ht": devis.montant_total_ht,
            "montant_total_ttc": devis.montant_total_ttc,
        }
    )


def _lire_payload_previsualisation(payload):
    """Lit et valide les champs communs à un aperçu de ligne (article,
    quantité, taux de marge, prix forcé, taux de TVA). Lève _ValeurInvalide
    (avec un message déjà prêt pour la réponse 400) si l'un d'eux est
    manquant/invalide. Partagé entre previsualiser_ligne_view (devis déjà
    enregistré) et previsualiser_ligne_nouveau_devis_view (devis pas encore
    enregistré)."""
    reference = payload.get("article")
    try:
        article = Article.objects.get(pk=reference)
    except Article.DoesNotExist:
        raise _ValeurInvalide(f"Article « {reference} » introuvable.")

    try:
        quantite = float(payload["quantite"])
    except (KeyError, TypeError, ValueError):
        raise _ValeurInvalide("Quantité invalide.")

    taux, _fourni = _parse_float_optionnel(payload, "taux_marge_matiere_applique", "Taux de marge invalide.")
    prix_force, _fourni = _parse_float_optionnel(
        payload, "prix_vente_unitaire_force", "Prix unitaire forcé invalide."
    )
    taux_tva, _fourni = _parse_fk_optionnel(payload, "taux_tva", TauxTVA.objects.all(), "Taux de TVA introuvable.")

    return article, quantite, taux, prix_force, taux_tva


@staff_member_required
@require_http_methods(["POST"])
def previsualiser_ligne_view(request, numero):
    """Aperçu du coût/prix d'une ligne pas encore enregistrée (article +
    quantité tout juste saisis dans une nouvelle ligne de l'inline, sur la
    fiche d'un devis déjà enregistré) — ne persiste rien, contrairement à
    recalculer_ligne_view. Pour un devis lui-même pas encore enregistré (le
    formulaire d'ajout), voir previsualiser_ligne_nouveau_devis_view."""
    devis = get_object_or_404(Devis, pk=numero)

    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"detail": "JSON invalide."}, status=400)

    try:
        article, quantite, taux, prix_force, taux_tva = _lire_payload_previsualisation(payload)
    except _ValeurInvalide as exc:
        return JsonResponse({"detail": exc.message}, status=400)

    try:
        resultat = previsualiser_ligne(
            devis,
            article,
            quantite,
            taux_marge_matiere_applique=taux,
            prix_vente_unitaire_force=prix_force,
            taux_tva=taux_tva,
        )
    except ChiffrageError as exc:
        return JsonResponse({"detail": str(exc)}, status=400)

    return JsonResponse({"ok": True, **resultat})


def _contact_json(contact):
    return {"id": contact.pk, "texte": str(contact)} if contact else None


@staff_member_required
@require_http_methods(["GET"])
def valeurs_defaut_tiers_view(request, code):
    """Adresse de facturation, adresse de livraison et contact par défaut
    pour le tiers `code` — utilisé pour pré-remplir ces champs dès qu'un
    client est sélectionné sur la fiche Devis (formulaire d'ajout comme de
    modification), sans écraser un choix déjà fait par l'utilisateur (c'est
    le JS appelant qui décide de ça, pas cette vue).

    Le contact suit un ordre de priorité : celui associé à l'adresse de
    livraison par défaut (Contact.adresse_associee), sinon le contact
    principal du tiers (Contact.est_principal) — voir
    contact_associe_adresse_view() pour le même choix quand l'utilisateur
    change l'adresse de livraison après coup, indépendamment du client."""
    tiers = get_object_or_404(Tiers, pk=code)

    facturation = Adresse.objects.filter(tiers=tiers, est_facturation=True, est_principale=True).first()
    livraison = Adresse.objects.filter(tiers=tiers, est_livraison=True, est_principale=True).first()

    contact = None
    if livraison is not None:
        contact = Contact.objects.filter(adresse_associee=livraison).first()
    if contact is None:
        contact = Contact.objects.filter(tiers=tiers, est_principal=True).first()

    return JsonResponse(
        {
            "adresse_facturation": (
                {"id": facturation.pk, "texte": str(facturation)} if facturation else None
            ),
            "adresse_livraison": ({"id": livraison.pk, "texte": str(livraison)} if livraison else None),
            "contact": _contact_json(contact),
        }
    )


@staff_member_required
@require_http_methods(["GET"])
def contact_associe_adresse_view(request, adresse_id):
    """Contact associé à une adresse de livraison précise
    (Contact.adresse_associee) — utilisé quand l'utilisateur change
    l'adresse de livraison d'un devis après coup (indépendamment de la
    sélection du client, qui passe par valeurs_defaut_tiers_view ci-dessus)
    : propose alors le contact sur place à cette adresse, s'il y en a un."""
    adresse = get_object_or_404(Adresse, pk=adresse_id)
    contact = Contact.objects.filter(adresse_associee=adresse).first()
    return JsonResponse({"contact": _contact_json(contact)})


@staff_member_required
@require_http_methods(["POST"])
def previsualiser_ligne_nouveau_devis_view(request):
    """Même aperçu que previsualiser_ligne_view, mais pour un devis PAS
    ENCORE enregistré (formulaire d'ajout d'un nouveau Devis) : il n'y a donc
    ni `numero` pour construire l'URL, ni objet Devis en base. Le contexte
    normalement lu sur l'objet (date de création, taux de marge globale) est
    fourni directement dans le payload par le JS (valeurs actuelles du
    formulaire) ; previsualiser_ligne() n'a besoin que de lectures d'attributs
    sur `devis`, jamais d'une requête le concernant — un Devis en mémoire,
    jamais enregistré, suffit donc comme contexte."""
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"detail": "JSON invalide."}, status=400)

    try:
        article, quantite, taux, prix_force, taux_tva = _lire_payload_previsualisation(payload)
    except _ValeurInvalide as exc:
        return JsonResponse({"detail": exc.message}, status=400)

    date_creation_str = payload.get("date_creation")
    if date_creation_str:
        # Le widget de date de l'admin soumet sa valeur selon le format local
        # (ex. JJ/MM/AAAA en fr-fr), pas nécessairement l'ISO strict attendu
        # par date.fromisoformat() — on utilise donc le DateField de Django,
        # qui connaît DATE_INPUT_FORMATS et accepte aussi bien l'ISO.
        try:
            date_creation = forms.DateField().clean(date_creation_str.strip())
        except ValidationError:
            return JsonResponse({"detail": "Date de création invalide."}, status=400)
    else:
        date_creation = datetime.date.today()

    try:
        taux_marge_globale, _fourni = _parse_float_optionnel(
            payload, "taux_marge_globale", "Taux de marge globale invalide."
        )
    except _ValeurInvalide as exc:
        return JsonResponse({"detail": exc.message}, status=400)

    devis_provisoire = Devis(date_creation=date_creation, taux_marge_globale=taux_marge_globale)

    try:
        resultat = previsualiser_ligne(
            devis_provisoire,
            article,
            quantite,
            taux_marge_matiere_applique=taux,
            prix_vente_unitaire_force=prix_force,
            taux_tva=taux_tva,
        )
    except ChiffrageError as exc:
        return JsonResponse({"detail": str(exc)}, status=400)

    return JsonResponse({"ok": True, **resultat})


def _taux_tva_suggere_json(article, client):
    """Suggestion de taux de TVA (article + régime fiscal du client),
    jamais appliquée d'autorité : le JS ne la propose que si le champ
    "Taux de TVA" de la ligne est encore vide (voir commande_admin_live.js),
    exactement comme les autres valeurs par défaut de ce projet."""
    if client is None:
        return None
    try:
        taux = resoudre_taux_tva(article, client)
    except ChiffrageError:
        return None
    return {"id": taux.pk, "texte": str(taux), "taux": taux.taux}


@staff_member_required
@require_http_methods(["POST"])
def recalculer_ligne_commande_view(request, numero, ligne_id):
    """Met à jour une ligne de commande (quantité / prix unitaire / taux de
    TVA) — utilisé par le JS de la fiche Commande pour le recalcul en
    temps réel. Le prix n'est recalculé automatiquement depuis la
    quantité/gamme/nomenclature que pour une ligne SANS devis d'origine :
    une ligne héritée d'un devis (devis_ligne renseigné) est une surcharge
    (voir CommandeLigne), jamais réécrite toute seule — seule une valeur
    explicitement envoyée par l'utilisateur y est appliquée."""
    commande = get_object_or_404(Commande, pk=numero)
    ligne = get_object_or_404(CommandeLigne, pk=ligne_id, commande=commande)

    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"detail": "JSON invalide."}, status=400)

    try:
        quantite, fourni = _parse_float_optionnel(payload, "quantite_commandee", "Quantité invalide.")
        if fourni:
            ligne.quantite_commandee = quantite

        prix, prix_fourni = _parse_float_optionnel(
            payload, "prix_vente_unitaire", "Prix unitaire invalide."
        )
        if prix_fourni:
            ligne.prix_vente_unitaire = prix

        taux_tva, fourni = _parse_fk_optionnel(
            payload, "taux_tva", TauxTVA.objects.all(), "Taux de TVA introuvable."
        )
        if fourni:
            ligne.taux_tva = taux_tva
    except _ValeurInvalide as exc:
        return JsonResponse({"detail": exc.message}, status=400)

    if ligne.devis_ligne_id is None and not prix_fourni and ligne.quantite_commandee:
        try:
            resultat = previsualiser_ligne_commande(
                ligne.article, ligne.quantite_commandee, commande.date_commande
            )
        except ChiffrageError as exc:
            return JsonResponse({"detail": str(exc)}, status=400)
        ligne.prix_vente_unitaire = resultat["prix_vente_unitaire"]

    try:
        ligne.full_clean()
    except Exception as exc:
        return JsonResponse({"detail": erreur_lisible(exc)}, status=400)
    ligne.save()

    ligne.refresh_from_db()
    return JsonResponse(
        {
            "ok": True,
            "prix_vente_unitaire": ligne.prix_vente_unitaire,
            "montant_ht": ligne.montant_ht,
            "montant_ttc": ligne.montant_ttc,
            "taux_tva_suggere": _taux_tva_suggere_json(ligne.article, commande.client),
        }
    )


@staff_member_required
@require_http_methods(["POST"])
def previsualiser_ligne_commande_view(request, numero):
    """Aperçu du prix d'une ligne de commande pas encore enregistrée (article
    + quantité tout juste saisis dans une nouvelle ligne de l'inline, sur la
    fiche d'une commande déjà enregistrée) — ne persiste rien. Pour une
    commande elle-même pas encore enregistrée (formulaire d'ajout), voir
    previsualiser_ligne_nouvelle_commande_view."""
    commande = get_object_or_404(Commande, pk=numero)

    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"detail": "JSON invalide."}, status=400)

    reference = payload.get("article")
    try:
        article = Article.objects.get(pk=reference)
    except Article.DoesNotExist:
        return JsonResponse({"detail": f"Article « {reference} » introuvable."}, status=400)

    try:
        quantite = float(payload["quantite"])
    except (KeyError, TypeError, ValueError):
        return JsonResponse({"detail": "Quantité invalide."}, status=400)

    try:
        resultat = previsualiser_ligne_commande(article, quantite, commande.date_commande)
    except ChiffrageError as exc:
        return JsonResponse({"detail": str(exc)}, status=400)

    return JsonResponse(
        {"ok": True, "taux_tva_suggere": _taux_tva_suggere_json(article, commande.client), **resultat}
    )


@staff_member_required
@require_http_methods(["POST"])
def previsualiser_ligne_nouvelle_commande_view(request):
    """Même aperçu que previsualiser_ligne_commande_view, mais pour une
    commande PAS ENCORE enregistrée (formulaire d'ajout) : ni `numero`, ni
    objet Commande en base — date de commande et client sont lus
    directement dans le payload (valeurs actuelles du formulaire)."""
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"detail": "JSON invalide."}, status=400)

    reference = payload.get("article")
    try:
        article = Article.objects.get(pk=reference)
    except Article.DoesNotExist:
        return JsonResponse({"detail": f"Article « {reference} » introuvable."}, status=400)

    try:
        quantite = float(payload["quantite"])
    except (KeyError, TypeError, ValueError):
        return JsonResponse({"detail": "Quantité invalide."}, status=400)

    date_commande_str = payload.get("date_commande")
    if date_commande_str:
        try:
            date_commande = forms.DateField().clean(date_commande_str.strip())
        except ValidationError:
            return JsonResponse({"detail": "Date de commande invalide."}, status=400)
    else:
        date_commande = datetime.date.today()

    client = None
    client_code = payload.get("client")
    if client_code:
        client = Tiers.objects.filter(pk=client_code).first()

    try:
        resultat = previsualiser_ligne_commande(article, quantite, date_commande)
    except ChiffrageError as exc:
        return JsonResponse({"detail": str(exc)}, status=400)

    return JsonResponse(
        {"ok": True, "taux_tva_suggere": _taux_tva_suggere_json(article, client), **resultat}
    )
