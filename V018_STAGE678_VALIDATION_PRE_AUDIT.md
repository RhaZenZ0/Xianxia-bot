# v0.18 Stage 6–8 Validation — Pre-Audit Checkpoint

Validation run after Stage 6 lifespan/road/caravan work, Stage 7 dashboard-owned Discord setup,
and Stage 8 Python authority cleanup.

## Go

```text
go test ./...
PASS

go vet ./...
PASS
```

## Python

```text
python -m pytest -q
239 passed, 78 subtests passed
```

## Stage 6 coverage

- one shared Go lifespan package for realm/stage ceilings, age evaluation, old-age expiry, and bootstrap age;
- player and NPC lifespan queries use the shared model;
- simulation NPC natural-death checks use the shared Go model;
- high-realm bootstrap ages are bounded by canonical realm lifespan;
- multi-hop road routing is Go-owned;
- road duration, danger, encounter rolls, travel costs, and arrival locks are server-owned;
- intermediate-route discovery is persisted;
- caravan route/ETA/cost resolution uses canonical roads.

## Stage 7 coverage

- bot source contains no `guild.create_text_channel` calls;
- bot source contains no `guild.create_category` calls;
- base and realm channel setup bind/validate existing Discord channels;
- channel creation is admin-dashboard-owned;
- private gameplay threads remain supported inside configured channels.

## Stage 8 coverage

- Python lifespan mechanics were removed;
- lifespan UI/read surfaces call Go `character.lifespan` / `npc.lifespan`;
- obsolete Python lifespan tests/fixtures were migrated away from `app.lifespan`;
- gameplay authority remains behind the Go engine boundary.

Schema version remains 22. Runtime release metadata remains v0.17 for this development checkpoint.
