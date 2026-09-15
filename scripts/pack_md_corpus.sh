#!/usr/bin/env bash
# Wait for the bekendmakingen crawl to finish, run a final HTML->MD clean pass,
# then pack the MD-only national corpus (verordeningen/md + bekendmakingen/md +
# iv3 + _shared manifests, NO raw html) into a single zstd tarball for shipping
# to gojo. Pure local work; notifies on completion.
set -euo pipefail
G=/home/jouchuki2/hobby/capelle-deploy/data/groeikernen
REPO=/home/jouchuki2/hobby/capelle-deploy
OUT=/home/jouchuki2/hobby/capelle-deploy/data/md-corpus.tar.zst

echo "[pack] waiting for crawl to finish..."
while [[ "$(systemctl --user is-active bekendmakingen-crawl 2>/dev/null)" == "active" ]]; do
    sleep 30
done
echo "[pack] crawl done: $(tail -1 "$G/bekendmakingen-crawl.log")"

echo "[pack] final clean pass (idempotent, skips done)..."
python3 "$REPO/scripts/clean_bekendmakingen.py" >> "$G/bekendmakingen-clean.log" 2>&1 || true

# Coverage report
md=0; for d in "$G"/*/bekendmakingen/md; do [[ -n "$(ls -A "$d" 2>/dev/null)" ]] && md=$((md+1)); done
vo=0; for d in "$G"/*/verordeningen/md; do [[ -n "$(ls -A "$d" 2>/dev/null)" ]] && vo=$((vo+1)); done
echo "[pack] coverage: bekendmakingen/md=$md  verordeningen/md=$vo  (of 342)"

echo "[pack] building MD-only file list (md + iv3 + _shared, no html)..."
cd "$G"
# Per-gemeente md trees + iv3 + the _shared manifests; exclude all html dirs.
find . \( -path '*/md' -o -path '*/iv3' \) -type d -prune -print > /tmp/md-corpus-paths.txt
echo "_shared" >> /tmp/md-corpus-paths.txt

echo "[pack] tar | zstd-12 -> $OUT"
tar --files-from=/tmp/md-corpus-paths.txt -cf - \
  | zstd -12 -T0 -q -o "$OUT" -f

echo "[pack] DONE: $OUT  size=$(du -h "$OUT" | cut -f1)"
echo "[pack] sha256: $(sha256sum "$OUT" | cut -d' ' -f1)"
