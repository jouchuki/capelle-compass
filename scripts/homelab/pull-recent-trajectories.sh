#!/usr/bin/env bash
# Pull recent Capelle OHRS job trajectories/artifacts from production yuta LXCs.
#
# Defaults assume the current lab topology:
#   laptop -> ssh gojo -> lxc exec yuta-okkotsu/yuta-maki-zenin
#
# Usage:
#   scripts/pull-recent-trajectories.sh
#   LIMIT=50 scripts/pull-recent-trajectories.sh
#   OUT_DIR=ohrs-trajectories/schollevaar YUTAS="yuta-maki-zenin" scripts/pull-recent-trajectories.sh
#
# Env overrides:
#   SSH_HOST         default: gojo
#   YUTAS            default: "yuta-okkotsu yuta-maki-zenin"
#   REMOTE_JOBS_DIR  default: /tmp/capelle-jobs
#   LIMIT            default: 25, per yuta
#   OUT_DIR          default: ohrs-trajectories/recent-<UTC timestamp>
#   INCLUDE_ALL      default: 1; copy whole job dir tar entries without dereferencing symlinks.
#                    Set 0 to copy only trajectory.jsonl, analysis.json, settings.json.
set -euo pipefail

SSH_HOST="${SSH_HOST:-gojo}"
YUTAS="${YUTAS:-yuta-okkotsu yuta-maki-zenin}"
REMOTE_JOBS_DIR="${REMOTE_JOBS_DIR:-/tmp/capelle-jobs}"
LIMIT="${LIMIT:-25}"
INCLUDE_ALL="${INCLUDE_ALL:-1}"

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "${script_dir}/.." && pwd)"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_DIR="${OUT_DIR:-${repo_root}/ohrs-trajectories/recent-${stamp}}"

if [[ ! "$LIMIT" =~ ^[0-9]+$ ]] || [[ "$LIMIT" -lt 1 ]]; then
  echo "ERROR: LIMIT must be a positive integer, got: $LIMIT" >&2
  exit 2
fi

mkdir -p "$OUT_DIR"
manifest="$OUT_DIR/manifest.tsv"
summary="$OUT_DIR/README.md"
: > "$manifest"

log() { printf 'pull-recent-trajectories: %s\n' "$*" >&2; }

printf '# Pulled Capelle OHRS trajectories\n\n' > "$summary"
printf -- '- pulled_at_utc: `%s`\n' "$stamp" >> "$summary"
printf -- '- ssh_host: `%s`\n' "$SSH_HOST" >> "$summary"
printf -- '- yutas: `%s`\n' "$YUTAS" >> "$summary"
printf -- '- remote_jobs_dir: `%s`\n' "$REMOTE_JOBS_DIR" >> "$summary"
printf -- '- limit_per_yuta: `%s`\n' "$LIMIT" >> "$summary"
printf -- '- include_all: `%s`\n\n' "$INCLUDE_ALL" >> "$summary"
printf 'host\tjob_id\ttrajectory_mtime_utc\ttrajectory_bytes\tanalysis_bytes\tsettings_bytes\n' >> "$manifest"

remote_list_script='set -euo pipefail
jobs_dir="$1"
limit="$2"
if [[ ! -d "$jobs_dir" ]]; then
  exit 0
fi
find "$jobs_dir" -mindepth 2 -maxdepth 2 -type f -name trajectory.jsonl -printf "%T@\t%h\n" \
  | sort -nr \
  | head -n "$limit" \
  | while IFS=$'"'"'\t'"'"' read -r _ dir; do basename "$dir"; done
'

remote_manifest_script='set -euo pipefail
jobs_dir="$1"
shift
for job in "$@"; do
  dir="$jobs_dir/$job"
  traj="$dir/trajectory.jsonl"
  analysis="$dir/analysis.json"
  settings="$dir/settings.json"
  [[ -f "$traj" ]] || continue
  mtime="$(date -u -r "$traj" +%Y-%m-%dT%H:%M:%SZ)"
  tbytes="$(stat -c %s "$traj" 2>/dev/null || echo 0)"
  abytes="$(stat -c %s "$analysis" 2>/dev/null || echo 0)"
  sbytes="$(stat -c %s "$settings" 2>/dev/null || echo 0)"
  printf "%s\t%s\t%s\t%s\t%s\n" "$job" "$mtime" "$tbytes" "$abytes" "$sbytes"
done
'

for yuta in $YUTAS; do
  log "listing latest $LIMIT jobs on $yuta ..."
  mapfile -t jobs < <(
    ssh "$SSH_HOST" "lxc exec '$yuta' -- bash -s -- '$REMOTE_JOBS_DIR' '$LIMIT'" <<< "$remote_list_script"
  )

  host_dir="$OUT_DIR/$yuta"
  mkdir -p "$host_dir"

  if [[ "${#jobs[@]}" -eq 0 ]]; then
    log "no trajectory jobs found on $yuta"
    printf '\n## %s\n\nNo trajectory jobs found.\n' "$yuta" >> "$summary"
    continue
  fi

  log "pulling ${#jobs[@]} job dirs from $yuta -> $host_dir"
  if [[ "$INCLUDE_ALL" == "1" ]]; then
    # Copy the selected job directories as tar entries. tar stores symlinks as
    # symlinks by default, so Capelle_enquetes links do not explode into PDFs.
    quoted_jobs=""
    for job in "${jobs[@]}"; do
      quoted_jobs+=" $(printf '%q' "$job")"
    done
    ssh "$SSH_HOST" "lxc exec '$yuta' -- bash -lc 'cd $(printf '%q' "$REMOTE_JOBS_DIR") && tar -czf - --warning=no-file-changed $quoted_jobs'" \
      | tar -xzf - -C "$host_dir"
  else
    for job in "${jobs[@]}"; do
      mkdir -p "$host_dir/$job"
      for file in trajectory.jsonl analysis.json settings.json; do
        ssh "$SSH_HOST" "lxc exec '$yuta' -- bash -lc 'test -f $(printf '%q' "$REMOTE_JOBS_DIR/$job/$file") && tar -C $(printf '%q' "$REMOTE_JOBS_DIR/$job") -czf - $(printf '%q' "$file") || true'" \
          | tar -xzf - -C "$host_dir/$job" 2>/dev/null || true
      done
    done
  fi

  mapfile -t rows < <(
    ssh "$SSH_HOST" "lxc exec '$yuta' -- bash -s -- '$REMOTE_JOBS_DIR' ${jobs[*]}" <<< "$remote_manifest_script"
  )
  printf '\n## %s\n\n' "$yuta" >> "$summary"
  for row in "${rows[@]}"; do
    [[ -n "$row" ]] || continue
    printf '%s\t%s\n' "$yuta" "$row" >> "$manifest"
    IFS=$'\t' read -r job mtime tbytes abytes sbytes <<< "$row"
    printf -- '- `%s`: trajectory `%s` bytes, analysis `%s` bytes, mtime `%s`\n' "$job" "$tbytes" "$abytes" "$mtime" >> "$summary"
  done

done

log "wrote $manifest"
log "wrote $summary"
printf '%s\n' "$OUT_DIR"
