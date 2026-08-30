# v0.18 Stages 6–8 Implementation Notes

## Stage 6

- Added shared Go lifespan authority used by player/NPC projection, NPC bootstrap, and old-age death.
- Restored bounded richer high-realm NPC bootstrap ages.
- Added multi-hop canonical road routing with server-derived duration, danger, encounters, costs,
  discovery, and transit enforcement.
- Added canonical caravan routing/ETA/operating costs.
- Hardened caravan dispatch with Go-side quantity/escort bounds and living-actor enforcement.

## Stage 7

- Discord guild channel/category creation is dashboard-owned.
- Bot setup validates/binds existing configured channels.
- Removed channel permission-overwrite repair and startup realm-channel auto-provision behavior.
- Private gameplay threads remain supported.

## Stage 8

- Removed obsolete Python lifespan authority modules.
- Migrated lifespan consumers to Go.
- Removed stale test-support dependency on the deleted Python lifespan model.
- Kept Python orchestration/read surfaces where they do not duplicate Go mechanical authority.

See `V018_STAGE678_VALIDATION.md` and `V018_STAGE678_ADVERSARIAL_AUDIT.md`.
