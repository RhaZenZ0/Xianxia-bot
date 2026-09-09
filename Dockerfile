# Pinned by digest as well as tag (v0.29.0). A tag is a moving pointer; the
# digest is the exact multi-arch index that was reviewed. To move it, look up
# the new index digest (`docker buildx imagetools inspect python:3.12-slim`)
# and change both lines together.
FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea

LABEL org.opencontainers.image.title="Jade Meridian Realm Xianxia Discord Bot" \
      org.opencontainers.image.version="0.29.0"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt

WORKDIR /app

# TLS trust store, made explicit for openai 3.x.
#
# openai 3.x replaced httpx with HTTPX2, which verifies certificates against the
# OPERATING SYSTEM trust store instead of the certifi bundle httpx 0.x used. On
# python:*-slim that store is whatever ca-certificates provides, so it is
# installed and then asserted here rather than assumed.
#
# This matters more than it looks. If the bundle were missing, every OpenRouter
# call would fail with a TLS error and narration would degrade SILENTLY to
# procedural prose - nothing raises anywhere, because narration is descriptive
# only and canonical mechanics are resolved before it runs. Players would notice
# the prose going flat; no log would say why. The `test -s` turns that into a
# loud build failure instead. go_core/Dockerfile already does the same thing for
# the same reason.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && update-ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && test -s /etc/ssl/certs/ca-certificates.crt

# requirements.txt says what the bot needs; requirements.lock says exactly
# which files satisfy it, with a SHA-256 for every wheel and sdist, transitive
# dependencies included. The image installs the lock under --require-hashes,
# so a package that changes on the index without a version bump - or a
# resolver that quietly picks a newer transitive release - fails the build
# instead of shipping. Regenerate with `make lock` after editing requirements.txt.
COPY requirements.txt requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

COPY . .

# Drop out of root. The Go engine already runs as uid 10001, but the three Python
# services (bot, db-init, dashboard) had no USER at all and so ran as root -
# including the dashboard, which is the only network-facing one.
#
# These containers mount no volumes: Go owns SQLite and Python reaches it over
# GAME_ENGINE_URL, so there is no host-owned path that has to stay writable.
# /app/data is created and handed over anyway because Database.__init__ calls
# path.parent.mkdir() unconditionally, before it checks whether the Go transport
# is configured - without this the three services would die at startup on a
# PermissionError for a directory they then never use.
RUN groupadd --system --gid 10001 xianxia \
    && useradd --system --uid 10001 --gid xianxia --home-dir /app --no-create-home xianxia \
    && mkdir -p /app/data \
    && chown -R xianxia:xianxia /app/data
USER 10001:10001

HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
    CMD ["python", "-m", "app.ops.healthcheck"]

CMD ["python", "-m", "app.bot"]
