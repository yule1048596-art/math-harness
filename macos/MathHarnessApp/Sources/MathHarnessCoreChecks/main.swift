import Foundation
import MathHarnessCore

func require(_ condition: @autoclosure () -> Bool, _ message: String) {
  guard condition() else {
    fatalError("Core check failed: \(message)")
  }
}

let readyData = Data(
  #"{"base_url":"http://127.0.0.1:54321","pid":42,"port":54321,"version":"0.20.0"}"#.utf8
)
let ready = try JSONDecoder().decode(BackendReady.self, from: readyData)
require(ready.baseURL == "http://127.0.0.1:54321", "ready base URL")
require(ready.port == 54321, "ready port")
require(ready.version == "0.20.0", "ready version")

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

let migrationKeys = LegacyProviderSettingsMigrationKeys(
  providerSettings: "providerSettings",
  completionMarker: "didMigrateLegacyMiMo",
  legacyProvider: "solverProvider",
  legacyBaseURL: "mimoBaseURL",
  legacyModel: "mimoModel"
)
let migrationSuite = "MathHarnessCoreChecks.\(UUID().uuidString)"
let migrationDefaults = UserDefaults(suiteName: migrationSuite)!
migrationDefaults.removePersistentDomain(forName: migrationSuite)
migrationDefaults.set("mimo", forKey: migrationKeys.legacyProvider)
migrationDefaults.set("https://legacy.example/v1", forKey: migrationKeys.legacyBaseURL)
migrationDefaults.set("legacy-model", forKey: migrationKeys.legacyModel)
enum SimulatedKeychainError: Error { case denied }
let failedMigration = LegacyProviderSettingsMigrator.migrate(
  defaults: migrationDefaults,
  keys: migrationKeys,
  preset: .mimo,
  legacyProviderID: "mimo",
  readLegacyKey: { "legacy-key-placeholder" },
  migrateLegacyKey: { _ in throw SimulatedKeychainError.denied }
)
require(!failedMigration, "failed key migration reports false")
require(
  !migrationDefaults.bool(forKey: migrationKeys.completionMarker),
  "failed key migration remains retryable"
)
require(
  migrationDefaults.data(forKey: migrationKeys.providerSettings) == nil,
  "failed key migration does not commit settings"
)
var migratedProfileID: String?
let retriedMigration = LegacyProviderSettingsMigrator.migrate(
  defaults: migrationDefaults,
  keys: migrationKeys,
  preset: .mimo,
  legacyProviderID: "mimo",
  readLegacyKey: { "legacy-key-placeholder" },
  migrateLegacyKey: {
    migratedProfileID = $0
    return true
  }
)
require(retriedMigration, "key migration retries after failure")
require(
  migrationDefaults.bool(forKey: migrationKeys.completionMarker),
  "successful key migration commits marker"
)
let migratedSettingsData = migrationDefaults.data(forKey: migrationKeys.providerSettings)!
let migratedSettings = try JSONDecoder().decode(
  ProviderSettings.self, from: migratedSettingsData
)
require(migratedSettings.profiles.first?.id == migratedProfileID, "migrated profile key target")
require(
  migratedSettings.profiles.first?.baseURL == "https://legacy.example/v1",
  "migrated legacy base URL"
)
require(
  migratedSettings.profiles.first?.defaultModel == "legacy-model",
  "migrated legacy model"
)
migrationDefaults.removePersistentDomain(forName: migrationSuite)

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
// v0.15 之前的 helper 不返回这几个字段。App 和 helper 各自升级，新 App 配旧 helper
// 必须还能读出消息——解码整条崩掉的话，用户连回答都看不到。
require(conversationMessage.counterexample.isEmpty, "legacy message decodes without counterexample")
require(conversationMessage.checkedClaims.isEmpty, "legacy message decodes without claims")
require(conversationMessage.conclusionConfidence == nil, "legacy message has no conclusion axis")
require(conversationMessage.generationError == nil, "legacy message has no stream error")

let interruptedMessageData = Data(
  #"{"id":"msg-interrupted","workspace_id":"ws-1","conversation_id":"chat-1","turn_id":"turn-interrupted","ordinal":3,"role":"assistant","kind":"chat","content":"先整理已知条件，","provider":"mimo","model":"mimo-7b","generation_error":"RuntimeError: connection reset","attempt_id":null,"knowledge_draft_id":null,"verification_status":null,"conclusion_confidence":null,"process_confidence":null,"counterexample":{},"checked_claims":[],"method_keys":[],"created_at":"2026-08-07T00:30:00Z"}"#
    .utf8
)
let interruptedMessage = try JSONDecoder().decode(
  ConversationMessage.self,
  from: interruptedMessageData
)
require(
  interruptedMessage.generationError == "RuntimeError: connection reset",
  "interrupted message preserves generation error"
)
require(interruptedMessage.conclusionConfidence == nil, "interrupted message is untrusted")
require(!interruptedMessage.wasStoppedByUser, "a dropped connection is not a user stop")

let stoppedMessageData = Data(
  #"{"id":"msg-stopped","workspace_id":"ws-1","conversation_id":"chat-1","turn_id":"turn-stopped","ordinal":5,"role":"assistant","kind":"chat","content":"先求导：","provider":"mimo","model":"mimo-7b","generation_error":"StoppedByUser: 用户中止了这次生成","attempt_id":null,"knowledge_draft_id":null,"verification_status":null,"conclusion_confidence":null,"process_confidence":null,"counterexample":{},"checked_claims":[],"method_keys":[],"created_at":"2026-08-13T00:30:00Z"}"#
    .utf8
)
let stoppedMessage = try JSONDecoder().decode(
  ConversationMessage.self,
  from: stoppedMessageData
)
// 用户自己按的停止和断线在数据上一样——都不检查、不入库。差别只在怎么说：自己按的
// 停止不该弹一个「连接中断」的错误提示。
require(stoppedMessage.wasStoppedByUser, "a user stop is recognised")
require(stoppedMessage.conclusionConfidence == nil, "a stopped message is untrusted")

let gradedMessageData = Data(
  #"{"id":"msg-2","workspace_id":"ws-1","conversation_id":"chat-1","turn_id":"turn-2","ordinal":4,"role":"assistant","kind":"chat","content":"(a+b)^2 - (a-b)^2 = 4ab","provider":"mimo","model":"mimo-7b","attempt_id":null,"knowledge_draft_id":null,"verification_status":null,"conclusion_confidence":"verified","process_confidence":"step_failed","counterexample":{"a":"7","b":"-4"},"checked_claims":["(a+b)**2 = a**2 + 2*a*b + b**2"],"method_keys":[],"created_at":"2026-08-07T01:00:00Z"}"#
    .utf8
)
let gradedMessage = try JSONDecoder().decode(ConversationMessage.self, from: gradedMessageData)
// 结论对、推导错必须能分别读出来。合成一个标签就会把它显示成「对」，而这恰恰是
// 最该被看见的一种。
require(
  ConclusionConfidence(rawValue: gradedMessage.conclusionConfidence ?? "") == .verified,
  "graded message conclusion axis"
)
require(
  ProcessConfidence(rawValue: gradedMessage.processConfidence ?? "") == .stepFailed,
  "graded message process axis"
)
require(gradedMessage.counterexample["a"] == "7", "graded message counterexample")
require(gradedMessage.checkedClaims.count == 1, "graded message checked claims")
// 后端以后还会加档位，未知值必须安全落地而不是崩掉。
require(ConclusionConfidence(rawValue: "brand_new_tier") == nil, "unknown tier stays nil")

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

// --- Markdown 与数学记号的冲突 ---------------------------------------
//
// 这一组是本项目里 Markdown 渲染唯一的风险点：把公式改掉。`x**2 + y**2` 交给
// Markdown 会变成 `x<strong>2 + y</strong>2`，星号凭空消失，用户看不出发生了什么。

let escape = MathMarkdown.escapingMathOperators

// 运算符要被转义掉。
require(escape("x**2 + y**2") == #"x\*\*2 + y\*\*2"#, "power operator is escaped")
require(escape("a*b*c") == #"a\*b\*c"#, "multiplication is escaped")
require(escape("x_1 + x_2") == #"x\_1 + x\_2"#, "subscript is escaped")
require(escape("(a+b)**2") == #"(a+b)\*\*2"#, "power after a bracket is escaped")

// 中文正文里的强调要活下来。
require(escape("**重点**内容") == "**重点**内容", "bold around CJK survives")
require(escape("设 **a** 为常数") == "设 **a** 为常数", "spaced bold survives")
require(escape("- **步骤 1**：求导") == "- **步骤 1**：求导", "bold before punctuation survives")

// 反引号里的东西已经是代码，再插反斜杠会真的显示出来。
require(escape("`x**2`") == "`x**2`", "code spans are left alone")
require(escape(#"\*"#) == #"\*"#, "an existing escape is not doubled")

let sample = """
  ### 求导步骤

  1. 先用幂法则
  - 再检查定义域

  ```
  diff(x**2, x)
  ```

  结论是 2*x。
  """
let sampleBlocks = MathMarkdown.blocks(from: sample)
require(
  sampleBlocks.first == .heading(level: 3, text: "求导步骤"),
  "heading block"
)
require(
  sampleBlocks.contains(.listItem(marker: "1.", text: "先用幂法则")),
  "ordered list block"
)
require(
  sampleBlocks.contains(.listItem(marker: "•", text: "再检查定义域")),
  "bullet list block"
)
require(sampleBlocks.contains(.code("diff(x**2, x)")), "fenced code block")
require(sampleBlocks.last == .paragraph("结论是 2*x。"), "trailing paragraph")

// 以数字开头的正常句子不是列表。
require(
  MathMarkdown.blocks(from: "2026 年的题目") == [.paragraph("2026 年的题目")],
  "a sentence starting with digits is not a list"
)

// --- LaTeX 排版 ------------------------------------------------------
//
// 一条不让步的规则：**任何一处解析不了，整条公式退回原文显示**。不做部分渲染——
// 半懂半猜地渲出来，丢掉的那个符号不会有任何提示，而这是数学软件。

typealias MathNode = MathTypesetting.Node

require(MathTypesetting.parse("x") == .text("x"), "a bare symbol")
require(
  MathTypesetting.parse(#"\frac{1}{x}"#)
    == .fraction(numerator: .text("1"), denominator: .text("x")),
  "fraction"
)
require(
  MathTypesetting.parse("x^2")
    == .script(base: .text("x"), superscript: .text("2"), subscriptNode: nil),
  "single-character superscript"
)
require(
  MathTypesetting.parse("a_{ij}")
    == .script(base: .text("a"), superscript: nil, subscriptNode: .text("ij")),
  "braced subscript"
)
require(
  MathTypesetting.parse(#"\sqrt[3]{x}"#)
    == .radical(radicand: .text("x"), index: .text("3")),
  "cube root"
)
require(MathTypesetting.parse(#"\alpha"#) == .text("α"), "greek letter")
require(MathTypesetting.parse(#"\le"#) == .text("≤"), "relation symbol")

// 大算符的上下标是**上下限**，不是角标——要摆在符号上下，不是右边。
if case .row(let items)? = MathTypesetting.parse(#"\sum_{k=1}^{n} k"#),
  case .bigOperator(let symbol, let lower, let upper) = items.first
{
  require(symbol == "∑", "sum symbol")
  require(lower != nil && upper != nil, "sum carries both limits")
  require(upper == .text("n"), "sum upper limit")
} else {
  fatalError("Core check failed: sum with limits")
}

// 矩阵：线性代数是这个软件的主战场之一。
if case .matrix(let open, _, let rows)? =
  MathTypesetting.parse(#"\begin{pmatrix}1 & 2 \\ 3 & 4\end{pmatrix}"#)
{
  require(open == "(", "pmatrix delimiter")
  require(rows.count == 2 && rows[0].count == 2, "pmatrix shape")
} else {
  fatalError("Core check failed: pmatrix")
}

// 认不出来的一律整条作废。
require(MathTypesetting.parse(#"\foobar{x}"#) == nil, "an unknown command fails")
require(MathTypesetting.parse(#"\frac{1}"#) == nil, "a missing argument fails")
require(MathTypesetting.parse("{x") == nil, "an unbalanced brace fails")
require(MathTypesetting.parse("x_1_2") == nil, "a repeated subscript fails")
require(MathTypesetting.parse("x @ y") == nil, "an unknown character fails")

// 切分：文字与公式分开，落单的 `$` 不算公式起点。
require(
  MathTypesetting.segments(in: "所以 $x^2$ 是它的导数。").count == 3,
  "text, formula, text"
)
if case .rawFormula(let source) = MathTypesetting.segments(in: #"公式 $\foobar$ 结束"#)[1] {
  // 解析失败要**原样带着定界符**，让用户看见他写的是什么。
  require(source == #"$\foobar$"#, "a failed formula keeps its source")
} else {
  fatalError("Core check failed: failed formula falls back to source")
}
require(
  MathTypesetting.segments(in: "这件事花了 $5 元") == [.text("这件事花了 $5 元")],
  "an unpaired dollar sign is not a formula"
)
if case .formula(_, let display, _) = MathTypesetting.segments(in: "$$x+1$$")[0] {
  require(display, "double dollars are display math")
} else {
  fatalError("Core check failed: display math")
}

// 设置页那张预览卡里的原句。它渲不出来的话，用户打开设置第一眼看到的就是一段
// 生 LaTeX。
for source in [
  #"\frac{d}{dx}x^{n} = n x^{n-1}"#,
  #"\sum_{k=1}^{n} k = \frac{n(n+1)}{2}"#,
  #"\lim_{x \to 0} \frac{\sin x}{x} = 1"#,
  #"\int_{0}^{1} x^2 dx = \frac{1}{3}"#,
  #"\sqrt{a^2 + b^2} \le |a| + |b|"#,
  #"\alpha + \beta \equiv \gamma \pmod{n}"#,
] {
  require(MathTypesetting.parse(source) != nil, "typesets: \(source)")
}

// 结构签名的版本健康度。版本对不上的卡片在结构检索里是「关着的」，这件事必须能被
// 界面读出来——不然用户只会觉得「最近检索变差了」，而没有任何报错。
let healthData = Data(#"{"stale":3,"feature_version":1}"#.utf8)
let health = try JSONDecoder().decode(SignatureHealth.self, from: healthData)
require(health.stale == 3, "signature health stale count")
require(health.featureVersion == 1, "signature health feature version")

let rebuiltData = Data(#"{"rebuilt":3}"#.utf8)
let rebuilt = try JSONDecoder().decode(SignatureRebuildResult.self, from: rebuiltData)
require(rebuilt.rebuilt == 3, "signature rebuild result")

// --- 知识库导出 ------------------------------------------------------
//
// 两条不变式撑着整个 v0.20：
//   1. 渲染逐字符稳定——不稳定的话，正文保护会被自己的输出击穿，用户手写的东西第一次
//      自动导出就没了；
//   2. 可信度只降不升——导出的措辞不得比库里的标签更强。

let exportedMethodJSON = #"""
  {"id":"m-1","workspace_id":"ws-1","key":"differentiate","name":"求导",
   "goal":"对表达式求导并化简。","applicable_when":["需要求一个表达式的导数"],
   "procedure":["按和、积、商与复合的求导法则逐层处理"],
   "failure_modes":["分不清哪一层是内层时会整条算错"],
   "tags":["微积分"],"status":"promoted","version":3,
   "success_count":7,"failure_count":1,"confidence":"verified",
   "signature":{"feature_version":1,"sample_count":2}}
  """#
let exportedMethod = try JSONDecoder().decode(
  MethodCard.self, from: Data(exportedMethodJSON.utf8)
)
let methodDocument = KnowledgeExport.methodDocument(
  exportedMethod, workspaceFolder: "微积分"
)

require(
  methodDocument.relativePath == "微积分/方法卡/differentiate.md",
  "method file name comes from the stable key"
)
// vault 靠 `tool_idea` 认出这是一张 M2 级方法笔记；它必须排在最前。
require(
  methodDocument.text.contains("tags:\n  - tool_idea\n  - 微积分"),
  "method card carries the tool_idea tag first"
)
require(methodDocument.text.contains("feature_version: 1"), "feature version travels")
require(
  methodDocument.body.contains("## 什么时候换"),
  "the switching section is always present"
)
// 不变式 1：同样的输入两次导出必须逐字符一致。
require(
  KnowledgeExport.methodDocument(exportedMethod, workspaceFolder: "微积分").text
    == methodDocument.text,
  "rendering is byte-for-byte stable"
)
// 绝不产出 vault 规范禁止的公式定界符。
require(
  !methodDocument.text.contains(#"\("#) && !methodDocument.text.contains(#"\["#),
  "no forbidden math delimiters"
)

func exampleJSON(conclusion: String, counterexample: String) -> String {
  #"""
  {"id":"ex-8f2a1b2c","workspace_id":"ws-1","problem":"求 x^2*sin(x) 的导数",
   "solution":"用乘积法则逐项求导。","tags":["微积分"],"method_hint":null,
   "reviewed":true,"problem_kind":"chat","math_payload":null,
   "verification":{"status":"verified","summary":"符号验证通过","checks":["diff(x**2, x) = 2*x"],
     "computed":{},"error":null,"conclusion_confidence":"CONCLUSION",
     "process_confidence":"step_checked","counterexample":COUNTEREXAMPLE},
   "extraction":null,"method_drafts":[],"status":"promoted","origin":"conversation",
   "source_attempt_id":null,"reviewed_at":null,"reviewer_note":"","revision":1,
   "created_at":"2026-08-14T10:00:00Z","updated_at":"2026-08-14T10:00:00Z"}
  """#
  .replacingOccurrences(of: "CONCLUSION", with: conclusion)
  .replacingOccurrences(of: "COUNTEREXAMPLE", with: counterexample)
}

let verifiedExample = try JSONDecoder().decode(
  ProblemExample.self, from: Data(exampleJSON(conclusion: "verified", counterexample: "{}").utf8)
)
let exampleDocument = KnowledgeExport.exampleDocument(
  verifiedExample, workspaceFolder: "微积分", methods: [exportedMethod]
)
require(
  exampleDocument.relativePath == "微积分/例题/2026-08-14-ex8f2a1b.md",
  "example file name is date plus a stable short id"
)
require(exampleDocument.text.contains("proof_state: 已证明"), "verified maps to 已证明")
require(
  exampleDocument.body.contains("[[math-harness/微积分/方法卡/differentiate|求导]]"),
  "example links back to its method by full path with an alias"
)
// 被检查的断言是解析器语法，不是排版数学——包成行内代码，顺便躲开 `x**2` 被 Markdown
// 吃掉星号的问题。
require(exampleDocument.body.contains("`diff(x**2, x) = 2*x`"), "claims are inline code")

// 不变式 2：可信度只降不升。
require(KnowledgeExport.proofStateLabel("proof_verified") == "已证明", "proof tier")
require(KnowledgeExport.proofStateLabel("numerically_checked") == "待检查", "numeric tier")
require(KnowledgeExport.proofStateLabel("peer_reviewed") == "待检查", "peer review is not proof")
require(KnowledgeExport.proofStateLabel("unchecked") == "待检查", "unchecked tier")
// `refuted` 没有对应的正面标签——那四档全是正面状态，硬塞进去等于把「找到反例」说成
// 一种证明程度。
require(KnowledgeExport.proofStateLabel("refuted") == nil, "refuted gets no positive label")

let refutedExample = try JSONDecoder().decode(
  ProblemExample.self,
  from: Data(
    exampleJSON(conclusion: "refuted", counterexample: #"{"a":"7","b":"-4"}"#).utf8
  )
)
let refutedDocument = KnowledgeExport.exampleDocument(
  refutedExample, workspaceFolder: "微积分"
)
require(!refutedDocument.text.contains("proof_state:"), "a refuted example claims nothing")
// 反例进 `[!error]`，不进 `[!example]`——vault 的数学笔记规范专门区分这两种 callout。
require(refutedDocument.body.contains("> [!error] 反例"), "counterexamples use the error callout")
require(refutedDocument.body.contains("`a = 7`"), "counterexample values are inline code")

// 正文指纹只归一换行。硬换行的行尾双空格是 Markdown 语义，绝不能顺手清掉。
require(
  KnowledgeExport.bodyDigest("a\r\nb") == KnowledgeExport.bodyDigest("a\nb"),
  "line-ending differences do not count as an edit"
)
require(
  KnowledgeExport.bodyDigest("a\nb\n\n") == KnowledgeExport.bodyDigest("a\nb"),
  "a trailing newline does not count as an edit"
)
require(
  KnowledgeExport.bodyDigest("a  \nb") != KnowledgeExport.bodyDigest("a\nb"),
  "a Markdown hard break is meaningful content"
)

// 文件名安全化。
require(
  KnowledgeExport.sanitizedComponent("微积分/上", fallback: "w") == "微积分-上",
  "path separators are replaced"
)
require(KnowledgeExport.sanitizedComponent("...", fallback: "w") == "w", "empty falls back")
require(
  KnowledgeExport.sanitizedComponent(String(repeating: "长", count: 200), fallback: "w").count
    == 80,
  "over-long names are truncated"
)
// 撞名不猜：报出来让用户改名，不自作主张加后缀。
require(
  KnowledgeExport.folderCollisions(["微积分/上", "微积分:上", "线性代数"]).count == 1,
  "collisions are reported, not silently renamed"
)
require(KnowledgeExport.folderCollisions(["微积分", "线性代数"]).isEmpty, "distinct names pass")

// --- Vault 边界 ------------------------------------------------------
//
// 用户的 vault 里是他自己经年累月的笔记。我们写进去的每一个字节都必须落在
// `math-harness/` 以内——这条边界不能靠调用方自觉，要在拼路径的地方就挡住。

let vaultRoot = URL(fileURLWithPath: "/Users/someone/Vault")
let inside = VaultLayout.destination(root: vaultRoot, relativePath: "微积分/方法卡/differentiate.md")
require(
  inside?.path == "/Users/someone/Vault/math-harness/微积分/方法卡/differentiate.md",
  "a normal relative path lands under our own folder"
)
require(VaultLayout.isInsideOurFolder(inside!, root: vaultRoot), "and is recognised as ours")

// 越界一律拒绝，**不做「清洗后继续」**：修正之后写到哪里只有我们自己知道。
for escaping in [
  "../别人的笔记.md",
  "微积分/../../逃出去.md",
  "/绝对路径.md",
  ".隐藏文件.md",
  "微积分//空段.md",
  "",
] {
  require(
    VaultLayout.destination(root: vaultRoot, relativePath: escaping) == nil,
    "escaping path is refused: \(escaping)"
  )
}

// 拼出来之后还要再验一次：字符串前缀比对会被 `..` 骗过去，所以用标准化路径比。
require(
  !VaultLayout.isInsideOurFolder(
    URL(fileURLWithPath: "/Users/someone/Vault/math-harness/../其它/x.md"),
    root: vaultRoot
  ),
  "a path that walks back out is not ours"
)
require(
  !VaultLayout.isInsideOurFolder(
    URL(fileURLWithPath: "/Users/someone/Vault/其它笔记.md"),
    root: vaultRoot
  ),
  "a sibling of our folder is not ours"
)
// 同名前缀不算在里面：`math-harness-backup` 是别的目录。
require(
  !VaultLayout.isInsideOurFolder(
    URL(fileURLWithPath: "/Users/someone/Vault/math-harness-backup/x.md"),
    root: vaultRoot
  ),
  "a folder sharing our prefix is not ours"
)

// --- 写入策略 --------------------------------------------------------
//
// 本版守的一件事：**不许覆盖用户写的正文**。用户会立刻在正文里补触发信号，而回流要等
// 下一版；自动导出如果覆盖，他第一次写的东西就静默没了。

let draft = methodDocument

// 文件不存在 → 整篇写。
require(
  VaultWriter.decide(document: draft, existingText: nil) == .create(draft.text),
  "an absent file is created")

// 正文还是我们上次写下去的样子 → 内容没变就不写。全量重写会把 vault 的修改时间
// 全刷一遍，Obsidian 的「最近编辑」就废了。
require(
  VaultWriter.decide(document: draft, existingText: draft.text) == .unchanged,
  "an untouched identical file is skipped"
)

// 用户改了正文 → 保留他的正文，只刷新 frontmatter。
// 真实的用户编辑长这样：正文变了，而 frontmatter 里的 `body_sha256` 还是我们上次
// 写下去的那一份——两者对不上，就是有人动过。
let userEdited = draft.text + "\n估不动时 → [[积分比较]]\n"
guard
  case .keepUserBody(let keptText, let sidecar) =
    VaultWriter.decide(document: draft, existingText: userEdited)
else {
  fatalError("Core check failed: an edited body must be kept")
}
require(keptText.contains("[[积分比较]]"), "the user's own text survives")
require(keptText.contains("body_owner: user"), "ownership flips to the user")
require(sidecar == nil, "an unchanged draft produces no side-by-side file")

// 归属是**粘的**。上一步我们把用户的正文原样写了回去，它的指纹当然对得上——没有这个
// 标记的话，下一次导出就会理直气壮地覆盖掉用户写的东西。
guard case .keepUserBody = VaultWriter.decide(document: draft, existingText: keptText)
else {
  fatalError("Core check failed: user ownership must be sticky")
}

// 机器初稿变了 → 另存一份，仍然不碰用户正文。
let changedMethodJSON = exportedMethodJSON.replacingOccurrences(
  of: #""goal":"对表达式求导并化简。""#, with: #""goal":"求导并化简，必要时先换元。""#
)
let changedMethod = try JSONDecoder().decode(
  MethodCard.self, from: Data(changedMethodJSON.utf8)
)
let changedDraft = KnowledgeExport.methodDocument(changedMethod, workspaceFolder: "微积分")
guard
  case .keepUserBody(_, let newSidecar) =
    VaultWriter.decide(document: changedDraft, existingText: keptText)
else {
  fatalError("Core check failed: a changed draft still keeps the user body")
}
require(newSidecar != nil, "a changed draft is offered side by side")

// 认不出形状的文件一律当用户的——可能被整篇重写了，也可能压根不是我们的文件。
guard
  case .keepUserBody(let foreignText, _) =
    VaultWriter.decide(document: draft, existingText: "这是用户自己写的一篇笔记。")
else {
  fatalError("Core check failed: an unrecognised file is treated as the user's")
}
require(foreignText.contains("这是用户自己写的一篇笔记。"), "foreign content is preserved")

// --- 事务 ------------------------------------------------------------

func readBack(_ url: URL) -> String {
  (try? String(contentsOf: url, encoding: .utf8)) ?? ""
}

let sandbox = FileManager.default.temporaryDirectory
  .appendingPathComponent("mh-vault-check-\(UUID().uuidString)")
try FileManager.default.createDirectory(at: sandbox, withIntermediateDirectories: true)
defer { try? FileManager.default.removeItem(at: sandbox) }

let fileA = sandbox.appendingPathComponent("a.md")
let fileB = sandbox.appendingPathComponent("nested/b.md")

let applied = VaultWriter.apply([
  VaultWriter.FileWrite(url: fileA, text: "A1", expectedDigest: nil),
  VaultWriter.FileWrite(url: fileB, text: "B1", expectedDigest: nil),
])
require(applied.status == .applied, "a clean transaction applies")
require(readBack(fileA) == "A1", "first file written")
require(readBack(fileB) == "B1", "nested file written")

// 计划之后文件被别人改过 → 前置检查就拦下，**一个字节都不写**。
try "外部改动".write(to: fileA, atomically: true, encoding: .utf8)
let raced = VaultWriter.apply([
  VaultWriter.FileWrite(
    url: fileA, text: "A2", expectedDigest: VaultWriter.digest(of: Data("A1".utf8))),
  VaultWriter.FileWrite(
    url: fileB, text: "B2", expectedDigest: VaultWriter.digest(of: Data("B1".utf8))),
])
require(raced.status == .preflightFailed, "a raced file fails preflight")
require(raced.written.isEmpty, "preflight failure writes nothing")
// 关键：**同一批里没被改的那个也不许写**。半批写入比整批不写难收拾得多。
require(readBack(fileB) == "B1", "the sibling file is untouched")

// --- 索引与链接 ------------------------------------------------------

// 链接必须写全路径。两个工作区都可能有一张 `differentiate.md`，也各有一份 `索引.md`；
// 只写文件名的话 Obsidian 会在同名文件里任选一个，**而且不报错**。
require(
  KnowledgeExport.wikilink(to: "微积分/方法卡/differentiate.md", alias: "求导")
    == "[[math-harness/微积分/方法卡/differentiate|求导]]",
  "links carry the full path so same-named files cannot collide"
)
let methodWithSources = KnowledgeExport.methodDocument(
  exportedMethod, workspaceFolder: "微积分", sources: [verifiedExample]
)
require(
  methodWithSources.body.contains("[[math-harness/微积分/例题/2026-08-14-ex8f2a1b|"),
  "a method card links to its sources by full path"
)

let workspaceIndex = KnowledgeExport.workspaceIndexDocument(
  workspaceName: "微积分",
  workspaceFolder: "微积分",
  methods: [exportedMethod],
  pendingExamples: [refutedExample],
  staleSignatureCount: 2,
  pendingMerges: ["微积分/方法卡/differentiate.updated.md"]
)
require(
  workspaceIndex.relativePath == "微积分/索引.md", "workspace index path")
require(workspaceIndex.body.contains("### 符号验证"), "cards are grouped by confidence")
require(workspaceIndex.body.contains("（用过 7 次）"), "reuse count is visible")
require(workspaceIndex.body.contains("2 张方法卡的结构特征待重建"), "stale count surfaces")
require(workspaceIndex.body.contains("新初稿等着合并"), "pending merges surface")
require(workspaceIndex.body.contains("## 待复核"), "the review queue surfaces")

// 索引是生成物，但保护规则一视同仁——所以先把话说在前面。
require(
  workspaceIndex.body.contains("这一页由 Math Harness 生成"),
  "generated pages say so up front"
)

let rootIndex = KnowledgeExport.rootIndexDocument(workspaces: [
  (name: "微积分", folder: "微积分", methodCount: 12, pendingCount: 3),
  (name: "线性代数", folder: "线性代数", methodCount: nil, pendingCount: 0),
])
require(rootIndex.relativePath == "MATH-HARNESS.md", "root index path")
require(rootIndex.body.contains("12 张方法卡 · 3 条待复核"), "known counts are reported")
// **不知道就别说。** 写个 0 上去是假的，会让人以为那个工作区空了。
require(!rootIndex.body.contains("0 张方法卡"), "unknown counts are omitted, not faked")
require(rootIndex.body.contains("|线性代数]]"), "the unloaded workspace is still linked")

// --- 端到端：真的写一遍文件 ------------------------------------------
//
// 前面各段验的是决策和渲染。这一段把整条路跑通：建工作区目录、写文件、模拟一次用户
// 编辑、再导出一次，确认改动还在。这是本版最该被自动化守住的场景。

let vault = FileManager.default.temporaryDirectory
  .appendingPathComponent("mh-vault-e2e-\(UUID().uuidString)")
try FileManager.default.createDirectory(at: vault, withIntermediateDirectories: true)
defer { try? FileManager.default.removeItem(at: vault) }

let exporter = KnowledgeExporter(root: vault)
let first = exporter.export(
  workspaceName: "微积分",
  otherWorkspaceNames: ["线性代数"],
  methods: [exportedMethod],
  examples: [verifiedExample],
  pendingExamples: [],
  staleSignatureCount: 0,
  allWorkspaces: [(name: "微积分", methodCount: 1, pendingCount: 0)]
)
require(first.status == .applied, "the first export applies")
require(first.created == 4, "method, example and two indexes are created")

let cardURL = vault.appendingPathComponent("math-harness/微积分/方法卡/differentiate.md")
require(FileManager.default.fileExists(atPath: cardURL.path), "the card landed in our folder")
// 只碰 math-harness/，vault 根目录下不该多出别的东西。
let topLevel = try FileManager.default.contentsOfDirectory(atPath: vault.path)
require(topLevel == ["math-harness"], "nothing is written outside our own folder")

// 什么都没变 → 一个字节都不写。
let second = exporter.export(
  workspaceName: "微积分",
  otherWorkspaceNames: ["线性代数"],
  methods: [exportedMethod],
  examples: [verifiedExample],
  allWorkspaces: [(name: "微积分", methodCount: 1, pendingCount: 0)]
)
require(second.created == 0 && second.updated == 0, "an unchanged export writes nothing")
require(second.skipped == 4, "everything is skipped")

// 用户在正文里补了一条切换关系。
let edited = readBack(cardURL) + "\n估不动时 → [[math-harness/微积分/方法卡/积分比较|积分比较]]\n"
try edited.write(to: cardURL, atomically: true, encoding: .utf8)

let third = exporter.export(
  workspaceName: "微积分",
  otherWorkspaceNames: ["线性代数"],
  methods: [changedMethod],
  examples: [verifiedExample],
  allWorkspaces: [(name: "微积分", methodCount: 1, pendingCount: 0)]
)
require(third.status == .applied, "the third export applies")
let afterEdit = readBack(cardURL)
// **本版最要紧的一条**：用户写的东西还在。
require(afterEdit.contains("积分比较"), "the user's own text survives a later export")
require(afterEdit.contains("body_owner: user"), "ownership stays with the user")
require(third.preserved >= 1, "the export reports what it preserved")
require(!third.pendingMerge.isEmpty, "a changed draft is offered side by side")
require(
  FileManager.default.fileExists(
    atPath: vault.appendingPathComponent(
      "math-harness/微积分/方法卡/differentiate.updated.md"
    ).path
  ),
  "the new draft is written next to it, not over it"
)

// 撞名不猜：报出来让用户改名。
let clashing = exporter.export(
  workspaceName: "微积分/上",
  otherWorkspaceNames: ["微积分:上"],
  methods: [],
  examples: []
)
require(clashing.status == .preflightFailed, "colliding workspace names stop the export")
require(
  clashing.failures.first?.contains("改名") == true,
  "and say what to do about it"
)

print("MathHarnessCore checks passed")
