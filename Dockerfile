# syntax=docker/dockerfile:1
FROM node:22-alpine AS web

WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        fonts-liberation \
        fonts-dejavu-core \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md requirements.txt ./
COPY src ./src

# Core web + upload extras by default. Add transcribe when needed:
#   docker build --build-arg EXTRAS=web,upload,transcribe -t clipforge .
ARG EXTRAS=web,upload
RUN pip install --no-cache-dir ".[${EXTRAS}]"

COPY --from=web /web/dist ./web/dist

RUN useradd --create-home --uid 10001 clipforge \
    && mkdir -p /data \
    && chown -R clipforge:clipforge /app /data

USER clipforge
ENV CLIPFORGE_DATA_DIR=/data \
    CLIPFORGE_WEB_DIST=/app/web/dist \
    PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["uvicorn", "clipforge.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
