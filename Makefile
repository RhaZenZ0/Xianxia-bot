PYTHON ?= python3
GO ?= go

.PHONY: install-dev lock test test-python test-go lint format-check check docker-build

install-dev:
	$(PYTHON) -m pip install -r requirements-dev.txt

# requirements.lock is what the Dockerfile installs (under --require-hashes).
# Regenerate it after any change to requirements.txt; needs uv.
lock:
	uv pip compile requirements.txt --generate-hashes --python-version 3.12 --python-platform linux --custom-compile-command "make lock" -o requirements.lock

test: test-python test-go

test-python:
	$(PYTHON) -m pytest -q

test-go:
	cd go_core && CGO_ENABLED=1 $(GO) test ./...

lint:
	$(PYTHON) -m ruff check app scripts
	cd go_core && $(GO) vet ./...

format-check:
	@test -z "$$(gofmt -l go_core)" || (echo 'Go files need gofmt:'; gofmt -l go_core; exit 1)

check: lint format-check test

docker-build:
	docker build -f Dockerfile -t xianxia-bot:dev .
	docker build -f go_core/Dockerfile -t xianxia-engine:dev .