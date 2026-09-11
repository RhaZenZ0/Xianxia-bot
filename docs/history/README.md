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
- `MAIN_SPLIT_PLAN.md` — the plan by which the 13,000-line `main.py` became `app/bot/`; the module docstrings
  still cite their phase of it.
