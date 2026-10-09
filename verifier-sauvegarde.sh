#!/bin/sh
# Vérifie qu'une sauvegarde est utilisable, SANS toucher à la base de l'ERP :
#   1. le fichier n'est pas corrompu (gzip, somme de contrôle SHA256 si elle existe) ;
#   2. la base est restaurée dans une base temporaire, qui contient bien les tables et les données de l'ERP, puis supprimée ;
#   3. (option --age-max N) la sauvegarde la plus récente a moins de N jours : sinon le script échoue, ce qui fait prévenir DSM.
#
# Usage : ./verifier-sauvegarde.sh [--age-max JOURS] [--sans-restauration] [fichier_base.sql.gz[.enc]]
#   sans fichier : la sauvegarde la plus récente de <dossier du projet>_sauvegardes
#
# À planifier (Planificateur de tâches de DSM) chaque semaine : une sauvegarde qu'on n'a jamais restaurée n'est qu'une hypothèse.
set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"
DEST="${ERP_SAUVEGARDES:-${PROJECT_DIR}_sauvegardes}"

AGE_MAX=""
SANS_RESTAURATION=""
FICHIER=""
while [ $# -gt 0 ]; do
    case "$1" in
        --age-max) AGE_MAX="$2"; shift 2 ;;
        --sans-restauration) SANS_RESTAURATION="oui"; shift ;;
        *) FICHIER="$1"; shift ;;
    esac
done

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
DB_USER="$(lire_env DB_USER erp_user)"

TMP="$(mktemp -d)"
BASE_VERIF="erp_verif_$$"
nettoyer() {
    statut=$?
    $DOCKER_CMD exec -T db psql -U "$DB_USER" -d postgres -q -c "DROP DATABASE IF EXISTS \"${BASE_VERIF}\" WITH (FORCE);" >/dev/null 2>&1 || true
    rm -rf "$TMP"
    [ "$statut" -eq 0 ] || echo "!! Vérification de la sauvegarde ÉCHOUÉE." >&2
}
trap nettoyer EXIT

if [ -z "$FICHIER" ]; then
    FICHIER="$(ls -1t "$DEST"/erp_base_*.sql.gz 2>/dev/null | head -n 1)"
    [ -n "$FICHIER" ] || { echo "!! Aucune sauvegarde dans $DEST." >&2; exit 1; }
fi
[ -f "$FICHIER" ] || { echo "!! Fichier introuvable : $FICHIER" >&2; exit 1; }
echo "==> Sauvegarde vérifiée : $FICHIER"

if [ -n "$AGE_MAX" ]; then
    # Âge en jours, d'après la date de modification du fichier.
    age="$(( ( $(date +%s) - $(stat -c %Y "$FICHIER") ) / 86400 ))"
    if [ "$age" -gt "$AGE_MAX" ]; then
        echo "!! La dernière sauvegarde a ${age} jour(s) (maximum accepté : ${AGE_MAX}). La planification ne tourne plus ?" >&2
        exit 1
    fi
    echo "    âge : ${age} jour(s) — correct"
fi

CLAIR="$FICHIER"
case "$FICHIER" in
    *.enc)
        CLE_CHIFFREMENT=""
        CONF="${ERP_SAUVEGARDE_CONF:-$PROJECT_DIR/sauvegarde.conf}"
        # shellcheck disable=SC1090
        [ -f "$CONF" ] && . "$CONF"
        [ -n "$CLE_CHIFFREMENT" ] && [ -f "$CLE_CHIFFREMENT" ] || { echo "!! Fichier chiffré : CLE_CHIFFREMENT manquante dans sauvegarde.conf." >&2; exit 1; }
        CLAIR="$TMP/base.sql.gz"
        openssl enc -d -aes-256-cbc -pbkdf2 -in "$FICHIER" -out "$CLAIR" -pass "file:$CLE_CHIFFREMENT" || { echo "!! Déchiffrement impossible." >&2; exit 1; }
        echo "    déchiffrement : correct"
        ;;
    *)
        # Somme de contrôle enregistrée à la sauvegarde (fichiers non chiffrés du dossier des sauvegardes).
        if [ -f "$(dirname "$FICHIER")/SHA256SUMS" ]; then
            nom="$(basename "$FICHIER")"
            ligne="$(grep "  ${nom}\$" "$(dirname "$FICHIER")/SHA256SUMS" | tail -n 1 || true)"
            if [ -n "$ligne" ]; then
                ( cd "$(dirname "$FICHIER")" && echo "$ligne" | sha256sum -c - >/dev/null ) || { echo "!! Somme de contrôle différente : le fichier a été modifié ou abîmé." >&2; exit 1; }
                echo "    somme de contrôle : correcte"
            fi
        fi
        ;;
esac
gzip -t "$CLAIR" || { echo "!! Fichier compressé corrompu." >&2; exit 1; }
echo "    compression : correcte"

if [ -z "$SANS_RESTAURATION" ]; then
    echo "==> Restauration d'essai dans une base temporaire (${BASE_VERIF})..."
    $DOCKER_CMD exec -T db psql -U "$DB_USER" -d postgres -v ON_ERROR_STOP=1 -q -c "CREATE DATABASE \"${BASE_VERIF}\" OWNER \"${DB_USER}\";"
    gzip -dc "$CLAIR" | $DOCKER_CMD exec -T db psql -U "$DB_USER" -d "$BASE_VERIF" -v ON_ERROR_STOP=1 -q >/dev/null
    TABLES="$($DOCKER_CMD exec -T db psql -U "$DB_USER" -d "$BASE_VERIF" -tA -c "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';" | tr -d '[:space:]')"
    MIGRATIONS="$($DOCKER_CMD exec -T db psql -U "$DB_USER" -d "$BASE_VERIF" -tA -c "SELECT count(*) FROM django_migrations;" | tr -d '[:space:]')"
    [ "${TABLES:-0}" -gt 10 ] && [ "${MIGRATIONS:-0}" -gt 0 ] || { echo "!! La base restaurée est vide ou incomplète (tables : ${TABLES:-0}, migrations : ${MIGRATIONS:-0})." >&2; exit 1; }
    echo "    base restaurée : ${TABLES} tables, ${MIGRATIONS} migrations appliquées"
fi
echo "==> Sauvegarde utilisable."
