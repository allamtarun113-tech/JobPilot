# Minimal Tectonic image for Linux hosts: run with --network none --read-only and a tmpfs work dir.
#   docker build -t jobpilot-tectonic infra/sandbox
#   docker run --rm --network none --read-only --tmpfs /tmp -v "$WORK:/work" jobpilot-tectonic \
#     tectonic --only-cached --untrusted /work/resume.tex
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && curl -fsSL https://drop-sh.fullyjustified.net | sh && mv tectonic /usr/local/bin/ \
    && apt-get purge -y curl && rm -rf /var/lib/apt/lists/*
ENV XDG_CACHE_HOME=/opt/tectonic-cache
# Warm the package cache at build time (network allowed only here) with the real template's packages.
COPY warm.tex /tmp/warm.tex
RUN tectonic /tmp/warm.tex && rm -f /tmp/warm.* && chmod -R a+rX /opt/tectonic-cache
USER nobody
WORKDIR /work
