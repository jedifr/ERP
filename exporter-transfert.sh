#!/bin/sh
# Prépare le transfert de l'ERP vers un autre NAS : une sauvegarde fraîche de la base et des fichiers déposés, réunie dans UNE archive
# avec la version de l'application et le mode d'emploi.
#
# Usage : ./exporter-transfert.sh [--avec-env] [dossier_de_destination]
#   dossier par défaut : <dossier du projet>_sauvegardes/transferts
#   --avec-env : ajoute le fichier .env (clé secrète, mot de passe de la base). L'archive est alors CHIFFRÉE (CLE_CHIFFREMENT de
#                sauvegarde.conf obligatoire) : sans cela le script refuse, car .env donne accès à toutes les données.
#
# Côté nouveau NAS : voir docs/SAUVEGARDE.md (installer le projet, puis ./importer-transfert.sh archive).
set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

AVEC_ENV=""
if [ "$1" = "--avec-env" ]; then
    AVEC_ENV="oui"
    shift
fi
DEST="${1:-${PROJECT_DIR}_sauvegardes/transferts}"

CLE_CHIFFREMENT=""
CONF="${ERP_SAUVEGARDE_CONF:-$PROJECT_DIR/sauvegarde.conf}"
# shellcheck disable=SC1090
[ -f "$CONF" ] && . "$CONF"
if [ -n "$AVEC_ENV" ]; then
    [ -f .env ] || { echo "!! Pas de fichier .env à inclure." >&2; exit 1; }
    [ -n "$CLE_CHIFFREMENT" ] && [ -f "$CLE_CHIFFREMENT" ] || { echo "!! --avec-env exige CLE_CHIFFREMENT (phrase secrète) dans sauvegarde.conf : le .env ne voyage jamais en clair." >&2; exit 1; }
fi

TMP="$(mktemp -d "${PROJECT_DIR}_transfert.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
PAQUET="$TMP/erp_transfert"
mkdir -p "$PAQUET"

echo "==> Sauvegarde fraîche de la base et des fichiers..."
ERP_SANS_COPIE_EXTERNE=1 ./sauvegarder-nas.sh "$TMP/sauvegarde" 1 >/dev/null
cp "$TMP"/sauvegarde/erp_base_*.sql.gz "$PAQUET/"
cp "$TMP"/sauvegarde/erp_fichiers_*.tar.gz "$PAQUET/" 2>/dev/null || echo "    (pas de fichiers déposés)"
[ -f VERSION ] && cp VERSION "$PAQUET/VERSION"
if [ -n "$AVEC_ENV" ]; then
    cp .env "$PAQUET/env"
    [ -f sauvegarde.conf ] && cp sauvegarde.conf "$PAQUET/sauvegarde.conf"
fi
cat > "$PAQUET/LISEZMOI.txt" <<TXT
Archive de transfert de l'ERP — créée le $(date '+%d/%m/%Y à %H:%M') sur $(hostname)
Version de l'application : $( [ -f VERSION ] && cat VERSION || echo inconnue )

Sur le NOUVEAU NAS :
  1. Installer Docker (Container Manager) et copier le projet (voir docs/DEPLOIEMENT_SYNOLOGY.md).
  2. Copier cette archive dans le dossier du projet.
  3. Lancer :  ./importer-transfert.sh erp_transfert_AAAAMMJJ_HHMMSS.tar.gz   (ou .tar.gz.enc si l'archive est chiffrée)
  4. Ouvrir l'ERP et vérifier (connexion, un devis, un PDF). Garder l'ancien NAS intact jusqu'à cette vérification.
$( [ -n "$AVEC_ENV" ] && echo "  Le .env est inclus : il sera installé s'il n'en existe pas déjà. L'archive est chiffrée : gardez la phrase secrète (CLE_CHIFFREMENT)." || echo "  Le .env N'est PAS inclus : recopiez-le à part (clé secrète, mot de passe de la base) ou créez-en un nouveau (les sessions seront fermées)." )
TXT

mkdir -p "$DEST"
ARCHIVE="$DEST/erp_transfert_$(date +%Y%m%d_%H%M%S).tar.gz"
tar -C "$TMP" -czf "$ARCHIVE" erp_transfert
if [ -n "$AVEC_ENV" ]; then
    openssl enc -aes-256-cbc -pbkdf2 -salt -in "$ARCHIVE" -out "${ARCHIVE}.enc" -pass "file:$CLE_CHIFFREMENT"
    rm -f "$ARCHIVE"
    ARCHIVE="${ARCHIVE}.enc"
fi
echo "==> Archive de transfert prête : $ARCHIVE"
ls -lh "$ARCHIVE"
