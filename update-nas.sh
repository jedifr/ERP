#!/bin/sh
# Met à jour l'ERP depuis GitHub sur le NAS : télécharge la dernière version
# de la branche, conserve le .env existant, bascule, reconstruit et relance
# les conteneurs Docker.
#
# Usage : ./update-nas.sh [branche]
#   (branche par défaut : claude/project-construction-k7owwb)
set -e

BRANCH="${1:-claude/project-construction-k7owwb}"
REPO="jedifr/ERP"

# Ce script remplace son propre dossier plus bas dans l'exécution. On se
# relance depuis une copie temporaire pour ne plus dépendre du fichier
# original pendant l'opération (rien de dangereux en soi côté Unix, mais
# c'est plus simple à raisonner ainsi). La copie est placée à côté du projet
# plutôt que dans /tmp : sur DSM, /tmp est souvent monté "noexec" (écriture
# et chmod +x possibles, mais exécution refusée par le noyau).
if [ -z "$ERP_UPDATE_RELAUNCHED" ]; then
    ORIG_DIR="$(cd "$(dirname "$0")" && pwd)"
    PARENT_DIR="$(dirname "$ORIG_DIR")"
    TMP_SELF="$(mktemp "${PARENT_DIR}/.erp-update.XXXXXX")"
    cp "$0" "$TMP_SELF"
    chmod +x "$TMP_SELF"
    ERP_UPDATE_RELAUNCHED=1 ERP_UPDATE_ORIG_DIR="$ORIG_DIR" exec "$TMP_SELF" "$BRANCH"
fi
trap 'rm -f "$0"' EXIT

PROJECT_DIR="$ERP_UPDATE_ORIG_DIR"
PARENT_DIR="$(dirname "$PROJECT_DIR")"
PROJECT_NAME="$(basename "$PROJECT_DIR")"
NEW_DIR="${PARENT_DIR}/${PROJECT_NAME}_new"
OLD_DIR="${PARENT_DIR}/${PROJECT_NAME}_old"
TARBALL="${PARENT_DIR}/${PROJECT_NAME}_update.tar.gz"

echo "==> Mise à jour de l'ERP — branche : ${BRANCH}"
echo "==> Dossier projet : ${PROJECT_DIR}"

echo "==> Téléchargement..."
rm -rf "$NEW_DIR" "$TARBALL"
curl -fL -o "$TARBALL" "https://codeload.github.com/${REPO}/tar.gz/refs/heads/${BRANCH}"

echo "==> Extraction..."
mkdir "$NEW_DIR"
tar -xzf "$TARBALL" -C "$NEW_DIR" --strip-components=1
rm -f "$TARBALL"

if [ -f "${PROJECT_DIR}/.env" ]; then
    echo "==> Conservation du .env existant..."
    cp "${PROJECT_DIR}/.env" "${NEW_DIR}/.env"
else
    echo "!! Aucun .env existant trouvé : à configurer dans ${PROJECT_DIR} après la mise à jour."
fi

echo "==> Bascule vers la nouvelle version..."
rm -rf "$OLD_DIR"
mv "$PROJECT_DIR" "$OLD_DIR"
mv "$NEW_DIR" "$PROJECT_DIR"

cd "$PROJECT_DIR"

if [ "$(id -u)" = "0" ]; then
    DOCKER_CMD="docker compose"
else
    DOCKER_CMD="sudo docker compose"
fi

echo "==> Reconstruction et redémarrage des conteneurs (${DOCKER_CMD})..."
$DOCKER_CMD up -d --build

echo "==> Nettoyage de l'ancienne version..."
rm -rf "$OLD_DIR"

echo "==> Terminé. État des conteneurs :"
$DOCKER_CMD ps

echo
echo "En cas de souci : $DOCKER_CMD logs -f web"
