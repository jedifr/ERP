#!/bin/sh
# Sauvegarde de l'ERP sur le NAS : base de données (pg_dump compressé) + fichiers déposés
# (plans DXF/DWG). Conserve les N dernières sauvegardes.
#
# Usage : ./sauvegarder-nas.sh [dossier_de_destination] [nombre_a_conserver]
#   dossier par défaut : <dossier du projet>_sauvegardes (ex. /volume1/docker/erp_sauvegardes)
#   nombre conservé par défaut : 14
#
# Options (fichier sauvegarde.conf à côté du script, voir sauvegarde.conf.exemple et docs/SAUVEGARDE.md) :
#   COPIE_EXTERNE   copie, après chaque sauvegarde, vers un disque USB / un dossier réseau monté (chemin)
#                   ou vers un autre NAS / serveur (utilisateur@machine:/dossier, par rsync sur ssh)
#   CLE_CHIFFREMENT fichier contenant la phrase secrète : la copie externe est alors chiffrée (AES-256)
#   ALERTE_URL      adresse appelée (POST) si la sauvegarde échoue ; ALERTE_COMMANDE : commande lancée à la place
#
# À lancer à la main avant chaque mise à jour, ou chaque nuit via le Planificateur de tâches
# de DSM (Panneau de configuration > Planificateur de tâches > Script défini par l'utilisateur) :
#   cd /volume1/docker/erp && ./sauvegarder-nas.sh
# (cochez « Envoyer les détails de l'exécution par e-mail » : DSM prévient aussi quand le script échoue).
set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
DEST="${1:-${PROJECT_DIR}_sauvegardes}"
GARDER="${2:-14}"

cd "$PROJECT_DIR"

CONF="${ERP_SAUVEGARDE_CONF:-$PROJECT_DIR/sauvegarde.conf}"
COPIE_EXTERNE=""
CLE_CHIFFREMENT=""
ALERTE_URL=""
ALERTE_COMMANDE=""
GARDER_EXTERNE=30
# shellcheck disable=SC1090
[ -f "$CONF" ] && . "$CONF"

alerter() {
    message="ERP — $1"
    if [ -n "$ALERTE_URL" ]; then
        curl -fsS -m 20 -X POST --data-urlencode "message=${message}" "$ALERTE_URL" >/dev/null 2>&1 || echo "    (alerte non envoyée à ${ALERTE_URL})" >&2
    fi
    if [ -n "$ALERTE_COMMANDE" ]; then
        MESSAGE_ALERTE="$message" sh -c "$ALERTE_COMMANDE" || echo "    (la commande d'alerte a échoué)" >&2
    fi
}

TMP_EXTERNE=""
terminer() {
    statut=$?
    [ -n "$TMP_EXTERNE" ] && rm -rf "$TMP_EXTERNE"
    if [ "$statut" -ne 0 ]; then
        echo "!! Sauvegarde ÉCHOUÉE (code ${statut})." >&2
        alerter "la sauvegarde de $(hostname) a échoué le $(date '+%d/%m/%Y à %H:%M'). Voir le planificateur de tâches du NAS."
    fi
}
trap terminer EXIT

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
MEDIA_FAIT=""
if $DOCKER_CMD exec -T web test -d /app/media; then
    $DOCKER_CMD exec -T web tar -C /app -cz media > "$FICHIER_MEDIA"
    gzip -t "$FICHIER_MEDIA"
    MEDIA_FAIT="oui"
else
    echo "    (aucun dossier de fichiers à sauvegarder)"
fi

# Somme de contrôle : permet de vérifier plus tard qu'une copie n'a pas été abîmée.
( cd "$DEST" && sha256sum "$(basename "$FICHIER_BASE")" ${MEDIA_FAIT:+"$(basename "$FICHIER_MEDIA")"} >> SHA256SUMS )

echo "==> Conservation des ${GARDER} dernières sauvegardes..."
for motif in "erp_base_*.sql.gz" "erp_fichiers_*.tar.gz"; do
    ls -1t "$DEST"/$motif 2>/dev/null | tail -n +"$((GARDER + 1))" | while read -r ancien; do
        rm -f "$ancien"
    done
done
# La liste des sommes ne garde que les fichiers qui existent encore.
if [ -f "$DEST/SHA256SUMS" ]; then
    ( cd "$DEST" && while read -r somme nom; do [ -f "$nom" ] && echo "$somme  $nom"; done < SHA256SUMS | sort -u -k2 > SHA256SUMS.tmp && mv SHA256SUMS.tmp SHA256SUMS ) || true
fi

# ------------------------------------------------------------------ copie hors du NAS
if [ -n "$COPIE_EXTERNE" ] && [ -z "$ERP_SANS_COPIE_EXTERNE" ]; then
    echo "==> Copie hors du NAS vers ${COPIE_EXTERNE}..."
    TMP_EXTERNE="$(mktemp -d "${DEST}/.externe.XXXXXX")"
    A_COPIER=""
    for f in "$FICHIER_BASE" ${MEDIA_FAIT:+"$FICHIER_MEDIA"}; do
        if [ -n "$CLE_CHIFFREMENT" ]; then
            [ -f "$CLE_CHIFFREMENT" ] || { echo "!! Clé de chiffrement introuvable : $CLE_CHIFFREMENT" >&2; exit 1; }
            openssl enc -aes-256-cbc -pbkdf2 -salt -in "$f" -out "$TMP_EXTERNE/$(basename "$f").enc" -pass "file:$CLE_CHIFFREMENT"
            A_COPIER="$A_COPIER $TMP_EXTERNE/$(basename "$f").enc"
        else
            echo "    (attention : copie NON chiffrée — voir CLE_CHIFFREMENT dans sauvegarde.conf)"
            A_COPIER="$A_COPIER $f"
        fi
    done
    case "$COPIE_EXTERNE" in
        *@*:*)  # autre machine : rsync sur ssh (pas de rotation automatique côté distant)
            # shellcheck disable=SC2086
            rsync -a -e "ssh -o BatchMode=yes" $A_COPIER "$COPIE_EXTERNE/"
            ;;
        *)
            mkdir -p "$COPIE_EXTERNE"
            # shellcheck disable=SC2086
            cp $A_COPIER "$COPIE_EXTERNE/"
            for motif in "erp_base_*" "erp_fichiers_*"; do
                ls -1t "$COPIE_EXTERNE"/$motif 2>/dev/null | tail -n +"$((GARDER_EXTERNE + 1))" | while read -r ancien; do rm -f "$ancien"; done
            done
            ;;
    esac
    rm -rf "$TMP_EXTERNE"
    TMP_EXTERNE=""
fi

# État de la dernière sauvegarde réussie (lu par verifier-sauvegarde.sh).
{
    echo "date=$(date '+%Y-%m-%dT%H:%M:%S')"
    echo "base=$(basename "$FICHIER_BASE")"
    [ -n "$MEDIA_FAIT" ] && echo "fichiers=$(basename "$FICHIER_MEDIA")"
    echo "copie_externe=${COPIE_EXTERNE:-non}"
    echo "chiffree=$([ -n "$CLE_CHIFFREMENT" ] && echo oui || echo non)"
} > "$DEST/derniere_sauvegarde.txt"

echo "==> Terminé :"
ls -lh "$FICHIER_BASE" "$FICHIER_MEDIA" 2>/dev/null || true
if [ -z "$COPIE_EXTERNE" ]; then
    echo
    echo "Pensez à copier ce dossier hors du NAS (disque externe, autre NAS, cloud) : une sauvegarde"
    echo "qui ne vit que sur le NAS ne protège pas d'une panne du NAS. Voir COPIE_EXTERNE dans"
    echo "sauvegarde.conf.exemple et docs/SAUVEGARDE.md."
fi
