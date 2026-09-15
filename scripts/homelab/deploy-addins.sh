#!/usr/bin/env bash
# =============================================================================
# scripts/deploy-addins.sh
# -----------------------------------------------------------------------------
# Build the Oxes Office add-in task-pane bundles (Word / PowerPoint / Excel)
# and ship them to the dedicated static host (addins-host, served at
# addins.oxesgov.nl) under /opt/addins/<host>/.
#
# Layout shipped (matches each manifest's SourceLocation https://addins.oxesgov.nl/<host>/taskpane.html):
#   /opt/addins/word/{taskpane.html,assets/...,manifest.xml}
#   /opt/addins/powerpoint/{...}
#   /opt/addins/excel/{...}
#
# Transport mirrors deploy-frontend-oxes.sh: tar -> scp to the LXC hub -> stream
# into the container via `lxc exec ... cat` (NOT `lxc file push`) -> atomic swap.
# Power BI is distributed as a .pbiviz (import into the Power BI service), NOT
# served here.
#
# Env:
#   CAPELLE_LAN_HOST   LXC hub (default: gojo)
#   ADDINS_CONTAINER   target container (default: addins-host)
# Flags: --no-build, --dry-run
# =============================================================================

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LAN_HOST="${CAPELLE_LAN_HOST:-gojo}"
CONTAINER="${ADDINS_CONTAINER:-addins-host}"
HOSTS=(word powerpoint excel)
TAG="[deploy-addins]"
NO_BUILD=0; DRY_RUN=0
for a in "$@"; do case "$a" in
  --no-build) NO_BUILD=1 ;;
  --dry-run)  DRY_RUN=1 ;;
  *) echo "$TAG unknown arg: $a" >&2; exit 2 ;;
esac; done

log() { echo "$TAG $*" >&2; }
run() { if [[ "$DRY_RUN" == 1 ]]; then echo "$TAG [dry-run] $*" >&2; else "$@"; fi; }

# ---- 1. build each add-in --------------------------------------------------
if [[ "$NO_BUILD" == 0 ]]; then
  log "building add-in bundles (pnpm)..."
  ( cd "$REPO_ROOT/addins" && run pnpm install --silent && for h in "${HOSTS[@]}"; do
      log "  build $h"; run pnpm -C "$h" build >&2
    done )
else
  log "--no-build: using existing dist/"
fi

# ---- 2. assemble staging tree ---------------------------------------------
STAGE="$(mktemp -d)"; trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$STAGE/addins"
for h in "${HOSTS[@]}"; do
  dist="$REPO_ROOT/addins/$h/dist"
  [[ -d "$dist" ]] || { echo "$TAG FATAL: $dist missing (build first)" >&2; exit 1; }
  mkdir -p "$STAGE/addins/$h"
  run cp -a "$dist/." "$STAGE/addins/$h/"
  # ship the manifest alongside the bundle for easy sideload/admin grab
  [[ -f "$REPO_ROOT/addins/$h/manifest.xml" ]] && run cp "$REPO_ROOT/addins/$h/manifest.xml" "$STAGE/addins/$h/manifest.xml"
done
TGZ="$STAGE/addins.tgz"
run tar -C "$STAGE/addins" -czf "$TGZ" .
log "packed $(du -h "$TGZ" 2>/dev/null | cut -f1 || echo '?') -> hosts: ${HOSTS[*]}"

# ---- 3. ship to the container + atomic swap -------------------------------
if [[ "$DRY_RUN" == 1 ]]; then
  log "[dry-run] would: scp $TGZ -> $LAN_HOST:/tmp, stream into $CONTAINER, atomic-swap /opt/addins"
  log "[dry-run] done"; exit 0
fi
REMOTE_TGZ="/tmp/addins-$$.tgz"
scp -q "$TGZ" "$LAN_HOST:$REMOTE_TGZ"
ssh "$LAN_HOST" "cat $REMOTE_TGZ | lxc exec $CONTAINER -- bash -c 'cat > /tmp/addins.tgz'"
ssh "$LAN_HOST" "rm -f $REMOTE_TGZ"
ssh "$LAN_HOST" "lxc exec $CONTAINER -- bash -s" <<'REMOTE'
set -euo pipefail
cd /opt
rm -rf addins.new && mkdir addins.new
tar -xzf /tmp/addins.tgz -C addins.new
# keep the landing page
printf '<!doctype html><meta charset=utf-8><title>Oxes add-ins</title><p>Oxes add-in host.\n' > addins.new/index.html
[[ -d addins ]] && mv addins "addins.bak-$(date +%s)"
mv addins.new addins
rm -f /tmp/addins.tgz
# prune old backups (keep newest 3)
ls -1dt addins.bak-* 2>/dev/null | tail -n +4 | xargs -r rm -rf
REMOTE

# ---- 4. verify -------------------------------------------------------------
log "verifying..."
for h in "${HOSTS[@]}"; do
  code=$(ssh "$LAN_HOST" "lxc exec $CONTAINER -- curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/$h/taskpane.html" 2>/dev/null || echo "000")
  log "  /$h/taskpane.html -> $code"
done
log "deploy complete -> $CONTAINER:/opt/addins"
