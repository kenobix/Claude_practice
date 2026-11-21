#!/usr/bin/env bash
set -eu
python3 --version
docker --version
docker compose version
# A nonzero exit means daemon access failed, not success.
docker info --format '{{.ServerVersion}}'
