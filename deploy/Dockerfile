# The backend (worker + dispatcher) as one container for a cloud server.
#   docker build --platform linux/amd64 -f deploy/Dockerfile -t jobpilot-backend .
#   docker run --env-file .env jobpilot-backend
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    JOBPILOT_IN_CONTAINER=1 \
    XDG_CACHE_HOME=/opt/cache \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

# Tectonic for resume PDFs: the self-contained (static) Linux build, pinned.
ARG TECTONIC_VERSION=0.17.0
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && curl -fsSL "https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%40${TECTONIC_VERSION}/tectonic-${TECTONIC_VERSION}-x86_64-unknown-linux-musl.tar.gz" \
       | tar -xz -C /usr/local/bin tectonic \
    && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv
WORKDIR /app

# Dependencies first, so code changes don't reinstall them.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --group backend --no-install-project

COPY src ./src
COPY config ./config
COPY prompts ./prompts
COPY templates ./templates
COPY deploy ./deploy
COPY Data/learning ./Data/learning
COPY Data/taxonomy ./Data/taxonomy
RUN uv sync --locked --no-dev --group backend

# Warm the caches at build time (network allowed only here): LaTeX packages for the resume template,
# and the local embedding model.
RUN tectonic deploy/sandbox/warm.tex --outdir /tmp && rm -f /tmp/warm.* \
    && .venv/bin/python -c "from jobpilot.llm.embeddings import default_embedder; import asyncio; asyncio.run(default_embedder().embed(['warm up']))"

RUN useradd --create-home --uid 10001 app \
    && mkdir -p var && chown -R app:app var /opt/cache \
    && chmod +x deploy/entrypoint.sh
USER app

CMD ["deploy/entrypoint.sh"]
