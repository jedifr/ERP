import os, re, sys, django, json
sys.path.insert(0, "/home/user/ERP")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()
from django.utils import translation
from django.utils.translation import gettext
U = "/usr/local/lib/python3.11/dist-packages/unfold"

TRAD = {
 "Action": "Action",
 "Add another %(model)s": "Ajouter encore : %(model)s",
 "Add new item": "Ajouter un élément",
 "Add row": "Ajouter une ligne",
 "Add %(name)s": "Ajouter %(name)s",
 "After you've created a user, you’ll be able to edit more user options.": "Après la création de l'utilisateur, vous pourrez modifier ses autres options.",
 "All applications": "Toutes les applications",
 "Apply Filters": "Appliquer les filtres",
 "Are you sure you want to delete the selected %(objects_name)s? All of the following objects and their related items will be deleted:": "Voulez-vous vraiment supprimer les %(objects_name)s sélectionnés ? Tous les objets suivants et leurs éléments associés seront supprimés :",
 "Are you sure you want to delete the %(object_name)s \"%(escaped_object)s\"? All of the following related items will be deleted:": "Voulez-vous vraiment supprimer %(object_name)s « %(escaped_object)s » ? Tous les éléments associés suivants seront supprimés :",
 "Below is a preview of data to be imported. If you are satisfied with the results, click 'Confirm import'": "Voici un aperçu des données à importer. Si le résultat vous convient, cliquez sur « Confirmer l'import ».",
 "Bold": "Gras", "By %(filter_title)s": "Par %(filter_title)s", "Cancel": "Annuler",
 "Change selected %(model)s": "Modifier la sélection (%(model)s)",
 "Changes": "Modifications",
 "Choose a date from the list below to revert to a previous version of this object": "Choisissez une date dans la liste ci-dessous pour revenir à une version précédente de cet objet",
 "Choose file to upload": "Choisir un fichier à téléverser", "Click to cancel": "Cliquer pour annuler", "Click to download": "Cliquer pour télécharger",
 "Code": "Code", "Collapse": "Réduire", "Confirm import": "Confirmer l'import", "Current file": "Fichier actuel", "Dark": "Sombre",
 "Dataset not found. No action performed.": "Jeu de données introuvable. Aucune action effectuée.",
 "Date": "Date", "Date from": "Date de début", "Date to": "Date de fin", "Default": "Par défaut",
 "Delete selected %(model)s": "Supprimer la sélection (%(model)s)",
 "Deleting the selected %(objects_name)s would require deleting the following protected related objects:": "La suppression des %(objects_name)s sélectionnés nécessiterait la suppression des objets protégés suivants :",
 "Deleting the selected %(objects_name)s would result in deleting related objects, but your account doesn't have permission to delete the following types of objects:": "La suppression des %(objects_name)s sélectionnés entraînerait la suppression d'objets associés, mais votre compte n'a pas la permission de supprimer les types d'objets suivants :",
 "Deleting the %(object_name)s '%(escaped_object)s' would require deleting the following protected related objects:": "La suppression de %(object_name)s « %(escaped_object)s » nécessiterait la suppression des objets protégés suivants :",
 "Deleting the %(object_name)s '%(escaped_object)s' would result in deleting related objects, but your account doesn't have permission to delete the following types of objects:": "La suppression de %(object_name)s « %(escaped_object)s » entraînerait la suppression d'objets associés, mais votre compte n'a pas la permission de supprimer les types d'objets suivants :",
 "Edit": "Modifier",
 "Enter a new password for the user <strong>%(username)s</strong>.": "Saisissez un nouveau mot de passe pour l'utilisateur <strong>%(username)s</strong>.",
 "Enter an URL": "Saisissez une adresse (URL)", "Expand row": "Développer la ligne", "Export": "Exporter", "False": "Faux",
 "Forgotten your password or username?": "Mot de passe ou identifiant oublié ?", "From": "De", "General": "Général", "Go back": "Retour",
 "Group": "Groupe", "Group permissions": "Droits du groupe", "Heading": "Titre", "Image preview": "Aperçu de l'image", "Import": "Importer",
 "Indent decrease": "Diminuer le retrait", "Indent increase": "Augmenter le retrait", "Italic": "Italique", "Light": "Clair",
 "Line number": "Numéro de ligne", "Link": "Lien", "Loading more results...": "Chargement de résultats supplémentaires…",
 "Manage group": "Gérer le groupe", "Manage user": "Gérer l'utilisateur", "Models in the %(name)s application": "Modèles de l'application %(name)s",
 "More actions": "Plus d'actions", "Name": "Nom", "New": "Nouveau", "Next": "Suivant", "No data": "Aucune donnée",
 "No recent searches": "Aucune recherche récente", "No results found": "Aucun résultat", "No results matching your query": "Aucun résultat pour votre recherche",
 "Non field specific": "Non spécifique à un champ", "Not enough data.": "Pas assez de données.", "Object permissions": "Droits sur l'objet",
 "Ordered list": "Liste numérotée", "Paragraph": "Paragraphe",
 "Please correct these errors in your data where possible, then reupload it using the form above.": "Corrigez si possible ces erreurs dans vos données, puis téléversez-les à nouveau avec le formulaire ci-dessus.",
 "Previous": "Précédent", "Quote": "Citation", "Recent searches": "Recherches récentes", "Record picture": "Photo de l'enregistrement",
 "Redo": "Rétablir", "Reset filters": "Réinitialiser les filtres", "Reset to default": "Rétablir par défaut", "Return to site": "Retour au site",
 "Row": "Ligne", "Run": "Lancer", "Search apps and models...": "Rechercher une page ou un modèle…", "Select action": "Choisir une action",
 "Select action to run": "Choisir l'action à lancer", "Select all rows": "Sélectionner toutes les lignes",
 "Select all %(total_count)s %(module_name)s": "Sélectionner les %(total_count)s %(module_name)s", "Select currency": "Choisir la devise",
 "Select format": "Choisir le format", "Select record": "Choisir l'enregistrement", "Select value": "Choisir une valeur", "Skipped": "Ignoré",
 "Some rows failed to validate": "Certaines lignes n'ont pas passé la validation", "Sorting priority: %(priority_number)s": "Priorité de tri : %(priority_number)s",
 "Strike": "Barré", "System": "Système",
 "This exporter will export the following fields": "Cet export contiendra les champs suivants",
 "This exporter will export the following fields: ": "Cet export contiendra les champs suivants : ",
 "This importer will import the following fields: ": "Cet import lira les champs suivants : ",
 "This item will be deleted.": "Cet élément sera supprimé.",
 "This page yielded into no results. Create a new item or reset your filters.": "Aucun résultat sur cette page. Créez un nouvel élément ou réinitialisez vos filtres.",
 "To": "À", "Toggle password visibility": "Afficher ou masquer le mot de passe", "True": "Vrai", "Type to search": "Rechercher…",
 "Underlined": "Souligné", "Undo": "Annuler", "Unlink": "Délier", "Unordered list": "Liste à puces", "Update": "Mettre à jour",
 "User permissions": "Droits de l'utilisateur", "Value": "Valeur", "View selected %(model)s": "Voir la sélection (%(model)s)",
 "View traceback": "Voir la trace", "Welcome": "Bienvenue",
 "You are authenticated as %(username)s, but are not authorized to access this page. Would you like to login to a different account?": "Vous êtes connecté en tant que %(username)s, mais vous n'avez pas accès à cette page. Voulez-vous vous connecter avec un autre compte ?",
 "You can not create nested object without parent": "Impossible de créer un objet imbriqué sans son parent",
 "You have been successfully logged out from the administration": "Vous êtes bien déconnecté de l'administration",
 "%(full_result_count)s total": "%(full_result_count)s au total",
}
PLURALS = {
 ("Export %(len)s selected item.", "Export %(len)s selected items."): ("Exporter l'élément sélectionné.", "Exporter les %(len)s éléments sélectionnés."),
 ("Found %(counter)s result in %(time)s seconds", "Found %(counter)s results in %(time)s seconds"): ("%(counter)s résultat trouvé en %(time)s secondes", "%(counter)s résultats trouvés en %(time)s secondes"),
 ("Please correct the error below.", "Please correct the errors below."): ("Veuillez corriger l'erreur ci-dessous.", "Veuillez corriger les erreurs ci-dessous."),
 ("entry", "entries"): ("entrée", "entrées"),
 ("%(counter)s result", "%(counter)s results"): ("%(counter)s résultat", "%(counter)s résultats"),
}
def norm(s): return re.sub(r"\s+", " ", s).strip()
TRAD_N = {}
def conv(s): return re.sub(r"{{\s*(\w+)\s*}}", r"%(\1)s", s)

rx_block = re.compile(r"{%\s*(?:blocktrans|blocktranslate)([^%]*)%}(.*?){%\s*end(?:blocktrans|blocktranslate)\s*%}", re.S)
rx_trans = re.compile(r"{%\s*(?:trans|translate)\s+(['\"])(.*?)\1", re.S)
entrees = {}   # msgid -> (msgstr, plural?)
manquants = set()
def ajoute(sing, plur=None, trimmed=False):
    cle_s = norm(sing); cle_p = norm(plur) if plur else None
    if plur:
        t = PLURALS.get((cle_s, cle_p)) or PLURALS.get((cle_s.replace("%(counter)s ", ""), cle_p.replace("%(counter)s ", "")) if False else None)
        if t is None:
            for (a, b), v in PLURALS.items():
                if norm(a) == cle_s and norm(b) == cle_p: t = v
        if t is None: manquants.add((cle_s, cle_p)); return
        entrees[(sing, plur)] = t
    else:
        t = TRAD_N.get(cle_s)
        if t is None: manquants.add(cle_s); return
        entrees[(sing, None)] = t
for k, v in TRAD.items(): TRAD_N[norm(k)] = v
translation.activate("fr")
for root, _, files in os.walk(U):
    for f in files:
        p = os.path.join(root, f)
        if f.endswith(".html"):
            s = open(p, encoding="utf-8").read()
            for m in rx_trans.finditer(s):
                if gettext(m.group(2)) == m.group(2): ajoute(m.group(2))
            for m in rx_block.finditer(s):
                opts = m.group(1); corps = conv(m.group(2)); trimmed = "trimmed" in opts
                parties = re.split(r"{%\s*plural\s*%}", corps)
                sing = parties[0]; plur = parties[1] if len(parties) > 1 else None
                if trimmed:
                    sing = translation.trim_whitespace(sing); plur = translation.trim_whitespace(plur) if plur else None
                if gettext(sing) == sing or plur: ajoute(sing, plur)
        elif f.endswith(".py"):
            s = open(p, encoding="utf-8").read()
            for m in re.finditer(r"\b_\((['\"])(.*?)\1\)|gettext(?:_lazy)?\((['\"])(.*?)\3\)", s):
                t = m.group(2) or m.group(4)
                if t and gettext(t) == t: ajoute(t)
print("manquants:", len(manquants))
for x in sorted(manquants, key=str): print("  ", repr(x))
json.dump({"n": len(entrees)}, sys.stdout); print()

def esc(s): return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
sortie = ['# Traductions françaises des textes de l\'interface Unfold (aucun catalogue fourni par la bibliothèque) : généré par docs/outils/generer_traductions.py',
          'msgid ""', 'msgstr ""', '"Project-Id-Version: ERP\\n"', '"Language: fr\\n"', '"MIME-Version: 1.0\\n"', '"Content-Type: text/plain; charset=UTF-8\\n"',
          '"Content-Transfer-Encoding: 8bit\\n"', '"Plural-Forms: nplurals=2; plural=(n > 1);\\n"', '']
for (sing, plur), tr in sorted(entrees.items(), key=lambda x: str(x[0])):
    if plur is None:
        sortie += [f'msgid "{esc(sing)}"', f'msgstr "{esc(tr)}"', '']
    else:
        sortie += [f'msgid "{esc(sing)}"', f'msgid_plural "{esc(plur)}"', f'msgstr[0] "{esc(tr[0])}"', f'msgstr[1] "{esc(tr[1])}"', '']
open("/home/user/ERP/locale/fr/LC_MESSAGES/django.po", "w", encoding="utf-8").write("\n".join(sortie))
