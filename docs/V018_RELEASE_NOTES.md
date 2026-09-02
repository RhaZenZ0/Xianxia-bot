# Xianxia RP v0.18 Release Notes

Release date: 2026-08-30  
Schema: 24

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

## Samsara dynasty investigation and claims patch

Schema 23 persists cross-incarnation dynasty history. Schema 24 makes that history playable:

- archive, ancestral-ruin, last-heir-tomb, and surviving-retainer leads are generated per investigated transition;
- persistent investigation quests corroborate documentary, physical, tomb, and witness evidence;
- confirmed surviving bloodlines can assert inheritance without copying old rank or resources into the new realm;
- fallen or extinct historical houses can pursue dynasty restoration;
- revenge is evidence-gated and requires a corroborated hostile culprit;
- extinct replacement houses can be challenged over legacy, archives, property, or recognition without becoming blood descendants;
- contested restoration, revenge, and replacement claims use persistent multi-round conflict state.


## GM dashboard system coverage

The GM dashboard now exposes the newer v0.18 systems instead of limiting observability to the older NPC/sect/event views. Dedicated read-only endpoints and tabs cover cultivation aptitudes, crafting/assets, exploration/travel, dynamic economy, and Samsara dynasty investigation/claims.

`/api/capabilities` publishes the backend view/API contract and table-coverage state. Regression tests cross-check browser API references against the backend registry, verify every navigation item has a loader, and perform authenticated HTTP smoke requests against every dashboard view endpoint. This turns frontend/backend drift into a test failure rather than a broken production tab.

## Upgrade

The application migrates supported older schemas through schema 24 automatically. Schema 22 canonicalizes
starter-family homelands, schema 23 adds persistent Samsara dynasty history, and schema 24 adds ancestral
investigation sites/quests plus dynasty claims and conflicts. Existing player locations are not teleported.

Back up the database before upgrading. The bundled local updater performs a live authoritative backup
before replacing code and rolls back code/database on failed installation.

## Dynasty risk-resolution follow-up (2026-08-31)

The ancestral dynasty system now uses authoritative server-side 2d10 resolution for investigation
quests and conflict rounds. Lead danger is a real difficulty input rather than display-only flavor;
physical high-danger failures can cause investigation setbacks and non-lethal vitality loss.
Contested dynasty rounds now resolve player and opposition checks before awarding pressure, and
additional completed ancestral quests reduce the opposition baseline. No schema migration is
required.
