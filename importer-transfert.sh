#!/bin/sh
# Installe sur ce NAS les données d'une archive créée par exporter-transfert.sh (sur l'ancien NAS).
# À lancer APRÈS avoir installé le projet (docs/DEPLOIEMENT_SYNOLOGY.md), dans le dossier du projet.
#
# Usage : ./importer-transfert.sh erp_transfert_AAAAMMJJ_HHMMSS.tar.gz[.enc]
#   (une archive .enc se déchiffre avec la phrase secrète indiquée par CLE_CHIFFREMENT dans sauvegarde.conf)
set -e

if [ -z "$1" ] || [ ! -f "$1" ]; then
    echo "Usage : $0 archive_de_transfert.tar.gz[.enc]" >&2
    exit 1
fi
ARCHIVE="$1"
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

if [ "$(id -u)" = "0" ]; then
    DOCKER_CMD="docker compose"
else
    DOCKER_CMD="sudo docker compose"
fi

TMP="$(mktemp -d "${PROJECT_DIR}_import.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

CLAIR="$ARCHIVE"
case "$ARCHIVE" in
    *.enc)
        CLE_CHIFFREMENT=""
        CONF="${ERP_SAUVEGARDE_CONF:-$PROJECT_DIR/sauvegarde.conf}"
        # shellcheck disable=SC1090
        [ -f "$CONF" ] && . "$CONF"
        [ -n "$CLE_CHIFFREMENT" ] && [ -f "$CLE_CHIFFREMENT" ] || { echo "!! Archive chiffrée : créez le fichier de la phrase secrète et indiquez-le dans CLE_CHIFFREMENT (sauvegarde.conf)." >&2; exit 1; }
        CLAIR="$TMP/archive.tar.gz"
        openssl enc -d -aes-256-cbc -pbkdf2 -in "$ARCHIVE" -out "$CLAIR" -pass "file:$CLE_CHIFFREMENT" || { echo "!! Déchiffrement impossible (mauvaise phrase secrète ?)." >&2; exit 1; }
        ;;
esac
tar -C "$TMP" -xzf "$CLAIR"
PAQUET="$TMP/erp_transfert"
[ -d "$PAQUET" ] || { echo "!! Archive inattendue (dossier erp_transfert absent)." >&2; exit 1; }
BASE="$(ls -1 "$PAQUET"/erp_base_*.sql.gz 2>/dev/null | head -n 1)"
[ -n "$BASE" ] || { echo "!! Pas de sauvegarde de base dans l'archive." >&2; exit 1; }
MEDIA="$(ls -1 "$PAQUET"/erp_fichiers_*.tar.gz 2>/dev/null | head -n 1 || true)"

echo "Version d'origine : $( [ -f "$PAQUET/VERSION" ] && cat "$PAQUET/VERSION" || echo inconnue ) — version de ce projet : $( [ -f VERSION ] && cat VERSION || echo inconnue )"
echo "(la version de ce projet doit être égale ou plus récente : les migrations se lancent au démarrage)"

if [ -f "$PAQUET/env" ] && [ ! -f .env ]; then
    echo "==> Installation du .env de l'archive..."
    cp "$PAQUET/env" .env
    chmod 600 .env
elif [ -f "$PAQUET/env" ]; then
    echo "    (un .env existe déjà ici : conservé ; celui de l'archive n'est pas installé)"
fi
if [ -f "$PAQUET/sauvegarde.conf" ] && [ ! -f sauvegarde.conf ]; then
    cp "$PAQUET/sauvegarde.conf" sauvegarde.conf
    echo "    sauvegarde.conf installé : vérifiez les chemins (COPIE_EXTERNE, CLE_CHIFFREMENT) pour ce NAS."
fi
[ -f .env ] || { echo "!! Pas de .env : créez-le (voir docs/DEPLOIEMENT_SYNOLOGY.md) puis relancez." >&2; exit 1; }

echo "==> Démarrage de la base..."
$DOCKER_CMD up -d db
echo "==> Restauration (installation neuve : pas de sauvegarde de sécurité)..."
printf 'RESTAURER\n' | ./restaurer-nas.sh --nouveau-nas "$BASE" ${MEDIA:+"$MEDIA"}
echo "==> Transfert terminé. Vérifiez l'ERP (connexion, un devis, un PDF) avant d'éteindre l'ancien NAS."
