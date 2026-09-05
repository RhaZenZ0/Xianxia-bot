PYTHON ?= python3
GO ?= go

.PHONY: install-dev test test-python test-go lint format-check check docker-build

install-dev:
	$(PYTHON) -m pip install -r requirements-dev.txt

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