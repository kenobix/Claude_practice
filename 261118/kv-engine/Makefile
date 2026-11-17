GO ?= ./scripts/go-env.sh go
GOFMT ?= ./scripts/go-env.sh gofmt

.PHONY: check fmt fmt-check vet test build demo
check: fmt-check vet test build

fmt:
	$(GOFMT) -w cmd internal tests

fmt-check:
	@files="$$($(GOFMT) -l cmd internal tests)" || exit $$?; test -z "$$files" || { echo "$$files"; exit 1; }

vet:
	$(GO) vet ./...

test:
	$(GO) test ./...

build:
	$(GO) build -o bin/kv ./cmd/kv
	$(GO) build -o bin/kvbench ./cmd/kvbench

demo: build
	python3 scripts/demo.py
