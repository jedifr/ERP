#!/bin/sh
# Sauvegarde de l'ERP sur le NAS : base de données (pg_dump compressé) + fichiers déposés
# (plans DXF/DWG). Conserve les N dernières sauvegardes.
#
# Usage : ./sauvegarder-nas.sh [dossier_de_destination] [nombre_a_conserver]
#   dossier par défaut : <dossier du projet>_sauvegardes (ex. /volume1/docker/erp_sauvegardes)
#   nombre conservé par défaut : 14
#
# À lancer à la main avant chaque mise à jour, ou chaque nuit via le Planificateur de tâches
# de DSM (Panneau de configuration > Planificateur de tâches > Script défini par l'utilisateur) :
#   cd /volume1/docker/erp && ./sauvegarder-nas.sh
set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
DEST="${1:-${PROJECT_DIR}_sauvegardes}"
GARDER="${2:-14}"

cd "$PROJECT_DIR"

if [ "$(id -u)" = "0" ]; then
    DOCKER_CMD="docker compose"
else
    DOCKER_CMD="sudo docker compose"
fi

# Même défauts que docker-compose.yml.
lire_env() {
    valeur=""
    if [ -f .env ]; then
        valeur="$(sed -n "s/^$1=//p" .env | tail -n 1 | tr -d '\r')"
    fi
    echo "${valeur:-$2}"
}
DB_NAME="$(lire_env DB_NAME erp_db)"
DB_USER="$(lire_env DB_USER erp_user)"

HORODATAGE="$(date +%Y%m%d_%H%M%S)"
mkdir -p "$DEST"
FICHIER_BASE="${DEST}/erp_base_${HORODATAGE}.sql.gz"
FICHIER_MEDIA="${DEST}/erp_fichiers_${HORODATAGE}.tar.gz"

echo "==> Sauvegarde de la base ${DB_NAME}..."
$DOCKER_CMD exec -T db pg_dump -U "$DB_USER" -d "$DB_NAME" --no-owner | gzip > "$FICHIER_BASE"
gzip -t "$FICHIER_BASE"
if [ "$(gzip -dc "$FICHIER_BASE" | wc -c)" -lt 1000 ]; then
    rm -f "$FICHIER_BASE"
    echo "!! La sauvegarde de la base est vide ou trop petite : abandon (rien n'a été conservé)." >&2
    exit 1
fi

echo "==> Sauvegarde des fichiers déposés..."
if $DOCKER_CMD exec -T web test -d /app/media; then
    $DOCKER_CMD exec -T web tar -C /app -cz media > "$FICHIER_MEDIA"
    gzip -t "$FICHIER_MEDIA"
else
    echo "    (aucun dossier de fichiers à sauvegarder)"
fi

echo "==> Conservation des ${GARDER} dernières sauvegardes..."
for motif in "erp_base_*.sql.gz" "erp_fichiers_*.tar.gz"; do
    ls -1t "$DEST"/$motif 2>/dev/null | tail -n +"$((GARDER + 1))" | while read -r ancien; do
        rm -f "$ancien"
    done
done

echo "==> Terminé :"
ls -lh "$FICHIER_BASE" "$FICHIER_MEDIA" 2>/dev/null || true
echo
echo "Pensez à copier ce dossier hors du NAS (disque externe, cloud) : une sauvegarde"
echo "qui ne vit que sur le NAS ne protège pas d'une panne du NAS."
