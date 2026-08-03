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
    MATH_HARNESS_CONVERSATION_PROVIDER="offline" \
    MATH_HARNESS_METHOD_EXTRACTOR="rules" \
    MATH_HARNESS_MEMORY_EXTRACTOR="disabled" \
    MATH_HARNESS_TARGET_DRAFTER="mimo" \
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
CONVERSATION="$(curl -fsS \
    -H 'Authorization: Bearer integration-token' \
    -H 'Content-Type: application/json' \
    --data-binary '{}' \
    "$BASE_URL/workspaces/$WORKSPACE_ID/conversations")"
CONVERSATION_ID="$(jq -r .id <<<"$CONVERSATION")"
CHAT_TURN="$(curl -fsS \
    -H 'Authorization: Bearer integration-token' \
    -H 'Content-Type: application/json' \
    --data-binary '{"message":"你好，请记住我们在测试持久会话。","turn_id":"bundle-chat-1"}' \
    "$BASE_URL/workspaces/$WORKSPACE_ID/conversations/$CONVERSATION_ID/turns")"
[[ "$(jq -r .assistant_message.kind <<<"$CHAT_TURN")" == "chat" ]]
[[ "$(jq -r .assistant_message.provider <<<"$CHAT_TURN")" == "offline" ]]
[[ "$(jq -r .conversation.message_count <<<"$CHAT_TURN")" == "2" ]]
MEMORY="$(curl -fsS \
    -H 'Authorization: Bearer integration-token' \
    -H 'Content-Type: application/json' \
    --data-binary '{"content":"用户偏好先讲直觉再展开细节","kind":"explanation_preference","tags":["中文偏好"],"pinned":true}' \
    "$BASE_URL/workspaces/$WORKSPACE_ID/memories")"
MEMORY_ID="$(jq -r .id <<<"$MEMORY")"
[[ "$(jq -r .source <<<"$MEMORY")" == "manual" ]]
[[ "$(jq -r .pinned <<<"$MEMORY")" == "true" ]]
MEMORY_SEARCH="$(curl -fsS \
    -G \
    -H 'Authorization: Bearer integration-token' \
    --data-urlencode 'q=直觉' \
    "$BASE_URL/workspaces/$WORKSPACE_ID/memories")"
[[ "$(jq -r 'length' <<<"$MEMORY_SEARCH")" == "1" ]]
[[ "$(jq -r '.[0].id' <<<"$MEMORY_SEARCH")" == "$MEMORY_ID" ]]
MEMORY_HEALTH="$(curl -fsS \
    -H 'Authorization: Bearer integration-token' \
    "$BASE_URL/workspaces/$WORKSPACE_ID/memory-health")"
[[ "$(jq -r .extractor_available <<<"$MEMORY_HEALTH")" == "false" ]]
IMPORT_LINE='{"problem":"展开 (x+1)^2","solution":"按二项式展开得到 x^2+2x+1。","tags":["代数"],"reviewed":true,"math_payload":{"expression":"(x+1)**2","expected":"x**2+2*x+1","variable":"x","point":"0","mode":"exact_equivalence"}}'
IMPORT_PREVIEW_BODY="$(jq -nc \
    --arg content "$IMPORT_LINE" \
    '{content:$content,file_format:"jsonl",source_name:"packaged.jsonl",commit:false}')"
IMPORT_PREVIEW="$(curl -fsS \
    -H 'Authorization: Bearer integration-token' \
    -H 'Content-Type: application/json' \
    --data-binary "$IMPORT_PREVIEW_BODY" \
    "$BASE_URL/workspaces/$WORKSPACE_ID/example-imports")"
[[ "$(jq -r .can_commit <<<"$IMPORT_PREVIEW")" == "true" ]]
[[ "$(jq -r .ready_count <<<"$IMPORT_PREVIEW")" == "1" ]]
IMPORT_COMMIT_BODY="$(jq '.commit = true' <<<"$IMPORT_PREVIEW_BODY")"
IMPORT_COMMIT="$(curl -fsS \
    -H 'Authorization: Bearer integration-token' \
    -H 'Content-Type: application/json' \
    --data-binary "$IMPORT_COMMIT_BODY" \
    "$BASE_URL/workspaces/$WORKSPACE_ID/example-imports")"
[[ "$(jq -r .committed <<<"$IMPORT_COMMIT")" == "true" ]]
[[ "$(jq -r .imported_count <<<"$IMPORT_COMMIT")" == "1" ]]
[[ "$(jq -r '.items[0].status' <<<"$IMPORT_COMMIT")" == "imported" ]]
BACKUP_ARCHIVE="$STAGING_ROOT/workspace.mathharness"
curl -fsS \
    -H 'Authorization: Bearer integration-token' \
    "$BASE_URL/workspaces/$WORKSPACE_ID/backup" \
    -o "$BACKUP_ARCHIVE"
RESTORE="$(curl -fsS \
    -H 'Authorization: Bearer integration-token' \
    -H 'Content-Type: application/vnd.math-harness.workspace+zip' \
    --data-binary "@$BACKUP_ARCHIVE" \
    "$BASE_URL/workspace-restores")"
RESTORED_WORKSPACE_ID="$(jq -r .workspace.id <<<"$RESTORE")"
[[ "$RESTORED_WORKSPACE_ID" != "$WORKSPACE_ID" ]]
[[ "$(jq -r .source_workspace_id <<<"$RESTORE")" == "$WORKSPACE_ID" ]]
[[ "$(jq -r .restored_record_counts.examples <<<"$RESTORE")" == "1" ]]
[[ "$(jq -r .restored_record_counts.memory_items <<<"$RESTORE")" == "1" ]]
RESTORED_CONVERSATIONS="$(curl -fsS \
    -H 'Authorization: Bearer integration-token' \
    "$BASE_URL/workspaces/$RESTORED_WORKSPACE_ID/conversations")"
[[ "$(jq -r 'length' <<<"$RESTORED_CONVERSATIONS")" == "1" ]]
RESTORED_MESSAGES="$(curl -fsS \
    -H 'Authorization: Bearer integration-token' \
    "$BASE_URL/workspaces/$RESTORED_WORKSPACE_ID/conversations/$CONVERSATION_ID/messages")"
[[ "$(jq -r 'length' <<<"$RESTORED_MESSAGES")" == "2" ]]
RESTORED_MEMORIES="$(curl -fsS \
    -H 'Authorization: Bearer integration-token' \
    "$BASE_URL/workspaces/$RESTORED_WORKSPACE_ID/memories")"
[[ "$(jq -r 'length' <<<"$RESTORED_MEMORIES")" == "1" ]]
[[ "$(jq -r '.[0].content' <<<"$RESTORED_MEMORIES")" == "用户偏好先讲直觉再展开细节" ]]
TARGET_DRAFT="$(curl -fsS \
    -H 'Authorization: Bearer integration-token' \
    -H 'Content-Type: application/json' \
    --data-binary '{"problem":"求 x→∞ 时 sqrt(x^2+x)-x 的渐进展开到 O(x^-2)"}' \
    "$BASE_URL/workspaces/$WORKSPACE_ID/math-target-drafts")"
[[ "$(jq -r .requires_confirmation <<<"$TARGET_DRAFT")" == "true" ]]
[[ "$(jq -r .target.expression <<<"$TARGET_DRAFT")" == "sqrt(x**2+x)-x" ]]
[[ "$(jq -r .status <<<"$TARGET_DRAFT")" == "fallback" ]]
SOLUTION_TURN="$(curl -fsS \
    -H 'Authorization: Bearer integration-token' \
    -H 'Content-Type: application/json' \
    --data-binary '{"message":"使用共轭有理化和泰勒展开求 sqrt(x^2+x)-x 的渐进展开","turn_id":"bundle-solve-1","tags":["radical"],"math_target":{"expression":"sqrt(x**2+x)-x","variable":"x","point":"oo","mode":"asymptotic_expansion","remainder_power":2}}' \
    "$BASE_URL/workspaces/$WORKSPACE_ID/conversations/$CONVERSATION_ID/turns")"
SOLUTION="$(jq -c .attempt <<<"$SOLUTION_TURN")"
[[ "$(jq -r .status <<<"$SOLUTION")" == "verified" ]]
[[ "$(jq -r .generation.fallback_used <<<"$SOLUTION")" == "true" ]]
[[ "$(jq -r .assistant_message.kind <<<"$SOLUTION_TURN")" == "solve" ]]
[[ "$(jq -r .assistant_message.verification_status <<<"$SOLUTION_TURN")" == "verified" ]]
[[ "$(jq -r .knowledge_draft.status <<<"$SOLUTION_TURN")" == "pending_review" ]]
ATTEMPT_ID="$(jq -r .id <<<"$SOLUTION")"
EXAMPLES="$(curl -fsS \
    -H 'Authorization: Bearer integration-token' \
    "$BASE_URL/workspaces/$WORKSPACE_ID/examples")"
CAPTURED="$(jq -c --arg attempt "$ATTEMPT_ID" \
    '.[] | select(.source_attempt_id == $attempt)' <<<"$EXAMPLES")"
[[ "$(jq -r .origin <<<"$CAPTURED")" == "conversation" ]]
[[ "$(jq -r .status <<<"$CAPTURED")" == "pending_review" ]]
[[ "$(jq -r .verification.status <<<"$CAPTURED")" == "verified" ]]
[[ "$(jq -r '.method_drafts | length > 0' <<<"$CAPTURED")" == "true" ]]
EXAMPLE_ID="$(jq -r .id <<<"$CAPTURED")"
EDIT_PAYLOAD="$(jq -nc \
    --arg problem "$(jq -r .problem <<<"$CAPTURED")" \
    --arg solution "$(jq -r .solution <<<"$CAPTURED")\n人工确认余项阶数。" \
    --arg hint "$(jq -r '.method_hint // ""' <<<"$CAPTURED")" \
    '{
        expected_revision: 1,
        problem: $problem,
        solution: $solution,
        tags: ["radical"],
        method_hint: (if $hint == "" then null else $hint end),
        math_payload: {
            expression: "sqrt(x**2+x)-x",
            expected: "1/2 - 1/(8*x)",
            variable: "x",
            point: "oo",
            mode: "asymptotic_expansion",
            remainder_power: 2
        }
    }')"
EDITED="$(curl -fsS \
    -X PATCH \
    -H 'Authorization: Bearer integration-token' \
    -H 'Content-Type: application/json' \
    --data-binary "$EDIT_PAYLOAD" \
    "$BASE_URL/workspaces/$WORKSPACE_ID/examples/$EXAMPLE_ID")"
[[ "$(jq -r .example.revision <<<"$EDITED")" == "2" ]]
[[ "$(jq -r .example.verification.status <<<"$EDITED")" == "verified" ]]
VERSIONS="$(curl -fsS \
    -H 'Authorization: Bearer integration-token' \
    "$BASE_URL/workspaces/$WORKSPACE_ID/examples/$EXAMPLE_ID/versions")"
[[ "$(jq -r 'length' <<<"$VERSIONS")" == "1" ]]
REVIEW="$(curl -fsS \
    -H 'Authorization: Bearer integration-token' \
    -H 'Content-Type: application/json' \
    --data-binary '{"decision":"approve","expected_revision":2,"reviewer_note":"packaged E2E"}' \
    "$BASE_URL/workspaces/$WORKSPACE_ID/examples/$EXAMPLE_ID/review")"
[[ "$(jq -r .example.status <<<"$REVIEW")" == "promoted" ]]
[[ "$(jq -r '.learned_methods | length > 0' <<<"$REVIEW")" == "true" ]]
[[ "$(jq -r '[.learned_methods[].status] | all(. == "promoted")' <<<"$REVIEW")" == "true" ]]

print "health=$(jq -r '.status + " v" + .version' <<<"$HEALTH")"
print "workspace=$(jq -r .name <<<"$WORKSPACE")"
print "conversation=$(jq -r '.conversation.title + " → " + (.conversation.message_count | tostring) + " messages"' <<<"$SOLUTION_TURN")"
print "memory=$(jq -r '.content + " → pinned=" + (.pinned | tostring)' <<<"$MEMORY")"
print "bulk_import=$(jq -r '.ready_count | tostring' <<<"$IMPORT_PREVIEW") ready → $(jq -r '.imported_count | tostring' <<<"$IMPORT_COMMIT") imported"
print "backup_restore=$(jq -r '.workspace.name' <<<"$RESTORE")"
print "solve_status=$(jq -r .status <<<"$SOLUTION")"
print "answer=$(jq -r .candidate.answer_expression <<<"$SOLUTION")"
print "model_fallback=$(jq -r .generation.fallback_used <<<"$SOLUTION")"
print "target_draft=$(jq -r '.provider + " → confirm=" + (.requires_confirmation | tostring)' <<<"$TARGET_DRAFT")"
print "knowledge_capture=$(jq -r '.origin + " → " + .status' <<<"$CAPTURED")"
print "knowledge_revision=$(jq -r .example.revision <<<"$EDITED")"
print "knowledge_review=$(jq -r .example.status <<<"$REVIEW")"
