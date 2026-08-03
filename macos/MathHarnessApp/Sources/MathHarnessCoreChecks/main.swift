import Foundation
import MathHarnessCore

func require(_ condition: @autoclosure () -> Bool, _ message: String) {
  guard condition() else {
    fatalError("Core check failed: \(message)")
  }
}

let readyData = Data(
  #"{"base_url":"http://127.0.0.1:54321","pid":42,"port":54321,"version":"0.14.1"}"#.utf8
)
let ready = try JSONDecoder().decode(BackendReady.self, from: readyData)
require(ready.baseURL == "http://127.0.0.1:54321", "ready base URL")
require(ready.port == 54321, "ready port")
require(ready.version == "0.14.1", "ready version")

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

let conversationData = Data(
  #"{"id":"chat-1","workspace_id":"ws-1","title":"根式讨论","summary":"此前讨论了有理化。","summary_through_ordinal":2,"message_count":4,"status":"active","provider":{"profile_id":"cheap","model":null},"created_at":"2026-08-01T01:00:00Z","updated_at":"2026-08-01T01:05:00Z"}"#
    .utf8
)
let conversation = try JSONDecoder().decode(Conversation.self, from: conversationData)
require(conversation.workspaceID == "ws-1", "conversation workspace")
require(conversation.summaryThroughOrdinal == 2, "conversation summary cursor")
require(conversation.messageCount == 4, "conversation message count")

let messageData = Data(
  #"{"id":"msg-1","workspace_id":"ws-1","conversation_id":"chat-1","turn_id":"turn-1","ordinal":2,"role":"assistant","kind":"solve","content":"答案为 1/2。","provider":"xiaomi_mimo","model":"mimo-v2.5-pro","attempt_id":"attempt-1","knowledge_draft_id":"ex-1","verification_status":"verified","method_keys":["rationalization"],"created_at":"2026-08-01T01:05:00Z"}"#
    .utf8
)
let conversationMessage = try JSONDecoder().decode(
  ConversationMessage.self,
  from: messageData
)
require(conversationMessage.role == "assistant", "conversation message role")
require(conversationMessage.verificationStatus == "verified", "conversation verification")
require(conversationMessage.methodKeys == ["rationalization"], "conversation method keys")

let memoryData = Data(
  #"{"id":"memory-1","workspace_id":"ws-1","kind":"explanation_preference","content":"先讲直觉，再给严格证明","tags":["严谨"],"status":"active","pinned":true,"source":"automatic","conversation_id":"chat-1","source_message_id":"msg-user-1","evidence":"我喜欢先讲直觉","supersedes_id":null,"created_at":"2026-08-01T01:05:00Z","updated_at":"2026-08-01T01:05:00Z"}"#
    .utf8
)
let memory = try JSONDecoder().decode(MemoryItem.self, from: memoryData)
require(memory.kind == .explanationPreference, "memory kind")
require(memory.status == .active, "memory status")
require(memory.pinned, "memory pin")
require(memory.conversationID == "chat-1", "memory source conversation")

let memoryRequestData = try JSONEncoder().encode(
  MemoryCreateRequest(
    content: "专题是渐进估计",
    kind: .topicContext,
    tags: ["渐进"],
    pinned: true
  )
)
let memoryRequestObject =
  try JSONSerialization.jsonObject(with: memoryRequestData) as? [String: Any]
require(memoryRequestObject?["kind"] as? String == "topic_context", "memory kind encoding")
require(memoryRequestObject?["pinned"] as? Bool == true, "memory pin encoding")

let memoryJobData = Data(
  #"{"id":"job-1","workspace_id":"ws-1","conversation_id":"chat-1","from_ordinal":0,"through_ordinal":2,"source_revision":"abc123","status":"succeeded","attempts":1,"provider":"xiaomi_mimo","model":"mimo-v2.5-pro","extracted_count":1,"input_tokens":42,"output_tokens":18,"duration_ms":320,"error":null,"created_at":"2026-08-01T01:05:00Z","started_at":"2026-08-01T01:05:01Z","completed_at":"2026-08-01T01:05:02Z"}"#
    .utf8
)
let memoryJob = try JSONDecoder().decode(MemoryExtractionJob.self, from: memoryJobData)
require(memoryJob.fromOrdinal == 0, "memory job start cursor")
require(memoryJob.extractedCount == 1, "memory job extraction count")
require(memoryJob.inputTokens == 42, "memory job usage")

let memoryHealthData = Data(
  #"{"workspace_id":"ws-1","automatic_extraction_enabled":true,"extractor_available":true,"queued_count":0,"running_count":0,"failed_count":0,"last_success_at":"2026-08-01T01:05:02Z","last_error_at":null,"last_error":null}"#
    .utf8
)
let memoryHealth = try JSONDecoder().decode(MemoryHealth.self, from: memoryHealthData)
require(memoryHealth.extractorAvailable, "memory extractor health")
require(memoryHealth.failedCount == 0, "memory failure health")

let turnRequestData = try JSONEncoder().encode(
  ConversationTurnRequest(
    message: "继续求下一项",
    turnID: "turn-2",
    mathTarget: SolveMathTargetRequest(
      expression: "sqrt(x**2+x)-x",
      remainderPower: 3
    )
  )
)
let turnRequestObject =
  try JSONSerialization.jsonObject(with: turnRequestData) as? [String: Any]
require(turnRequestObject?["turn_id"] as? String == "turn-2", "conversation turn id")
require(turnRequestObject?["math_target"] != nil, "conversation target encoding")

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

// --- Provider 档案与角色绑定 ---

// 密钥环境变量名的生成规则必须与 Python 侧 provider_config.key_env_name 逐字一致，
// 否则后端读不到密钥。Python 侧有同名断言。
require(
  ProviderEnvironment.keyEnvName(for: "deepseek-1")
    == "MATH_HARNESS_PROVIDER_KEY__DEEPSEEK_1",
  "key env name for dashed id"
)
require(
  ProviderEnvironment.keyEnvName(for: "a.b-c") == "MATH_HARNESS_PROVIDER_KEY__A_B_C",
  "key env name sanitization"
)
require(
  ProviderEnvironment.keychainAccount(for: "abc") == "provider-key-abc",
  "keychain account naming"
)

// 档案 JSON 键名必须与 Python 侧一致，且绝不含密钥。
let profile = ProviderPreset.deepseek.makeProfile(id: "deepseek-1")
let profileData = try JSONEncoder().encode(profile)
let profileObject = try JSONSerialization.jsonObject(with: profileData) as? [String: Any]
require(
  profileObject?["base_url"] as? String == "https://api.deepseek.com/v1", "profile base_url key")
require(profileObject?["default_model"] as? String == "deepseek-chat", "profile default_model key")
require(
  profileObject?["structured_output_mode"] as? String == "json_schema",
  "profile structured mode key"
)
require(profileObject?["timeout_seconds"] != nil, "profile timeout key")
require(profileObject?["max_output_tokens"] != nil, "profile max tokens key")
require(profileObject?["api_key"] == nil, "profile must never carry a key")

let decodedProfile = try JSONDecoder().decode(ProviderProfile.self, from: profileData)
require(decodedProfile == profile, "profile round trip")

// MiMo 的 Responses API 只保证 JSON Object，预设必须替用户选对。
require(ProviderPreset.mimo.structuredOutputMode == .jsonObject, "mimo uses json object")
require(ProviderPreset.openai.structuredOutputMode == .jsonSchema, "openai uses json schema")
require(!ProviderPreset.ollama.requiresAPIKey, "local ollama needs no key")

// 角色绑定 JSON。
let bindingData = try JSONEncoder().encode(
  RoleBinding(profile: "deepseek-1", model: "deepseek-reasoner", reasoningEffort: "high")
)
let bindingObject = try JSONSerialization.jsonObject(with: bindingData) as? [String: Any]
require(bindingObject?["profile"] as? String == "deepseek-1", "binding profile key")
require(bindingObject?["reasoning_effort"] as? String == "high", "binding effort key")

// 简单档必须展开成全部五个角色。
let simple = ProviderSettings(
  profiles: [profile],
  useSimpleMode: true,
  simpleProfileID: "deepseek-1"
)
let simpleRoles = simple.resolvedRoles()
require(simpleRoles.count == ModelRole.allCases.count, "simple mode covers every role")
require(
  ModelRole.allCases.allSatisfy { simpleRoles[$0.rawValue]?.profile == "deepseek-1" },
  "simple mode binds every role to one profile"
)

// 没有可用档案时全部退回离线，App 仍能离线工作。
let empty = ProviderSettings(profiles: [], useSimpleMode: true, simpleProfileID: nil)
require(
  empty.resolvedRoles().values.allSatisfy(\.isOffline),
  "empty settings fall back to offline"
)

// 指向已删除档案的绑定必须退回离线，而不是把无效 ID 发给后端。
let dangling = ProviderSettings(
  profiles: [profile],
  roles: [
    ModelRole.solver.rawValue: RoleBinding(profile: "deleted-profile"),
    ModelRole.conversation.rawValue: RoleBinding(profile: "deepseek-1"),
  ],
  useSimpleMode: false
)
let danglingRoles = dangling.resolvedRoles()
require(danglingRoles[ModelRole.solver.rawValue]?.isOffline == true, "dangling binding falls back")
require(
  danglingRoles[ModelRole.conversation.rawValue]?.profile == "deepseek-1",
  "valid binding survives"
)

// 对话的模型选择与归档状态
require(conversation.status == .active, "conversation defaults to active")
require(conversation.provider?.profileID == "cheap", "conversation provider decodes")

let providerData = try JSONEncoder().encode(
  ConversationProvider(profileID: "strong", model: "deepseek-reasoner")
)
let providerObject = try JSONSerialization.jsonObject(with: providerData) as? [String: Any]
require(providerObject?["profile_id"] as? String == "strong", "provider profile_id key")
// 对话记录里绝不能出现密钥——备份文件不加密。
require(providerObject?["api_key"] == nil, "conversation provider carries no key")

let renameData = try JSONEncoder().encode(ConversationRenameRequest(title: "新标题"))
let renameObject = try JSONSerialization.jsonObject(with: renameData) as? [String: Any]
require(renameObject?["title"] as? String == "新标题", "rename encoding")

print("MathHarnessCore checks passed")
