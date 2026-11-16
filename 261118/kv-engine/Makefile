GO ?= go
GOFMT ?= gofmt

.PHONY: check fmt fmt-check vet test build
check: fmt-check vet test build

fmt:
	$(GOFMT) -w cmd internal

fmt-check:
	@files="$$($(GOFMT) -l cmd internal)" || exit $$?; test -z "$$files" || { echo "$$files"; exit 1; }

vet:
	$(GO) vet ./...

test:
	$(GO) test ./...

build:
	$(GO) build -o bin/kv ./cmd/kv
