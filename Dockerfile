FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# GEOS est nécessaire à Shapely (app `decoupe`, imbrication de pièces découpées). Une roue
# (wheel) précompilée existe pour la plupart des architectures (aucune compilation nécessaire),
# mais certains NAS ARM n'en ont pas : dans ce cas pip compile depuis les sources, ce qui exige
# les en-têtes GEOS. Le paquet `libgeos-dev` fournit aussi la bibliothèque d'exécution, donc
# utile même quand aucune compilation n'a lieu.
#
# WeasyPrint (modèles de documents PDF personnalisés, app `documents`) a besoin de Pango/HarfBuzz ;
# `fonts-liberation` fournit des polices métriquement compatibles Arial/Helvetica (sans police, les
# PDF personnalisés s'afficheraient avec une police de repli de l'image).
#
# OpenCascade (`cadquery-ocp-novtk`, lecture et dépliage des fichiers STEP du site client, app `web`) est livré en roue
# précompilée mais attend libGL et libX11 du système, même sans affichage.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgeos-dev \
        libgl1 \
        libx11-6 \
        libexpat1 \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libharfbuzz-subset0 \
        fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chmod +x entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]
