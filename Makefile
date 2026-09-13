PYTHON ?= python3
GO ?= go

.PHONY: install-dev tools lock test test-python test-go lint format-check check docker-build

install-dev:
	$(PYTHON) -m pip install -r requirements-dev.txt

# staticcheck is what `make lint` gates the Go side on beyond `go vet`. Pinned,
# because an unpinned tool turns a new upstream release into a red build nobody
# asked for - the same reason the Dockerfile pins its base image by digest and
# requirements.lock carries hashes. CI installs this exact version.
STATICCHECK_VERSION ?= 2026.2.1

tools:
	$(GO) install honnef.co/go/tools/cmd/staticcheck@$(STATICCHECK_VERSION)

# requirements.lock is what the Dockerfile installs (under --require-hashes).
# Regenerate it after any change to requirements.txt; needs uv.
lock:
	uv pip compile requirements.txt --generate-hashes --python-version 3.12 --python-platform linux --custom-compile-command "make lock" -o requirements.lock

test: test-python test-go

test-python:
	$(PYTHON) -m pytest -q

test-go:
	cd go_core && CGO_ENABLED=1 $(GO) test ./...

# `go vet` finds none of the dead code staticcheck does: the v0.33.0 sweep
# deleted what tests kept alive, and six more names had crept back by v1.0.0
# with nothing calling them at all. staticcheck caught all six and vet caught
# none, so it is a gate rather than a suggestion - it fails the build, and it
# runs the whole default check set with no exclusions.
#
# It is required rather than skipped when absent: a lint step that quietly
# passes because a tool is missing is the failure mode this repo keeps fixing.
lint:
	$(PYTHON) -m ruff check app scripts
	cd go_core && $(GO) vet ./...
	@command -v staticcheck >/dev/null 2>&1 || { \
	  echo 'staticcheck is not on PATH. Install the pinned version with:'; \
	  echo '    make tools'; \
	  echo "(that is: $(GO) install honnef.co/go/tools/cmd/staticcheck@$(STATICCHECK_VERSION))"; \
	  exit 1; }
	cd go_core && CGO_ENABLED=1 staticcheck ./...

format-check:
	@test -z "$$(gofmt -l go_core)" || (echo 'Go files need gofmt:'; gofmt -l go_core; exit 1)

check: lint format-check test

docker-build:
	docker build -f Dockerfile -t xianxia-bot:dev .
	docker build -f go_core/Dockerfile -t xianxia-engine:dev .