"""Noms français des droits (permissions) : Django les enregistre en anglais (« Can change Facture ») à la création, et l'écran des groupes
les affiche tels quels. Ils sont traduits à chaque migration (signal post_migrate), y compris pour les nouveaux modèles."""

PREFIXES = (("Can add ", "Peut ajouter "), ("Can change ", "Peut modifier "), ("Can delete ", "Peut supprimer "), ("Can view ", "Peut voir "))
MODELES_DJANGO = {
    "log entry": "entrée du journal", "permission": "droit", "group": "groupe", "user": "utilisateur", "content type": "type de contenu", "session": "session",
}


def traduire_nom(nom):
    """Nom français d'un droit à partir de son nom anglais ; inchangé s'il est déjà français."""
    for anglais, francais in PREFIXES:
        if nom.startswith(anglais):
            reste = nom[len(anglais):]
            if reste.startswith("historical "):
                reste = "historique " + reste[len("historical "):]
            return francais + MODELES_DJANGO.get(reste, reste)
    return nom


def traduire_droits(sender=None, **kwargs):
    from django.contrib.auth.models import Permission

    for droit in Permission.objects.all():
        nouveau = traduire_nom(droit.name)
        if nouveau != droit.name:
            Permission.objects.filter(pk=droit.pk).update(name=nouveau)
