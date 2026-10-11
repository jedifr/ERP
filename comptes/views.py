from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.views.static import serve as serve_static

# Dossier de MEDIA_ROOT -> permission exigée pour le lire. Un dossier absent de la liste
# n'est lisible que par un compte du personnel (is_staff).
PERMISSIONS_PAR_DOSSIER = {
    "decoupe": "decoupe.view_piecedecoupe",
    "stock": "stock.view_lot",  # certificats matière
    "societe": "comptes.view_societe",  # logo
}


def media_protege(request, path):
    """Sert les fichiers déposés (plans DXF/DWG des clients...) uniquement à un compte
    connecté ayant le droit de les voir. Avant : accessibles à toute personne connaissant
    l'URL, sans connexion."""
    utilisateur = request.user
    if not utilisateur.is_authenticated:
        return redirect_to_login(request.get_full_path())
    dossier = path.split("/", 1)[0]
    permission = PERMISSIONS_PAR_DOSSIER.get(dossier)
    autorise = utilisateur.has_perm(permission) if permission else utilisateur.is_staff
    if not autorise:
        raise PermissionDenied
    return serve_static(request, path, document_root=settings.MEDIA_ROOT)
