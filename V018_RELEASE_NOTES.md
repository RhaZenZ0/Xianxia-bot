# Xianxia RP v0.18 Release Notes

Release date: 2026-08-30  
Schema: 22

## Authority completion

v0.18 completes the staged migration of gameplay authority into Go.

- Forage resolves from canonical actor/location/world data.
- Crafting accepts the canonical recipe contract and derives profession, facility, cost, roll,
  output, quality, toxicity, and progression in Go.
- Companion/artifact mutations use canonical living-actor, location, cooldown, inventory, encounter,
  access, and progression state.
- Authoritative gameplay mutations and queries reject caller-owned current `game_minute`; Go derives
  canonical current time. Explicit scheduler/import/simulation event time remains protocol data where
  historically required.
- Player and NPC lifespan projection, NPC bootstrap age, and natural old-age death share one Go model.

## Roads, travel, and caravans

- Direct and multi-hop routing is server-owned.
- Per-leg duration, danger, encounters, travel costs, discovery, and transit timing are canonical.
- Transit locks prevent acting at a destination before canonical arrival.
- Caravans use canonical route, ETA, operating cost, risk, payout basis, and settlement.
- Caravan quantity/escort bounds and living-actor checks are enforced at the Go boundary.

## Discord setup and Python cleanup

- Discord guild channel/category setup is owned by the admin dashboard.
- The bot no longer creates guild text channels/categories or repairs channel permission overwrites.
- Private gameplay threads inside configured channels remain supported.
- Obsolete Python lifespan authority modules and their hidden test-support fallback were removed.

## Release security hardening

- Standalone Go engine defaults to `127.0.0.1:8081` instead of all interfaces.
- Docker continues to bind the engine inside the private Compose network.
- Every Go JSON mutation/data endpoint has a request-body size limit.
- JSON decoding rejects trailing extra values.
- SQLite busy timeout is configured before journal-mode setup, preventing false readiness failures
  during concurrent connection pressure.
- `python-dotenv` now requires 1.2.2+, excluding the patched 2026 symlink-overwrite advisory range.
- Existing dashboard/control tokens remain constant-time compared and dashboard publishing remains
  loopback-only by default.

## Upgrade

The application migrates schema 21 to schema 22 automatically. The schema-22 migration canonicalizes
starter family homelands without teleporting existing characters.

Back up the database before upgrading. The bundled local updater performs a live authoritative backup
before replacing code and rolls back code/database on failed installation.
