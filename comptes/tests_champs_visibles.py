"""Garde-fou : aucun champ de saisie des fiches d'administration ne doit être « invisible ».

Un widget Django nu (TextInput, Select…) n'a pas la bordure d'Unfold : vide, il disparaît de la fiche (cas du champ « Délai »
des devis). Ce test ouvre la fiche d'ajout de chaque modèle enregistré et vérifie que tous les champs saisissables portent le
style Unfold."""

from html.parser import HTMLParser

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import TestCase

# Champs volontairement sans bordure propre (cases à cocher, fichiers, filtres, interrupteurs) ou rendus par un composant dédié.
TYPES_IGNORES = {"hidden", "checkbox", "radio", "file", "submit", "button", "image", "search"}


class _Champs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.suspects = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag not in ("input", "select", "textarea") or not a.get("name"):
            return
        if tag == "input" and (a.get("type") or "text") in TYPES_IGNORES:
            return
        classes = a.get("class") or ""
        # Listes déroulantes rendues par un composant dédié (recherche Select2, double liste des permissions) : pas de bordure propre.
        if tag == "select" and any(c in classes for c in ("admin-autocomplete", "selectfilter", "select2")):
            return
        if "border" not in classes:
            self.suspects.append(f"<{tag} name={a['name']!r} type={a.get('type')!r}>")


class ChampsVisiblesTests(TestCase):
    def test_tous_les_champs_ont_le_style_unfold(self):
        utilisateur = get_user_model().objects.create_superuser("audit-champs", "a@example.com", "pass-mot-de-passe-20")
        self.client.force_login(utilisateur)
        problemes = {}
        for modele in admin.site._registry:
            url = f"/admin/{modele._meta.app_label}/{modele._meta.model_name}/add/"
            reponse = self.client.get(url)
            if reponse.status_code != 200:
                continue
            analyseur = _Champs()
            analyseur.feed(reponse.content.decode())
            if analyseur.suspects:
                problemes[url] = sorted(set(analyseur.suspects))
        self.maxDiff = None
        self.assertEqual(problemes, {}, "Champs sans style Unfold (invisibles quand ils sont vides)")

