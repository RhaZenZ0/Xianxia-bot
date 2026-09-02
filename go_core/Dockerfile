FROM golang:1.26-bookworm AS build
WORKDIR /src
RUN apt-get update \
    && apt-get install -y --no-install-recommends libsqlite3-dev \
    && rm -rf /var/lib/apt/lists/*
# Preserve the real go_core/... layout (rather than flattening it straight
# into /src) and copy content/ in alongside it at the same relative depth.
# The test suite locates content/world.json by walking up three directories
# from each _test.go file's own path (go_core/internal/<pkg>/x_test.go ->
# go_core/internal -> go_core -> repo root -> content/world.json) - that only
# resolves correctly if go_core keeps its own directory here too.
COPY go_core/go.mod ./go_core/go.mod
COPY go_core/cmd ./go_core/cmd
COPY go_core/internal ./go_core/internal
COPY content ./content
WORKDIR /src/go_core
RUN CGO_ENABLED=1 go test ./...
RUN CGO_ENABLED=1 go build -trimpath -ldflags="-s -w" -o /out/xianxia-engine ./cmd/xianxia-core

FROM debian:bookworm-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libsqlite3-0 wget gosu \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 xianxia \
    && useradd --system --uid 10001 --gid xianxia --home-dir /nonexistent xianxia
COPY --from=build /out/xianxia-engine /usr/local/bin/xianxia-engine
COPY content/world.json /app/content/world.json
COPY go_core/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh
# Stays root here on purpose - docker-entrypoint.sh needs it to fix up the
# bind-mounted data directory's ownership before dropping to xianxia (uid
# 10001, pinned above) via gosu. See docker-entrypoint.sh for why.
EXPOSE 8081
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["/usr/local/bin/xianxia-engine"]
