import Foundation
import MathHarnessCore

func require(_ condition: @autoclosure () -> Bool, _ message: String) {
  guard condition() else {
    fatalError("Core check failed: \(message)")
  }
}

let readyData = Data(
  #"{"base_url":"http://127.0.0.1:54321","pid":42,"port":54321,"version":"0.9.0"}"#.utf8
)
let ready = try JSONDecoder().decode(BackendReady.self, from: readyData)
require(ready.baseURL == "http://127.0.0.1:54321", "ready base URL")
require(ready.port == 54321, "ready port")
require(ready.version == "0.9.0", "ready version")

let solveRequest = SolveRequest(
  problem: "求渐进展开",
  tags: ["radical"],
  topK: 3,
  mathTarget: SolveMathTargetRequest(
    expression: "sqrt(x**2+x)-x",
    remainderPower: 2
  ),
  maxOutputTokens: 4_000
)
let requestData = try JSONEncoder().encode(solveRequest)
guard
  let object = try JSONSerialization.jsonObject(with: requestData) as? [String: Any],
  let target = object["math_target"] as? [String: Any]
else {
  fatalError("Core check failed: encoded solve request shape")
}
require(object["top_k"] as? Int == 3, "top_k encoding")
require(object["max_output_tokens"] as? Int == 4_000, "max_output_tokens encoding")
require(target["remainder_power"] as? Int == 2, "remainder_power encoding")
require(target["mode"] as? String == "asymptotic_expansion", "mode encoding")
require(target["assumptions"] as? [String: [String]] == [:], "assumptions encoding")

let targetDraftData = Data(
  #"{"target":{"expression":"sin(x)/x","variable":"x","parameters":[],"assumptions":{},"point":"0","direction":"two_sided","mode":"limit","remainder_power":null},"status":"success","provider":"rules","model":null,"confidence":0.8,"summary":"已整理","warnings":[],"fallback_used":false,"error":null,"requires_confirmation":true}"#
    .utf8
)
let targetDraft = try JSONDecoder().decode(MathTargetDraftResult.self, from: targetDraftData)
require(targetDraft.target?.mode == .limit, "target draft mode")
require(targetDraft.requiresConfirmation, "target draft confirmation boundary")

let workspaceData = Data(
  #"{"id":"ws-1","name":"渐进估计","description":"测试","created_at":"2026-08-01T01:02:03.123456Z"}"#.utf8
)
let workspace = try JSONDecoder().decode(Workspace.self, from: workspaceData)
require(workspace.id == "ws-1", "workspace id")
require(workspace.name == "渐进估计", "workspace name")
require(workspace.createdAt.hasPrefix("2026-08-01"), "workspace timestamp")

let importRequestData = try JSONEncoder().encode(
  BulkExampleImportRequest(
    content: #"{"problem":"题目","solution":"解答"}"#,
    reviewPolicy: .pending,
    extractorPolicy: .rules,
    sourceName: "corpus.jsonl"
  )
)
let importRequestObject =
  try JSONSerialization.jsonObject(with: importRequestData) as? [String: Any]
require(importRequestObject?["file_format"] as? String == "auto", "import format encoding")
require(importRequestObject?["review_policy"] as? String == "pending", "review policy encoding")
require(
  importRequestObject?["extractor_policy"] as? String == "rules",
  "extractor policy encoding"
)
require(importRequestObject?["commit"] as? Bool == false, "preflight encoding")

let importResultData = Data(
  #"{"source_name":"corpus.jsonl","detected_format":"jsonl","commit_requested":false,"committed":false,"can_commit":true,"total_count":1,"ready_count":1,"duplicate_count":0,"invalid_count":0,"imported_count":0,"items":[{"index":1,"status":"ready","problem_preview":"题目","fingerprint":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","verification":{"status":"verified","summary":"通过","checks":[],"computed":{},"error":null},"method_keys":[],"example_id":null,"errors":[]}]}"#
    .utf8
)
let importResult = try JSONDecoder().decode(BulkExampleImportResult.self, from: importResultData)
require(importResult.canCommit, "import preflight gate")
require(importResult.items.first?.verification?.status == "verified", "import verification")

let restoreResultData = Data(
  #"{"workspace":{"id":"ws-2","name":"渐进估计（恢复）","description":"测试","created_at":"2026-08-01T02:00:00Z"},"source_workspace_id":"ws-1","source_app_version":"0.9.0","archive_format_version":1,"restored_record_counts":{"examples":2}}"#
    .utf8
)
let restoreResult = try JSONDecoder().decode(
  WorkspaceRestoreResult.self,
  from: restoreResultData
)
require(restoreResult.workspace.id == "ws-2", "restored workspace id")
require(restoreResult.sourceWorkspaceID == "ws-1", "restore source workspace")
require(restoreResult.restoredRecordCounts["examples"] == 2, "restore counts")

let exampleData = Data(
  #"{"id":"ex-1","workspace_id":"ws-1","problem":"求展开","solution":"先有理化。","tags":["radical"],"method_hint":null,"reviewed":false,"problem_kind":"asymptotic","math_payload":null,"verification":{"status":"verified","summary":"通过","checks":[],"computed":{},"error":null},"extraction":{"provider":"rules","model":null,"status":"success","extracted_method_keys":["rationalization"]},"method_drafts":[{"key":"rationalization","name":"有理化","goal":"处理抵消","applicable_when":[],"procedure":["乘共轭"],"failure_modes":[],"tags":[]}],"status":"pending_review","origin":"conversation","source_attempt_id":"attempt-1","reviewed_at":null,"reviewer_note":"","revision":1,"created_at":"2026-08-01T01:03:00Z","updated_at":"2026-08-01T01:03:00Z"}"#
    .utf8
)
let example = try JSONDecoder().decode(ProblemExample.self, from: exampleData)
require(example.origin == "conversation", "example origin")
require(example.sourceAttemptID == "attempt-1", "source attempt")
require(
  example.extraction?.extractedMethodKeys == ["rationalization"],
  "extracted method keys"
)
require(example.methodDrafts.first?.procedure == ["乘共轭"], "method draft preview")
require(example.revision == 1, "example revision")

let updateData = try JSONEncoder().encode(
  ExampleDraftUpdateRequest(
    expectedRevision: example.revision,
    problem: example.problem,
    solution: example.solution,
    tags: example.tags,
    methodHint: nil,
    mathPayload: nil
  )
)
let updateObject = try JSONSerialization.jsonObject(with: updateData) as? [String: Any]
require(updateObject?["expected_revision"] as? Int == 1, "draft revision lock encoding")

let reviewData = try JSONEncoder().encode(
  ExampleReviewRequest(decision: .approve, expectedRevision: 1, reviewerNote: "checked")
)
let reviewObject = try JSONSerialization.jsonObject(with: reviewData) as? [String: Any]
require(reviewObject?["decision"] as? String == "approve", "review decision")
require(reviewObject?["expected_revision"] as? Int == 1, "review revision lock")
require(reviewObject?["reviewer_note"] as? String == "checked", "review note")

print("MathHarnessCore checks passed")
