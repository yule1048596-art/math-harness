#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
PROJECT_ROOT="${SCRIPT_DIR:h}"
VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' \
    "$PROJECT_ROOT/macos/MathHarnessApp/Resources/Info.plist")"
ARCHITECTURE="$(uname -m)"
ARCHIVE_PATH="${1:-$PROJECT_ROOT/dist/Math-Harness-$VERSION-macOS-$ARCHITECTURE.zip}"
STAGING_ROOT="$(mktemp -d /tmp/math-harness-e2e.XXXXXX)"
BACKEND_PID=""

if [[ "$STAGING_ROOT" != /tmp/math-harness-e2e.* ]]; then
    print -u2 "Refusing to use an unexpected test path: $STAGING_ROOT"
    exit 1
fi

cleanup() {
    if [[ -n "$BACKEND_PID" ]]; then
        kill "$BACKEND_PID" 2>/dev/null || true
        wait "$BACKEND_PID" 2>/dev/null || true
    fi
    find "$STAGING_ROOT" -depth -delete 2>/dev/null || true
}
trap cleanup EXIT

ditto -x -k "$ARCHIVE_PATH" "$STAGING_ROOT"
APP_BUNDLE="$STAGING_ROOT/Math Harness.app"
BACKEND="$APP_BUNDLE/Contents/Resources/backend/math-harness-server"
READY_FILE="$STAGING_ROOT/ready.json"
DATA_ROOT="$STAGING_ROOT/data"
LOG_FILE="$STAGING_ROOT/backend.log"

codesign --verify --deep --strict --verbose=2 "$APP_BUNDLE"

env \
    MATH_HARNESS_LOCAL_TOKEN="integration-token" \
    MATH_HARNESS_SOLVER="mimo" \
    MATH_HARNESS_METHOD_EXTRACTOR="rules" \
    MIMO_API_KEY="bundle-test-placeholder" \
    MATH_HARNESS_MIMO_BASE_URL="http://127.0.0.1:9/v1" \
    MATH_HARNESS_MIMO_TIMEOUT_SECONDS="0.5" \
    MATH_HARNESS_VERIFICATION_REPAIR="false" \
    "$BACKEND" \
    --data-dir "$DATA_ROOT" \
    --ready-file "$READY_FILE" \
    --port 0 \
    >"$LOG_FILE" 2>&1 &
BACKEND_PID=$!

for _ in {1..200}; do
    [[ -f "$READY_FILE" ]] && break
    if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
        sed -n '1,200p' "$LOG_FILE"
        exit 1
    fi
    sleep 0.1
done
[[ -f "$READY_FILE" ]]

BASE_URL="$(jq -r .base_url "$READY_FILE")"
UNAUTHORIZED="$(curl -sS -o /dev/null -w '%{http_code}' "$BASE_URL/health")"
[[ "$UNAUTHORIZED" == "401" ]]

HEALTH="$(curl -fsS \
    -H 'Authorization: Bearer integration-token' \
    "$BASE_URL/health")"
WORKSPACE="$(curl -fsS \
    -H 'Authorization: Bearer integration-token' \
    -H 'Content-Type: application/json' \
    --data-binary '{"name":"macOS E2E","description":"packaged backend"}' \
    "$BASE_URL/workspaces")"
WORKSPACE_ID="$(jq -r .id <<<"$WORKSPACE")"
SOLUTION="$(curl -fsS \
    -H 'Authorization: Bearer integration-token' \
    -H 'Content-Type: application/json' \
    --data-binary '{"problem":"求 sqrt(x^2+x)-x 的渐进展开","tags":["radical"],"math_target":{"expression":"sqrt(x**2+x)-x","variable":"x","point":"oo","mode":"asymptotic_expansion","remainder_power":2}}' \
    "$BASE_URL/workspaces/$WORKSPACE_ID/solve")"
[[ "$(jq -r .status <<<"$SOLUTION")" == "verified" ]]
[[ "$(jq -r .generation.fallback_used <<<"$SOLUTION")" == "true" ]]

print "health=$(jq -r '.status + " v" + .version' <<<"$HEALTH")"
print "workspace=$(jq -r .name <<<"$WORKSPACE")"
print "solve_status=$(jq -r .status <<<"$SOLUTION")"
print "answer=$(jq -r .candidate.answer_expression <<<"$SOLUTION")"
print "model_fallback=$(jq -r .generation.fallback_used <<<"$SOLUTION")"
