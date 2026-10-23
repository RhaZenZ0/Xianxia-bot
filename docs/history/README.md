# History

The record of how the tree got here, kept for the archaeology and for old databases that still
upgrade through it. Nothing in this directory describes the current release; `VERSIONS.md` at the
repository root is the changelog, and the top-level `docs/` files are the living documents.

- `AUTHORITY_MIGRATION_0_15_TO_0_18.md` — the staged move of every game rule from Python into the Go
  engine, audit by audit, v0.15 to v0.18. Twenty-two files merged into one after v1.39.0, each kept
  whole under its original filename: the `V015_*`, `V016_*`, `V017_*` and `V018_*` checkpoint reviews,
  deletion lists, readiness audits, stage notes, build history and release notes, with
  `FINAL_AUTHORITY_PURGE_DELETION_LIST.md`, `PRE_FINAL_AUTHORITY_CLEANUP_STATUS.md` and `STAGE.md`.
- `RELEASE_NOTES_0_19_TO_0_25.md` — the per-release notes of the point-release era, superseded by the
  entries in `VERSIONS.md`. Nine files merged into one after v1.39.0, each kept whole under its
  original filename: `V019_RELEASE_NOTES.md` … `V025_RELEASE_NOTES.md`, `V023_1_REVIEW_FIXES.md`, and
  `V019_24_RELEASE.txt`, the release marker that shipped at the root of v0.19.24 as `RELEASE.txt`.
- `CHANGELOG_0_18_TO_0_40.md` — the per-release changelog entries as written, v0.18 to v0.40.0, moved here
  at v1.0.0-rc.1 when `VERSIONS.md` was trimmed to a paragraph per minor.
- `CHANGELOG_1_0_0_RCS.md` — the same move at the next boundary: the entries for `v1.0.0-rc.1` to
  `v1.0.0-rc.59`, moved here at **v1.0.0**. 54 entries across 59 candidates; five rcs never got one.
- `TODO_FIXED.md` — the closed half of the punch list: every entry ever fixed, newest first, moved
  across from `docs/TODO.md` and never deleted (v1.34.0).
- `MAIN_SPLIT_PLAN.md` — the plan by which the 13,000-line `main.py` became `app/bot/`; the module docstrings
  still cite their phase of it.
- `ROADMAP_1_0.md` — the road to 1.0, milestone by milestone with the test that gated each. Closed and moved
  here at **v1.0.0**: every row shipped, and the last of them is the release that retired the file. Two
  contract tests still cite the milestone they gate.
- `COMMISSIONS_DESIGN.md` — commissions and typed play as designed at v0.21.0, shipped at v0.21.1 and v0.22.0.
  Moved here at v1.0.0; `app/rules/commissions.py` and the Go action still cite its selection ladder.
- `PROFESSIONS_AUDIT.md` — the seven-line audit of the profession surface, closed at v1.0.0-rc.21 and moved
  here at v1.0.0. Nothing cites it; it is kept for the archaeology.

The two changelogs and the roadmap cite the merged files by their old names, as they were written
(`docs/history/V020_RELEASE_NOTES.md`, `V018_BUILD_HISTORY.md`); each of those names is a section
heading inside the merged file that holds it.
