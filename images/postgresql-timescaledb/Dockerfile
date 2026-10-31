# CloudNativePG's PostgreSQL image plus TimescaleDB with the Timescale License
# (TSL), which adds compression, continuous aggregates and retention policies.
# CloudNativePG only publishes the Apache-only build (timescaledb-oss).
# Used by the tsdb cluster (apps/tsdb); built by .github/workflows/tsdb-image.yaml.
#
# The packages come from Timescale's own apt repository, verified with its
# signing key (fingerprint pinned below). Only the extension files are copied
# into the final image, so it is the CNPG base plus timescaledb*.so/.sql.
ARG BASE=ghcr.io/cloudnative-pg/postgresql:18.6-202610190957-standard-trixie@sha256:d72ace96f5803b12c341bb5dac48532e8c1f92f69e8a717a59d779bfc038dea9
# renovate: datasource=github-releases depName=timescale/timescaledb
ARG TIMESCALEDB_VERSION=2.31.0

FROM ${BASE} AS builder
ARG PG_MAJOR=18
ARG TIMESCALEDB_VERSION
ARG TIMESCALE_KEY_FINGERPRINT=1005FB68604CE9B8F6879CF759F18EDF47F24417
USER 0
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl gnupg; \
    curl -fsSL https://packagecloud.io/timescale/timescaledb/gpgkey -o /tmp/timescale.asc; \
    gpg --show-keys --with-colons /tmp/timescale.asc | grep -q "^fpr:::::::::${TIMESCALE_KEY_FINGERPRINT}:"; \
    gpg --dearmor -o /usr/share/keyrings/timescale.gpg /tmp/timescale.asc; \
    . /etc/os-release; \
    echo "deb [signed-by=/usr/share/keyrings/timescale.gpg] https://packagecloud.io/timescale/timescaledb/debian/ ${VERSION_CODENAME} main" \
      > /etc/apt/sources.list.d/timescale.list; \
    apt-get update; \
    version=$(apt-cache madison "timescaledb-2-postgresql-${PG_MAJOR}" | awk -v v="${TIMESCALEDB_VERSION}~" 'index($3, v) == 1 { print $3; exit }'); \
    test -n "$version"; \
    apt-get install -y --no-install-recommends \
      "timescaledb-2-loader-postgresql-${PG_MAJOR}=${version}" \
      "timescaledb-2-postgresql-${PG_MAJOR}=${version}"; \
    mkdir -p /out/lib /out/extension /out/licenses; \
    cp -a /usr/lib/postgresql/${PG_MAJOR}/lib/timescaledb*.so /out/lib/; \
    cp -a /usr/share/postgresql/${PG_MAJOR}/extension/timescaledb* /out/extension/; \
    for pkg in timescaledb-2-postgresql-${PG_MAJOR} timescaledb-2-loader-postgresql-${PG_MAJOR}; do \
      if [ -f /usr/share/doc/$pkg/copyright ]; then cp /usr/share/doc/$pkg/copyright /out/licenses/$pkg; fi; \
    done

FROM ${BASE}
ARG PG_MAJOR=18
ARG TIMESCALEDB_VERSION
USER 0
COPY --from=builder /out/lib/ /usr/lib/postgresql/${PG_MAJOR}/lib/
COPY --from=builder /out/extension/ /usr/share/postgresql/${PG_MAJOR}/extension/
COPY --from=builder /out/licenses/ /usr/share/doc/timescaledb/
LABEL org.opencontainers.image.source="https://github.com/FruitieX/homelab" \
      org.opencontainers.image.description="CloudNativePG PostgreSQL ${PG_MAJOR} with TimescaleDB ${TIMESCALEDB_VERSION} (TSL)" \
      io.timescale.timescaledb.version="${TIMESCALEDB_VERSION}"
USER 26
