#!/bin/sh
set -e

python manage.py wait_for_db
python manage.py migrate --noinput
# Point de départ de l'historique pour les enregistrements créés avant son
# activation (ne touche que ceux qui n'ont encore aucune entrée : idempotent).
python manage.py populate_history --auto
python manage.py collectstatic --noinput
# Signale (sans bloquer) les réglages de sécurité douteux dans `docker compose logs web`.
python manage.py verifier_securite || true

exec gunicorn config.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers "${GUNICORN_WORKERS:-3}"
