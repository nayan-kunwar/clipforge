FROM python:3.12-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        fonts-liberation \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md requirements.txt ./
COPY src ./src

# Core install only. Add extras at build time if needed:
#   docker build --build-arg EXTRAS='[all]'
ARG EXTRAS=""
RUN pip install --no-cache-dir ".${EXTRAS}"

WORKDIR /data
ENTRYPOINT ["clipforge"]
CMD ["--help"]
