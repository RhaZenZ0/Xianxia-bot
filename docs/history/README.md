# History

The record of how the tree got here, kept for the archaeology and for old databases that still
upgrade through it. Nothing in this directory describes the current release; `VERSIONS.md` at the
repository root is the changelog, and the top-level `docs/` files are the living documents.

- `V015_*`, `V016_*`, `V017_*`, `V018_AUTHORITY_CLEANUP_ROADMAP.md`, `FINAL_AUTHORITY_PURGE_DELETION_LIST.md`,
  `PRE_FINAL_AUTHORITY_CLEANUP_STATUS.md` — the staged move of every game rule from Python into the Go
  engine, audit by audit.
- `V018_STAGE*_NOTES.md`, `V018_RELEASE_NOTES.md`, `V018_BUILD_HISTORY.md`, `STAGE.md` — the v0.18 authority
  stages and the consolidated validation record.
- `V019_RELEASE_NOTES.md` … `V025_RELEASE_NOTES.md`, `V023_1_REVIEW_FIXES.md`, `V019_24_RELEASE.txt` — per-release
  notes from the point-release era, superseded by the entries in `VERSIONS.md`.
- `CHANGELOG_0_18_TO_0_40.md` — the per-release changelog entries as written, v0.18 to v0.40.0, moved here
  at v1.0.0-rc.1 when `VERSIONS.md` was trimmed to a paragraph per minor.
- `CHANGELOG_1_0_0_RCS.md` — the same move at the next boundary: the entries for `v1.0.0-rc.1` to
  `v1.0.0-rc.59`, moved here at **v1.0.0**. 54 entries across 59 candidates; five rcs never got one.
- `MAIN_SPLIT_PLAN.md` — the plan by which the 13,000-line `main.py` became `app/bot/`; the module docstrings
  still cite their phase of it.
- `ROADMAP_1_0.md` — the road to 1.0, milestone by milestone with the test that gated each. Closed and moved
  here at **v1.0.0**: every row shipped, and the last of them is the release that retired the file. Two
  contract tests still cite the milestone they gate.
- `COMMISSIONS_DESIGN.md` — commissions and typed play as designed at v0.21.0, shipped at v0.21.1 and v0.22.0.
  Moved here at v1.0.0; `app/rules/commissions.py` and the Go action still cite its selection ladder.
- `PROFESSIONS_AUDIT.md` — the seven-line audit of the profession surface, closed at v1.0.0-rc.21 and moved
  here at v1.0.0. Nothing cites it; it is kept for the archaeology.
