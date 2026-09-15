#!/usr/bin/env bash
# Fetch the prebuilt ohrs ("oh") binary from its GitHub release.
#
# This is the unified way to obtain ohrs — no local Rust toolchain, no build
# host, no Docker. It replaces the old "build in safetrace/openharness-rs then
# scp the target/release/oh" dance and the host-to-host copy in
# deploy/bootstrap-yuta.sh.
#
# Usage:
#   scripts/fetch-ohrs.sh [dest] [version]
#     dest     where to write the binary  (default: $XDG_CACHE_HOME/capelle/ohrs/oh)
#     version  release tag                (default: $OHRS_VERSION, else v1.0.0)
#
# Env overrides:
#   OHRS_VERSION   release tag (e.g. v1.0.0)
#   OHRS_FORCE=1   re-download even if the destination already exists
#
# On success the resolved binary path is printed to STDOUT (so callers can do
# `OHRS_BINARY="$(scripts/fetch-ohrs.sh)"`); all diagnostics go to STDERR.
#
# Linux x86_64 only — that is the single asset the release publishes.
set -euo pipefail

VERSION="${2:-${OHRS_VERSION:-v1.0.0}}"
DEFAULT_CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/capelle/ohrs"
DEST="${1:-$DEFAULT_CACHE/oh}"

REPO="jouchuki/ohrs"
ASSET="oh-x86_64-unknown-linux-gnu.tar.gz"
BASE="https://github.com/${REPO}/releases/download/${VERSION}"

log() { echo "fetch-ohrs: $*" >&2; }

if [[ "$(uname -m)" != "x86_64" ]]; then
  log "WARNING: this host is $(uname -m), but only x86_64 is published; continuing anyway."
fi

if [[ -x "$DEST" && "${OHRS_FORCE:-0}" != "1" ]]; then
  log "$DEST already present ($("$DEST" --version 2>/dev/null || echo 'version unknown')); use OHRS_FORCE=1 to refresh."
  echo "$DEST"
  exit 0
fi

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

log "downloading ${ASSET} @ ${VERSION} ..."
curl -fsSL "${BASE}/${ASSET}"        -o "${tmp}/${ASSET}"
curl -fsSL "${BASE}/${ASSET}.sha256" -o "${tmp}/${ASSET}.sha256"

log "verifying checksum ..."
( cd "$tmp" && sha256sum -c "${ASSET}.sha256" >&2 )

tar -xzf "${tmp}/${ASSET}" -C "$tmp"
if [[ ! -f "${tmp}/oh" ]]; then
  log "ERROR: archive did not contain an 'oh' binary."
  exit 1
fi

mkdir -p "$(dirname "$DEST")"
install -m 0755 "${tmp}/oh" "$DEST"
log "installed $("$DEST" --version 2>/dev/null || echo 'oh') -> ${DEST}"

echo "$DEST"
