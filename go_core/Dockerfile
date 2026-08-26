FROM golang:1.26-bookworm AS build
WORKDIR /src
RUN apt-get update \
    && apt-get install -y --no-install-recommends libsqlite3-dev \
    && rm -rf /var/lib/apt/lists/*
COPY go_core/go.mod ./go.mod
COPY go_core/cmd ./cmd
COPY go_core/internal ./internal
RUN CGO_ENABLED=1 go test ./...
RUN CGO_ENABLED=1 go build -trimpath -ldflags="-s -w" -o /out/xianxia-engine ./cmd/xianxia-core

FROM debian:bookworm-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libsqlite3-0 wget \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system xianxia \
    && useradd --system --gid xianxia --home-dir /nonexistent xianxia
COPY --from=build /out/xianxia-engine /usr/local/bin/xianxia-engine
COPY content/world.json /app/content/world.json
USER xianxia
EXPOSE 8081
ENTRYPOINT ["/usr/local/bin/xianxia-engine"]
