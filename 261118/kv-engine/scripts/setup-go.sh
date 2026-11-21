#!/bin/sh
# Installs a verified toolchain inside this worktree only.
set -eu
base=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
target="$base/.tools/go1.27.4"
checksum=7226736c2339719cda28fbf3a54d71aa63bcefe376e98cef00034268dab24840
if [ "$(uname -s)/$(uname -m)" != Linux/x86_64 ]; then
    echo 'This setup supports Linux x86_64 only.' >&2; exit 1
fi
if [ -e "$target" ]; then
    [ -x "$target/go/bin/go" ] && [ "$(GOTOOLCHAIN=local "$target/go/bin/go" version)" = 'go version go1.27.4 linux/amd64' ] || {
        echo "Existing toolchain is unexpected; refusing to overwrite: $target" >&2; exit 1;
    }
    echo "Already installed: $target"; exit 0
fi
mkdir -p "$base/.tools"
staging=$(mktemp -d "$base/.tools/setup.XXXXXX")
trap 'rm -rf -- "$staging"' EXIT HUP INT TERM
if [ "$#" -gt 1 ]; then echo 'Usage: setup-go.sh [verified-source-archive]' >&2; exit 2; fi
if [ "$#" -eq 1 ]; then
    cp -- "$1" "$staging/go.tar.gz"
else
    curl --fail --location --connect-timeout 15 --max-time 180 \
        https://go.dev/dl/go1.27.4.linux-amd64.tar.gz -o "$staging/go.tar.gz"
fi
printf '%s  %s\n' "$checksum" "$staging/go.tar.gz" | sha256sum --check -
tar -C "$staging" -xzf "$staging/go.tar.gz"
[ "$(GOTOOLCHAIN=local "$staging/go/bin/go" version)" = 'go version go1.27.4 linux/amd64' ]
# Destination must not exist; never merge into an existing installation.
mv -T --no-clobber -- "$staging" "$target"
[ ! -d "$staging" ] || { echo 'Destination appeared during setup; refusing overwrite.' >&2; exit 1; }
trap - EXIT HUP INT TERM
rm -- "$target/go.tar.gz"
echo "Installed: $target/go"
