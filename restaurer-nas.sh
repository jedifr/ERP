#!/bin/sh
# Restaure l'ERP depuis une sauvegarde faite par sauvegarder-nas.sh.
# ATTENTION : remplace TOUTE la base actuelle par celle de la sauvegarde.
#
# Usage : ./restaurer-nas.sh erp_base_AAAAMMJJ_HHMMSS.sql.gz [erp_fichiers_AAAAMMJJ_HHMMSS.tar.gz]
#
# Avant de remplacer quoi que ce soit, une sauvegarde de sécurité de l'état actuel est faite
# dans <dossier du projet>_sauvegardes/avant_restauration.
set -e

NOUVEAU_NAS=""
if [ "$1" = "--nouveau-nas" ]; then
    NOUVEAU_NAS="oui"   # installation neuve (base vide) : pas de sauvegarde de sécurité de l'état actuel
    shift
fi
if [ -z "$1" ]; then
    echo "Usage : $0 [--nouveau-nas] fichier_base.sql.gz[.enc] [fichiers.tar.gz[.enc]]" >&2
    echo "  --nouveau-nas : première installation sur un NAS neuf (voir docs/SAUVEGARDE.md)" >&2
    echo "  Les fichiers .enc (copie externe chiffrée) sont déchiffrés avec CLE_CHIFFREMENT de sauvegarde.conf." >&2
    exit 1
fi
BASE_SAUVEGARDE="$1"
MEDIA_SAUVEGARDE="$2"

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

TMP_RESTAURATION="$(mktemp -d)"
nettoyer() { [ -n "$TMP_RESTAURATION" ] && rm -rf "$TMP_RESTAURATION"; return 0; }
trap nettoyer EXIT

# Déchiffre un fichier .enc (copie externe chiffrée) dans un dossier temporaire et affiche le chemin du fichier en clair.
dechiffrer() {
    case "$1" in
        *.enc)
            CLE_CHIFFREMENT=""
            CONF="${ERP_SAUVEGARDE_CONF:-$PROJECT_DIR/sauvegarde.conf}"
            # shellcheck disable=SC1090
            [ -f "$CONF" ] && . "$CONF"
            [ -n "$CLE_CHIFFREMENT" ] && [ -f "$CLE_CHIFFREMENT" ] || { echo "!! Fichier chiffré : indiquez CLE_CHIFFREMENT (fichier de la phrase secrète) dans sauvegarde.conf." >&2; return 1; }
            clair="$TMP_RESTAURATION/$(basename "${1%.enc}")"
            openssl enc -d -aes-256-cbc -pbkdf2 -in "$1" -out "$clair" -pass "file:$CLE_CHIFFREMENT" || { echo "!! Déchiffrement impossible (mauvaise phrase secrète ?)." >&2; return 1; }
            echo "$clair"
            ;;
        *) echo "$1" ;;
    esac
}

[ -f "$BASE_SAUVEGARDE" ] || { echo "!! Fichier introuvable : $BASE_SAUVEGARDE" >&2; exit 1; }
BASE_SAUVEGARDE="$(dechiffrer "$BASE_SAUVEGARDE")" || exit 1
gzip -t "$BASE_SAUVEGARDE" || { echo "!! Sauvegarde de base corrompue." >&2; exit 1; }
if [ -n "$MEDIA_SAUVEGARDE" ]; then
    [ -f "$MEDIA_SAUVEGARDE" ] || { echo "!! Fichier introuvable : $MEDIA_SAUVEGARDE" >&2; exit 1; }
    MEDIA_SAUVEGARDE="$(dechiffrer "$MEDIA_SAUVEGARDE")" || exit 1
    gzip -t "$MEDIA_SAUVEGARDE" || { echo "!! Sauvegarde des fichiers corrompue." >&2; exit 1; }
fi

if [ "$(id -u)" = "0" ]; then
    DOCKER_CMD="docker compose"
else
    DOCKER_CMD="sudo docker compose"
fi

lire_env() {
    valeur=""
    if [ -f .env ]; then
        valeur="$(sed -n "s/^$1=//p" .env | tail -n 1 | tr -d '\r')"
    fi
    echo "${valeur:-$2}"
}
DB_NAME="$(lire_env DB_NAME erp_db)"
DB_USER="$(lire_env DB_USER erp_user)"

echo "Cette opération REMPLACE la base « ${DB_NAME} » par le contenu de :"
echo "    $BASE_SAUVEGARDE"
echo "Les modifications faites depuis cette sauvegarde seront perdues."
printf "Tapez RESTAURER pour continuer : "
read -r confirmation
[ "$confirmation" = "RESTAURER" ] || { echo "Annulé."; exit 1; }

if [ -z "$NOUVEAU_NAS" ]; then
    echo "==> Sauvegarde de sécurité de l'état actuel..."
    ERP_SANS_COPIE_EXTERNE=1 ./sauvegarder-nas.sh "${PROJECT_DIR}_sauvegardes/avant_restauration" 5
fi

echo "==> Arrêt de l'application..."
$DOCKER_CMD stop web

echo "==> Remplacement de la base..."
$DOCKER_CMD exec -T db psql -U "$DB_USER" -d postgres -v ON_ERROR_STOP=1 \
    -c "DROP DATABASE IF EXISTS \"${DB_NAME}\" WITH (FORCE);" \
    -c "CREATE DATABASE \"${DB_NAME}\" OWNER \"${DB_USER}\";"
gzip -dc "$BASE_SAUVEGARDE" | $DOCKER_CMD exec -T db psql -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 -q

if [ -n "$MEDIA_SAUVEGARDE" ]; then
    echo "==> Restauration des fichiers déposés..."
    $DOCKER_CMD start web
    gzip -dc "$MEDIA_SAUVEGARDE" | $DOCKER_CMD exec -T web tar -C /app -x
    $DOCKER_CMD stop web
fi

echo "==> Redémarrage de l'application..."
$DOCKER_CMD up -d web

echo "==> Restauration terminée."
