#!/usr/bin/env bash
# =============================================================================
# scripts/verify-v1.1.sh
# -----------------------------------------------------------------------------
# Purpose:
#   End-to-end verification that v1.1 scale-out is actually working across
#   the yuta fleet. Runs from a workstation against live `data-compass.org`
#   AFTER v1.1 code is deployed on both yutas AND both are in the nginx
#   upstream. Reports PASS/FAIL per check, then a bottom-line GO / NO-GO.
#
# Checks:
#   a. LB routes to both yutas (X-Capelle-Hostname response header)
#   b. Cross-yuta Postgres (user registered via LB appears in aoi-todo)
#   c. Cross-yuta RabbitMQ queue (jobs are drained by both yutas)
#   d. Cross-yuta WS fanout (message_complete arrives on the OTHER yuta's WS)
#   e. Chroma reachable from both yutas
#   f. Token-accounting flows (admin stats/tokens is non-empty after a run)
#
# Required env:
#   CAPELLE_ADMIN_EMAIL       ops admin email (e.g. v.sokolovs@outlook.com)
#   CAPELLE_ADMIN_PASSWORD    ops admin password
#
# Optional env:
#   PUBLIC_ORIGIN             default: https://data-compass.org
#   HOST                      default: gojo (ssh target for lxc exec)
#   YUTAS                     default: "yuta-okkotsu yuta-maki-zenin"
#   CHROMA_CONTAINER          default: yuji-itadori
#   POSTGRES_CONTAINER        default: aoi-todo
#
# Exit codes:
#   0 = all PASS (GO)
#   1 = one or more FAIL (NO-GO)
# =============================================================================

set -euo pipefail

TAG="[verify-v1.1]"
PUBLIC_ORIGIN="${PUBLIC_ORIGIN:-https://data-compass.org}"
HOST="${HOST:-gojo}"
YUTAS="${YUTAS:-yuta-okkotsu yuta-maki-zenin}"
CHROMA_CONTAINER="${CHROMA_CONTAINER:-yuji-itadori}"
POSTGRES_CONTAINER="${POSTGRES_CONTAINER:-aoi-todo}"

: "${CAPELLE_ADMIN_EMAIL:?$TAG CAPELLE_ADMIN_EMAIL is required}"
: "${CAPELLE_ADMIN_PASSWORD:?$TAG CAPELLE_ADMIN_PASSWORD is required}"

ts() { date -u +'%Y-%m-%dT%H:%M:%SZ'; }
log() { echo "$(ts) $TAG $*"; }

# Scratch dir for cookies, ws logs, etc. Auto-cleaned on exit.
TMP="$(mktemp -d -t verify-v1.1.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT

RESULTS=()   # "CHECK_ID|PASS|note" or "...|FAIL|note"

record() {
    # record <id> <PASS|FAIL> <short-note>
    local id="$1" verdict="$2" note="$3"
    RESULTS+=("${id}|${verdict}|${note}")
    log "[${id}] ${verdict} — ${note}"
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
curl_with_hostname() {
    # Performs GET on the given path, prints `STATUS<TAB>HOSTHEADER`.
    local path="$1"
    curl -sS -o /dev/null \
        -w '%{http_code}\t%header{x-capelle-hostname}\n' \
        "${PUBLIC_ORIGIN}${path}"
}

admin_login() {
    # Logs in as the ops admin, stores cookies in $1. Returns 0 on success.
    local jar="$1"
    curl -sS -c "$jar" -b "$jar" -X POST \
        -H 'Content-Type: application/json' \
        -d "{\"email\":\"${CAPELLE_ADMIN_EMAIL}\",\"password\":\"${CAPELLE_ADMIN_PASSWORD}\"}" \
        "${PUBLIC_ORIGIN}/api/auth/login" >/dev/null
}

# ---------------------------------------------------------------------------
# (a) LB routes to both yutas
# ---------------------------------------------------------------------------
check_a_lb_routing() {
    log "check (a): LB routes to both yutas"
    local -A seen=()
    local i status host
    for i in $(seq 1 10); do
        local out
        out="$(curl_with_hostname /api/health || echo "000\t")"
        status="$(printf '%s' "$out" | cut -f1)"
        host="$(printf '%s' "$out" | cut -f2)"
        if [[ "$status" != "200" ]]; then
            record "a" "FAIL" "hit #$i got status=$status"
            return
        fi
        if [[ -n "$host" ]]; then
            seen["$host"]=$(( ${seen["$host"]:-0} + 1 ))
        fi
    done
    local distinct=${#seen[@]}
    if [[ "$distinct" -lt 2 ]]; then
        record "a" "FAIL" "only ${distinct} distinct X-Capelle-Hostname values across 10 hits: ${!seen[*]:-<none>}"
        return
    fi
    local summary=""
    for h in "${!seen[@]}"; do summary+="${h}=${seen[$h]} "; done
    record "a" "PASS" "2+ yutas served: ${summary%% }"
}

# ---------------------------------------------------------------------------
# (b) Cross-yuta Postgres: register via LB, row lands in aoi-todo
# ---------------------------------------------------------------------------
check_b_postgres() {
    log "check (b): cross-yuta Postgres"
    local probe_email="verify-v11-$(date +%s)-$$@example.invalid"
    local probe_password="verify-v1.1-pw-$(date +%s)"
    local http_code
    http_code="$(curl -sS -o /dev/null -w '%{http_code}' -X POST \
        -H 'Content-Type: application/json' \
        -d "{\"email\":\"${probe_email}\",\"password\":\"${probe_password}\"}" \
        "${PUBLIC_ORIGIN}/api/auth/register")"
    if [[ "$http_code" != "201" && "$http_code" != "200" ]]; then
        record "b" "FAIL" "register returned status=${http_code}"
        return
    fi
    # Query aoi-todo for the row
    local found
    found="$(ssh "$HOST" "lxc exec ${POSTGRES_CONTAINER} -- psql -U capelle -d capelle -tAc \"SELECT email FROM users WHERE email='${probe_email}' LIMIT 1\"" 2>/dev/null | tr -d '[:space:]' || true)"
    if [[ "$found" == "$probe_email" ]]; then
        record "b" "PASS" "user ${probe_email} found in ${POSTGRES_CONTAINER}"
    else
        record "b" "FAIL" "user ${probe_email} NOT found in ${POSTGRES_CONTAINER} (got='${found}')"
    fi
}

# ---------------------------------------------------------------------------
# (c) Cross-yuta RabbitMQ queue: submit 4 msgs, both yutas log job_completed
# ---------------------------------------------------------------------------
submit_n_messages() {
    # Submit <n> analysis messages as the ops admin. Returns 0 if all accepted.
    local n="$1" jar="$TMP/admin.cookie"
    admin_login "$jar"
    # Create a session
    local session_json
    session_json="$(curl -sS -b "$jar" -c "$jar" -X POST \
        -H 'Content-Type: application/json' \
        -d '{"title":"verify-v1.1"}' \
        "${PUBLIC_ORIGIN}/api/chat/sessions")"
    local session_id
    session_id="$(printf '%s' "$session_json" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("id",""))')"
    if [[ -z "$session_id" ]]; then
        echo "$TAG FATAL: could not create session (resp=${session_json})" >&2
        return 1
    fi
    echo "$session_id" > "$TMP/session_id"
    local i
    for i in $(seq 1 "$n"); do
        curl -sS -b "$jar" -c "$jar" -X POST \
            -H 'Content-Type: application/json' \
            -d "{\"content\":\"verify-v1.1 probe message #${i}\"}" \
            "${PUBLIC_ORIGIN}/api/chat/sessions/${session_id}/messages" \
            -o /dev/null -w "submit-${i}=%{http_code}\n"
        sleep 1
    done
}

count_job_completed() {
    # Count job_completed log lines in a yuta's service log within the last 5 min.
    local yuta="$1"
    ssh "$HOST" "lxc exec ${yuta} -- journalctl -u capelle-platform --since '5 min ago' --no-pager" 2>/dev/null \
        | grep -c 'job_completed' || true
}

check_c_rabbitmq_fanout() {
    log "check (c): cross-yuta RabbitMQ queue (4 messages, attempt 1)"

    # First attempt
    local -A baseline=()
    for y in $YUTAS; do baseline["$y"]="$(count_job_completed "$y")"; done

    if ! submit_n_messages 4; then
        record "c" "FAIL" "could not submit 4 probe messages"
        return
    fi

    # wait for jobs to run (these are short analysis messages; give them 60s)
    sleep 60

    local -A after=()
    local -A deltas=()
    local sum=0 max=0 max_host=""
    local distinct_nonzero=0
    for y in $YUTAS; do
        after["$y"]="$(count_job_completed "$y")"
        local d=$(( after["$y"] - baseline["$y"] ))
        deltas["$y"]="$d"
        sum=$(( sum + d ))
        if [[ "$d" -gt "$max" ]]; then max="$d"; max_host="$y"; fi
        if [[ "$d" -gt 0 ]]; then distinct_nonzero=$(( distinct_nonzero + 1 )); fi
    done

    local summary=""
    for y in $YUTAS; do summary+="${y}=+${deltas[$y]} "; done

    if [[ "$distinct_nonzero" -ge 2 ]]; then
        record "c" "PASS" "both yutas drained jobs: ${summary%% }"
        return
    fi

    # Re-run once before failing if one yuta got everything
    log "check (c): one yuta monopolised (${summary%% }); re-running once..."
    for y in $YUTAS; do baseline["$y"]="${after[$y]}"; done
    if ! submit_n_messages 4; then
        record "c" "FAIL" "second submission failed after monopoly"
        return
    fi
    sleep 60
    distinct_nonzero=0
    summary=""
    for y in $YUTAS; do
        after["$y"]="$(count_job_completed "$y")"
        local d=$(( after["$y"] - baseline["$y"] ))
        deltas["$y"]="$d"
        summary+="${y}=+${d} "
        if [[ "$d" -gt 0 ]]; then distinct_nonzero=$(( distinct_nonzero + 1 )); fi
    done
    if [[ "$distinct_nonzero" -ge 2 ]]; then
        record "c" "PASS" "2nd run: both yutas drained jobs: ${summary%% }"
    else
        record "c" "FAIL" "2nd run still monopolised: ${summary%% }"
    fi
}

# ---------------------------------------------------------------------------
# (d) Cross-yuta WS fanout: WS on one yuta, job executes on OTHER
# ---------------------------------------------------------------------------
check_d_ws_fanout() {
    log "check (d): cross-yuta WS fanout"
    local jar="$TMP/admin.cookie"
    admin_login "$jar"

    # Which yuta does the LB pick for the WS connection?
    # We get that from a plain /api/health hit right now.
    local ws_yuta
    ws_yuta="$(curl -sS -b "$jar" -o /dev/null \
        -w '%header{x-capelle-hostname}' "${PUBLIC_ORIGIN}/api/health")"
    if [[ -z "$ws_yuta" ]]; then
        record "d" "FAIL" "could not determine WS-side yuta (empty X-Capelle-Hostname)"
        return
    fi
    log "    WS-side yuta will likely be: ${ws_yuta}"

    # Snapshot job_completed on every yuta BEFORE we submit
    local -A pre_jc=()
    for y in $YUTAS; do pre_jc["$y"]="$(count_job_completed "$y")"; done

    # Create a session + kick off WS listener in background via a tiny python script.
    local session_json session_id
    session_json="$(curl -sS -b "$jar" -c "$jar" -X POST \
        -H 'Content-Type: application/json' -d '{"title":"verify-v1.1-ws"}' \
        "${PUBLIC_ORIGIN}/api/chat/sessions")"
    session_id="$(printf '%s' "$session_json" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("id",""))')"
    if [[ -z "$session_id" ]]; then
        record "d" "FAIL" "could not create session for WS test"
        return
    fi

    # Determine WS URL and cookie string
    local ws_origin="${PUBLIC_ORIGIN/https:/wss:}"
    ws_origin="${ws_origin/http:/ws:}"
    local cookie_header
    cookie_header="$(awk '/^[^#]/ && NF>=7 {print $6 "=" $7}' "$jar" | paste -sd'; ' -)"

    # Start WS listener via Python (uses `websockets` lib) — writes any
    # `message_complete` it sees to $TMP/ws.out. We write the listener as
    # a standalone file so the bash quoting stays sane.
    cat >"$TMP/ws_listen.py" <<'PYEOF'
import asyncio
import json
import sys

try:
    import websockets
except ImportError:
    print("websockets-missing", flush=True)
    sys.exit(0)

origin, cookie, session_id = sys.argv[1], sys.argv[2], sys.argv[3]
url = f"{origin}/api/ws"


async def listen() -> None:
    headers = [("Cookie", cookie)]
    try:
        async with websockets.connect(url, extra_headers=headers, open_timeout=10) as ws:
            print("ws-open", flush=True)
            end = asyncio.get_event_loop().time() + 180
            while asyncio.get_event_loop().time() < end:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=5)
                except asyncio.TimeoutError:
                    continue
                try:
                    obj = json.loads(raw)
                except Exception:
                    continue
                t = obj.get("type") or obj.get("event") or ""
                tl = str(t).lower()
                if "message_complete" in tl or "complete" in tl:
                    sid = obj.get("session_id") or obj.get("sessionId") or ""
                    if not sid or sid == session_id:
                        print(f"message_complete|{t}|{sid}", flush=True)
                        return
    except Exception as e:
        print(f"ws-error|{e}", flush=True)


asyncio.run(listen())
PYEOF
    python3 "$TMP/ws_listen.py" "$ws_origin" "$cookie_header" "$session_id" \
        >"$TMP/ws.out" 2>"$TMP/ws.err" &
    WS_PID=$!

    sleep 2

    # Force multiple submissions — we want at least one to land on a yuta
    # that is NOT ws_yuta. Six submissions = very high probability of
    # landing on the other yuta at least once with RR.
    local i
    for i in $(seq 1 6); do
        curl -sS -b "$jar" -c "$jar" -X POST \
            -H 'Content-Type: application/json' \
            -d "{\"content\":\"verify-v1.1 ws probe #${i}\"}" \
            "${PUBLIC_ORIGIN}/api/chat/sessions/${session_id}/messages" \
            -o /dev/null -w "ws-submit-${i}=%{http_code}\n"
        sleep 1
    done

    # Wait for either the WS listener to observe message_complete, or timeout.
    local waited=0
    while [[ $waited -lt 200 ]]; do
        if grep -q 'message_complete' "$TMP/ws.out" 2>/dev/null; then break; fi
        sleep 5
        waited=$(( waited + 5 ))
    done

    # Kill the listener either way.
    kill "$WS_PID" 2>/dev/null || true
    wait "$WS_PID" 2>/dev/null || true

    # Check whether a yuta OTHER than ws_yuta drained at least one job.
    local other_drained=0
    local summary=""
    for y in $YUTAS; do
        local d=$(( $(count_job_completed "$y") - pre_jc["$y"] ))
        summary+="${y}=+${d} "
        if [[ "$y" != "$ws_yuta" && "$d" -gt 0 ]]; then
            other_drained=1
        fi
    done

    if grep -q 'websockets-missing' "$TMP/ws.out" 2>/dev/null; then
        record "d" "FAIL" "python3 websockets lib missing — pip install websockets"
        return
    fi

    if grep -q 'message_complete' "$TMP/ws.out" 2>/dev/null && [[ "$other_drained" -eq 1 ]]; then
        record "d" "PASS" "message_complete arrived on WS (yuta ${ws_yuta}) while other yuta drained (${summary%% })"
    elif grep -q 'message_complete' "$TMP/ws.out" 2>/dev/null; then
        record "d" "FAIL" "got message_complete on WS but no job drained on the OTHER yuta (${summary%% }) — fanout inconclusive"
    else
        record "d" "FAIL" "no message_complete seen on WS within timeout (submits=${summary%% })"
    fi
}

# ---------------------------------------------------------------------------
# (e) Chroma reachable from both yutas
# ---------------------------------------------------------------------------
check_e_chroma() {
    log "check (e): Chroma reachable from both yutas"
    local all_ok=1
    local summary=""
    for y in $YUTAS; do
        local code
        code="$(ssh "$HOST" "lxc exec ${y} -- curl -sS -o /dev/null -w '%{http_code}' http://${CHROMA_CONTAINER}:8000/api/v2/heartbeat" 2>/dev/null || echo "000")"
        summary+="${y}=${code} "
        if [[ "$code" != "200" ]]; then all_ok=0; fi
    done
    if [[ "$all_ok" -eq 1 ]]; then
        record "e" "PASS" "chroma heartbeat 200 from every yuta: ${summary%% }"
    else
        record "e" "FAIL" "not all yutas reached chroma: ${summary%% }"
    fi
}

# ---------------------------------------------------------------------------
# (f) Token accounting still flows
# ---------------------------------------------------------------------------
check_f_tokens() {
    log "check (f): admin token-accounting endpoint returns recent data"
    local jar="$TMP/admin.cookie"
    admin_login "$jar"
    local body
    body="$(curl -sS -b "$jar" -c "$jar" "${PUBLIC_ORIGIN}/api/admin/stats/tokens?days=1&limit=5")"
    # Accept any JSON with at least one numeric key or a non-empty list.
    local non_empty
    non_empty="$(printf '%s' "$body" | python3 -c '
import sys, json
try:
    d = json.loads(sys.stdin.read() or "null")
except Exception:
    print("parse_error"); sys.exit(0)
if d is None: print("null"); sys.exit(0)
if isinstance(d, list):
    print("nonempty" if len(d) > 0 else "empty"); sys.exit(0)
if isinstance(d, dict):
    # Common shape: {"top_users": [...], "totals": {...}}
    any_list = any(isinstance(v, list) and len(v) > 0 for v in d.values())
    any_num  = any(isinstance(v, (int, float)) and v > 0 for v in d.values())
    print("nonempty" if (any_list or any_num) else "empty"); sys.exit(0)
print("unknown")
')"
    if [[ "$non_empty" == "nonempty" ]]; then
        record "f" "PASS" "stats/tokens returned non-empty data"
    else
        record "f" "FAIL" "stats/tokens response was ${non_empty}: ${body:0:200}"
    fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
log "=== v1.1 verification against ${PUBLIC_ORIGIN} ==="
log "yutas under test: ${YUTAS}"

check_a_lb_routing
check_b_postgres
check_c_rabbitmq_fanout
check_d_ws_fanout
check_e_chroma
check_f_tokens

echo ""
log "--- results ---"
overall=0
for row in "${RESULTS[@]}"; do
    id="$(printf '%s' "$row" | cut -d'|' -f1)"
    verdict="$(printf '%s' "$row" | cut -d'|' -f2)"
    note="$(printf '%s' "$row" | cut -d'|' -f3-)"
    printf '[%s] %-4s  %s\n' "$id" "$verdict" "$note"
    if [[ "$verdict" != "PASS" ]]; then overall=1; fi
done
echo ""
if [[ "$overall" -eq 0 ]]; then
    log "GO — all v1.1 cross-yuta checks passed."
    exit 0
else
    log "NO-GO — one or more v1.1 checks failed. Investigate via deploy/VERIFICATION.md."
    exit 1
fi
