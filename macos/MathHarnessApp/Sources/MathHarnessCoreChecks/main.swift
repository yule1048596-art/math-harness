import Foundation
import MathHarnessCore

func require(_ condition: @autoclosure () -> Bool, _ message: String) {
  guard condition() else {
    fatalError("Core check failed: \(message)")
  }
}

let readyData = Data(
  #"{"base_url":"http://127.0.0.1:54321","pid":42,"port":54321,"version":"0.7.0"}"#.utf8
)
let ready = try JSONDecoder().decode(BackendReady.self, from: readyData)
require(ready.baseURL == "http://127.0.0.1:54321", "ready base URL")
require(ready.port == 54321, "ready port")
require(ready.version == "0.7.0", "ready version")

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

let workspaceData = Data(
  #"{"id":"ws-1","name":"渐进估计","description":"测试","created_at":"2026-08-01T01:02:03.123456Z"}"#.utf8
)
let workspace = try JSONDecoder().decode(Workspace.self, from: workspaceData)
require(workspace.id == "ws-1", "workspace id")
require(workspace.name == "渐进估计", "workspace name")
require(workspace.createdAt.hasPrefix("2026-08-01"), "workspace timestamp")

let exampleData = Data(
  #"{"id":"ex-1","workspace_id":"ws-1","problem":"求展开","solution":"先有理化。","tags":["radical"],"method_hint":null,"reviewed":false,"problem_kind":"asymptotic","math_payload":null,"verification":{"status":"verified","summary":"通过","checks":[],"computed":{},"error":null},"extraction":{"provider":"rules","model":null,"status":"success","extracted_method_keys":["rationalization"]},"method_drafts":[{"key":"rationalization","name":"有理化","goal":"处理抵消","applicable_when":[],"procedure":["乘共轭"],"failure_modes":[],"tags":[]}],"status":"pending_review","origin":"conversation","source_attempt_id":"attempt-1","reviewed_at":null,"reviewer_note":"","created_at":"2026-08-01T01:03:00Z"}"#
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

let reviewData = try JSONEncoder().encode(
  ExampleReviewRequest(decision: .approve, reviewerNote: "checked")
)
let reviewObject = try JSONSerialization.jsonObject(with: reviewData) as? [String: Any]
require(reviewObject?["decision"] as? String == "approve", "review decision")
require(reviewObject?["reviewer_note"] as? String == "checked", "review note")

print("MathHarnessCore checks passed")
