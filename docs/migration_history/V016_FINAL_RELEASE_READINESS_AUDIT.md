# v0.16 Final Release-Readiness Audit

Audit date: 2026-08-29

## Verdict

**v0.16 release sign-off: PASS.** The release markers are promoted consistently to 0.16, the obsolete partial-build marker is removed from the shipping root, all four production-reachable Python RNG/authority blockers identified by the v0.15 final audit have been moved behind Go authority, and the requested Python gameplay implementation purge remains enforced by negative boundary contracts.

The remaining live Python `Database` mutators are an explicitly documented future-migration backlog. The v0.16 release criterion is that no confirmed Go-duplicated or statically unreachable Python gameplay mutator remains from this purge, not that every infrastructure/admin/support write has already been moved to Go.

## Blockers from the v0.15 audit

1. **Release identity was v0.14 / partial:** **CLOSED.** `VERSION`, `app/version.py`, Docker image label, Compose labels, health metadata, release tests, README, environment example, and user-facing version strings are now 0.16. `PARTIAL_BUILD.txt` has been removed from the release package; the v0.15 review documents remain under `docs/migration_history/`.
2. **Four production-reachable Python gameplay RNG paths:** **CLOSED.** NPC mood and clan bootstrap now execute in Go; forage outcome/profile RNG now executes in Go; child-root inheritance now executes in Go.
3. **Remaining Python `Database` mutators:** **RECLASSIFIED AS DOCUMENTED FUTURE MIGRATION.** The retained set is live/non-duplicated and is reviewed in `V016_REMAINING_DATABASE_MUTATORS.md`; zero statically unreachable write-like methods from the audited set remain.

## Authority checks

- `WorldSimulator` requires an engine; there is no Python no-engine gameplay fallback.
- Production has no `WorldSimulator._npc_mood`, `ensure_all_clans`, or `alchemy_forage_profile` implementation.
- Production has no `birthfamily.inherited_root` implementation.
- Character creation/reincarnation invoke the Go simulation bootstrap instead of a Python clan mutator.
- Python `/alchemy forage` does not supply TN, regional spirit resources, loot, or rare result to Go.
- Python family-child creation does not supply spiritual root, realm index, or phase to Go.
- Go rejects those forbidden client-supplied mechanical/outcome fields.
- New Go tests cover bootstrap ownership/idempotence, exact retainer totals, child-root authority rejection, and forage authority rejection/result ownership.
- The v0.15 purge targets (`CultivationService`, `SectService`, `ForbiddenArtsService`, direct `Database.create_character()` fixture path, deleted Go-owned simulation methods) remain absent.

## Production Python RNG review

Python modules still contain RNG helpers used by legacy/test-only content utilities (`birthfamily`, `samsara`, aptitude generation, creation UI rolls, and `World` random helper methods), plus non-gameplay uniqueness tokens in the database support layer. Static call-site review found no production callers for the legacy/test-only gameplay RNG entry points. The production-reachable RNG blockers identified in the prior audit are gone.

## Automated verification

- Python full suite: **222 passed + 39 subtests**.
- Go full suite: **PASS**.
- `go vet ./...`: **PASS**.
- Shell syntax (`startup.sh`, `stop.sh`, `update.sh`): **PASS**.
- `content/world.json`: **valid JSON**.
- Python compilation (`app`, `tests`): **PASS**.
- Docker Compose rendering: **SKIPPED** because Docker is not installed in the audit sandbox.

## Release identity

- Release: **0.16**
- Schema: **21**
- Schema migration in this release: **none**
- Release marker: `RELEASE.txt`

## Conclusion

The v0.16 source tree is internally consistent with the requested migration boundary and is ready to package as the final v0.16 release. Future authority work should start from the live/non-duplicated mutator list rather than deleting those methods without replacement.
