# v0.18 Final Release Security / Load-Test Audit

Audit date: 2026-08-30

## Result

No unresolved release-blocking authority, HTTP-boundary, setup, or load-test issue remains after the
fixes below.

## Findings closed during the final release gate

### HIGH — Standalone Go engine default exposed the internal raw-DB trust boundary

**Before:** when `ENGINE_ADDR` / `CORE_ADDR` were unset, the engine defaulted to `:8081`, which binds
all interfaces. The engine intentionally exposes raw SQLite session/batch endpoints for trusted
Python services, so a bare-metal or accidentally published deployment could expose an unauthenticated
internal data plane.

**Fix:** the standalone default is now `127.0.0.1:8081`.

**Compatibility:** Docker Compose already supplies `ENGINE_ADDR=:8081` and does not publish the engine
port to the host; container-to-container operation is unchanged.

### MEDIUM — Several Go JSON endpoints lacked request-body limits

**Before:** gameplay action/bootstrap already used `http.MaxBytesReader`, but simulation run/force,
database session execute/script, batch, and maintenance JSON endpoints did not consistently cap body
size.

**Fix:** bounded request bodies now cover all Go JSON mutation/data endpoints with endpoint-appropriate
limits.

**Regression:** new server tests verify oversized simulation and maintenance requests fail with
HTTP 400.

### MEDIUM — JSON decoder accepted trailing values

**Before:** the common decoder consumed the first JSON value and did not prove EOF, allowing ambiguous
bodies such as `{...} {...}`.

**Fix:** the common decoder now requires exactly one JSON value and rejects trailing content.

**Probe:** a live engine request containing two JSON values returns HTTP 400.

### MEDIUM — Readiness produced false 503s under concurrent probe pressure

**Before load test:** `/readyz` returned 121 transient HTTP 503 responses out of 1,200 concurrent
requests. Connection setup attempted `PRAGMA journal_mode=WAL` before configuring SQLite
`busy_timeout`, so simultaneous opens could observe transient lock failures.

**Fix:** connection-local `busy_timeout=10000` is applied before journal-mode verification/setup.

**Post-fix load test:** 1,200 / 1,200 readiness requests return HTTP 200.

### MEDIUM — `python-dotenv` dependency range permitted a known vulnerable release

**Before:** `python-dotenv>=1.0,<2` permitted versions affected by GHSA-mf9w-mj56-hr94 /
CVE-2026-28684.

**Fix:** minimum raised to `python-dotenv>=1.2.2,<2`, the upstream patched version floor.

## Runtime security checks

- Dashboard token must be a non-placeholder value of at least 20 characters.
- Dashboard Basic credentials use constant-time comparison.
- Dashboard host publishing defaults to `127.0.0.1` in Compose.
- Bot control endpoint requires `X-Xianxia-Control` and caps request bodies at 64 KiB.
- Go engine Docker service is `expose`-only, not host-published.
- Go runtime container runs as the non-root `xianxia` user.
- No active bot channel/category auto-provision or permission-repair mutations remain.
- No hard-coded Discord/OpenRouter/OpenAI/dashboard credential values found by static scan.
- No Python `eval`/`exec`, pickle, `os.system`, or `subprocess(..., shell=True)` patterns found in
  active application/Go source.

## Dependency observations

- `go list -m all` contains only the local `xianxia/core` module; there are no third-party Go modules.
- `httpx>=0.28,<1` is above the historical HTTPX improper-input-validation affected range fixed in
  older 0.23-era releases.
- Targeted current advisory lookup identified the python-dotenv advisory above and its 1.2.2 fix.
- Automated `pip-audit` / `govulncheck` binaries were not installed in the execution environment, so
  this audit does not claim a complete transitive SBOM/vulnerability-database scan.
- Python base images and several dependency ranges remain floating within bounded version families;
  this favors receiving patch updates but is less byte-for-byte reproducible than digest/hash locking.

## Load / stress results

All measurements are from the local execution environment, not target QNAP hardware.

### HTTP liveness

```text
requests:      3000
concurrency:   64
responses:     3000 x HTTP 200
throughput:    ~2170 req/s
p50 latency:   17.72 ms
p95 latency:   50.89 ms
p99 latency:   69.53 ms
max latency:   97.39 ms
```

### HTTP readiness after fix

```text
requests:      1200
concurrency:   32
responses:     1200 x HTTP 200
throughput:    ~1831 req/s
p50 latency:   11.61 ms
p95 latency:   29.49 ms
p99 latency:   41.76 ms
max latency:   67.89 ms
```

### Authority stress

```text
go test ./internal/game -run 'TestStage5|TestStage6|TestCaravan' -count=25
PASS
```

### Race detector

```text
go test -race ./...
PASS
```

### Adversarial HTTP probes

```text
trailing second JSON value      -> HTTP 400
oversized maintenance body      -> HTTP 400
wrong method on simulation API  -> HTTP 405
```

## Scope limitations

This is a release-engineering load/security audit, not a full external penetration test. It does not
simulate Discord gateway load, OpenRouter latency/rate limiting, target-QNAP disk contention, or a
production-sized long-lived SQLite database. Those should be observed after deployment with normal
metrics and backups enabled.

## Final assessment

v0.18 is suitable for release from the tested authority/security/load perspective. The internal Go
engine must remain private (loopback for standalone use or the private Compose network in Docker);
only the dashboard is intended for optional host publishing, loopback-bound by default.
