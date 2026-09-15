#!/usr/bin/env bash
# =============================================================================
# deploy/scripts/test-push-sh.sh — sanity tests for deploy/push.sh
#
# No pytest, no fixtures. Shell-level assertions on:
#   1. --help exits 0 and prints usage
#   2. invalid --component errors non-zero
#   3. missing target errors non-zero
#   4. --dry-run against a fake yuta prints a plan and exits 0 without side-effects
#
# Run: bash deploy/scripts/test-push-sh.sh
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PUSH="$SCRIPT_DIR/../push.sh"

PASS=0
FAIL=0

run_case() {
    local desc="$1"; shift
    local want_exit="$1"; shift
    # Remaining args are the command + flags.
    local actual_exit=0
    local output
    output="$("$@" 2>&1)" || actual_exit=$?
    if [[ "$actual_exit" == "$want_exit" ]]; then
        printf "  PASS  %s (exit=%d)\n" "$desc" "$actual_exit"
        PASS=$((PASS+1))
    else
        printf "  FAIL  %s (want exit=%s, got %s)\n" "$desc" "$want_exit" "$actual_exit"
        printf "  ---- output ----\n%s\n  ----------------\n" "$output"
        FAIL=$((FAIL+1))
    fi
}

assert_stdout_contains() {
    local desc="$1"; shift
    local needle="$1"; shift
    # Remaining args are the command.
    local output
    output="$("$@" 2>&1 || true)"
    if grep -qF -- "$needle" <<<"$output"; then
        printf "  PASS  %s contains '%s'\n" "$desc" "$needle"
        PASS=$((PASS+1))
    else
        printf "  FAIL  %s did NOT contain '%s'\n" "$desc" "$needle"
        printf "  ---- output ----\n%s\n  ----------------\n" "$output"
        FAIL=$((FAIL+1))
    fi
}

echo "== test-push-sh =="

# 1. --help
run_case "--help exits 0" 0 bash "$PUSH" --help

# 2. no args
run_case "no target exits 2" 2 bash "$PUSH"

# 3. invalid component
run_case "invalid component exits 2" 2 bash "$PUSH" yuta-okkotsu --component bogus

# 4. invalid target shape
run_case "non-yuta target exits 2" 2 bash "$PUSH" not-a-yuta

# 5. dry-run
assert_stdout_contains "dry-run mentions yuta name" "yuta-okkotsu" \
    bash "$PUSH" yuta-okkotsu --component frontend --no-restart --dry-run
assert_stdout_contains "dry-run mentions component" "component=frontend" \
    bash "$PUSH" yuta-okkotsu --component frontend --no-restart --dry-run

echo "---"
echo "pass=$PASS fail=$FAIL"
[[ "$FAIL" -eq 0 ]]
