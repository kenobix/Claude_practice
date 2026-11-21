#!/bin/sh
set -eu
base=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
case "${1-}" in go|gofmt) tool=$1; shift ;; *) echo 'Usage: go-env.sh go|gofmt [args...]' >&2; exit 2 ;; esac
root="$base/.tools/go1.27.4/go"
if [ ! -x "$root/bin/$tool" ]; then
    echo 'Run: sh scripts/setup-go.sh (from kv-engine)' >&2; exit 1
fi
export GOROOT="$root"
export GOTOOLCHAIN=local
export GOCACHE="$base/.tools/cache"
export GOPATH="$base/.tools/gopath"
export PATH="$root/bin:$PATH"
exec "$root/bin/$tool" "$@"
