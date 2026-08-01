import Foundation
import MathHarnessCore

func require(_ condition: @autoclosure () -> Bool, _ message: String) {
  guard condition() else {
    fatalError("Core check failed: \(message)")
  }
}

let readyData = Data(
  #"{"base_url":"http://127.0.0.1:54321","pid":42,"port":54321,"version":"0.6.0"}"#.utf8
)
let ready = try JSONDecoder().decode(BackendReady.self, from: readyData)
require(ready.baseURL == "http://127.0.0.1:54321", "ready base URL")
require(ready.port == 54321, "ready port")
require(ready.version == "0.6.0", "ready version")

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

print("MathHarnessCore checks passed")
