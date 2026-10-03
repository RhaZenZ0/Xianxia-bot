---
name: github-ci
description: How to verify a change in this repo - the full test suite runs in GitHub Actions (ci.yml), not locally; only fast, targeted checks run here, and the two playtests are the one thing that is always run locally and never in GitHub. Use before committing or pushing, when asked to run the tests or the suite, and when a CI check fails on a PR.
---

# The suite runs in GitHub; the playtests run here

`.github/workflows/ci.yml` is the full check suite, and it is the one place the whole of it runs:

| Job | What it runs |
|---|---|
| `python (1/4)` … `(4/4)` | four shards side by side, each `pytest -q` with `PYTEST_SHARD=<i>/4` over a quarter of the test files (shard 1 also runs `ruff check app scripts`) |
| `go` | `gofmt -l`, `go vet`, `staticcheck` and `govulncheck` at the Makefile's pins, `go test -race ./...` |
| `containers` | builds both images, then imports every bot service and boots the engine to `/livez` |

All of those start at the same time: none waits for another. `tests/conftest.py` deals the test
files round the shards (sorted, like cards), and `tests/python/unit/test_ci_shards.py` holds that
every file runs in exactly one. The shard count is the matrix list in `ci.yml` and nothing else.
To reproduce one shard's failure locally, run that test by node id, or the whole shard with
`PYTEST_SHARD=2/4 python -m pytest -q`.
| `pages` | only on a push to main, after the three above: builds the release-notes site from `VERSIONS.md` and deploys it to GitHub Pages |
| `release` | only on a `v*` tag, after the three above: manifest verify, archive, GitHub Release |

**Do not run `make check` or the whole `pytest -q` locally to verify a change.** It is slow here,
and a local green does not prove the Go job (the race detector, the pinned tool versions) or the
containers. Let GitHub run the suite.

## 1. Before pushing: targeted checks only

Run only what the change touches, so the push is not obviously red:

- **Python changed** - `python -m ruff check app scripts`, then the tests that exercise it:
  `python -m pytest -q <test files>`. Find them with a grep over `tests/` for the changed module,
  function or content key. Many gates here read source files by AST, so also grep `tests/` for the
  changed *file name*.
- **Go changed** - `gofmt -l go_core` (must print nothing), then
  `cd go_core && CGO_ENABLED=1 go vet ./<pkg>/... && CGO_ENABLED=1 go test ./<pkg>/... -run <Name>`
  for the touched package.
- **`content/world.json` changed** - `python -m json.tool content/world.json >/dev/null`, plus the
  content gates that name the key you touched.
- **`.github/workflows/ci.yml` changed** - the workflow file must still parse as YAML, and
  `tests/python/unit/test_release_channel.py`, `tests/python/contracts/test_security_defaults.py`,
  `test_deployment_hardening.py` and `test_env_migration.py` read it, so run those.
- **Any tracked file changed** - `python scripts/release_manifest.py --write`. The manifest covers
  the whole tree, `test_release_manifest.py` fails on a stale one, and the release job refuses to
  publish over it.

## 2. Push, then read CI

CI starts only for a **pull request** or a push to **main**. A push to a branch with no PR runs
nothing, so check whether the branch has a PR. If it has none, tell the user that and ask whether to
open one. Do not open a PR unasked.

With a PR open, read results through the GitHub MCP tools rather than guessing:

1. `mcp__github__actions_list` with `method: list_workflow_runs` and
   `workflow_runs_filter: {branch: <branch>}` gives the run for the current head (match `head_sha`).
2. `method: list_workflow_jobs` with the run id gives each job and the step it stopped at.
3. `mcp__github__get_job_logs` with `job_id`, `return_content: true` and `tail_lines: 80` gives the
   failure itself.

Prefer `subscribe_pr_activity` over polling: a failed check arrives as an event. Never wait with
`sleep`.

## 3. When a job fails

Reproduce the exact failure locally first: the single test by node id
(`pytest -q path::Class::test`, `go test ./pkg/... -run TestName`) or the failing step's command.
Then fix it, re-run that one check, and push. A failure is never a flake until it has been
reproduced on the base branch too.

Known CI-only trap: setup-go v7 exports `GOTOOLCHAIN=local`, and the pinned staticcheck needs a
newer Go than `go.mod`. That is why the two tool installs say `GOTOOLCHAIN=auto`; do not remove it.

## 4. The exception: the playtests never run in GitHub

`scripts/playtest_engine.py` and `scripts/playtest_discord.py` are not in CI and must not be added
to it. They need the Go engine built with CGO SQLite (`libsqlite3-dev`) and running beside them,
the Discord half boots the real bot inside SimCord (`requirements-dev.txt`), and each run takes
minutes of virtual time. CLAUDE.md says so: "It is a script, not CI".

Run them locally, and only locally:

```bash
python scripts/playtest_all.py                # both at once, the Discord sweep in 3 parts (about a third of the ~27 minutes run in turn)
python scripts/playtest_engine.py --launch    # builds and starts a scratch engine, drives every operation
python scripts/playtest_discord.py --launch   # the real bot under simulated Discord, every leaf pressed
```

Run them before a release, and whenever a change touches engine operations, hub pages, slash
commands, pickers or modals, because that wiring is what they prove and what the suite cannot see.
Neither asserts on dice. Report the pass and fail counts, and name each failing step with its
message. `tests/python/contracts/test_playtest_coverage.py`, which *is* in CI, holds both harnesses
to the live surface, so a new operation or leaf the playtest does not drive fails in GitHub even
though the playtest itself never runs there.
