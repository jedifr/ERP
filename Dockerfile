# syntax=docker/dockerfile:1

# --- Étape de build -----------------------------------------------------
# Inclut les outils de compilation et les en-têtes GEOS/libpq : nécessaire si aucune roue
# (wheel) Python précompilée n'existe pour l'architecture cible (ex. certains NAS ARM), auquel
# cas pip compile Shapely/psycopg2 depuis les sources. Sans effet (juste ignoré) si des roues
# précompilées existent pour l'architecture — ce qui est le cas la plupart du temps sur x86_64.
FROM python:3.11-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libgeos-dev \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# --- Image finale ---------------------------------------------------------
FROM python:3.11-slim

# Bibliothèques d'exécution (GEOS pour Shapely, au cas où il aurait fallu compiler depuis les
# sources à l'étape précédente ; psycopg2-binary embarque déjà sa propre libpq).
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgeos-dev \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 erp

COPY --from=builder /install /usr/local

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SECRET_KEY=build-time-placeholder-non-utilise-a-l-execution

WORKDIR /app
COPY . .
RUN mkdir -p /app/media /app/staticfiles \
    && python manage.py collectstatic --noinput \
    && chown -R erp:erp /app

USER erp

EXPOSE 8000

ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "60"]
