import Foundation

public struct HealthResponse: Codable, Equatable, Sendable {
  public let status: String
  public let version: String
  /// provider 配置解析失败时的原因；正常时为 nil。
  public let providerConfigError: String?

  enum CodingKeys: String, CodingKey {
    case status, version
    case providerConfigError = "provider_config_error"
  }

  public init(status: String, version: String, providerConfigError: String? = nil) {
    self.status = status
    self.version = version
    self.providerConfigError = providerConfigError
  }
}

/// 连接测试入参。密钥只用于这一次请求，后端不落盘、不记日志、不回显。
public struct ProviderTestRequest: Codable, Sendable {
  public let baseURL: String
  public let model: String
  public let apiKey: String?
  public let timeoutSeconds: Double

  enum CodingKeys: String, CodingKey {
    case baseURL = "base_url"
    case model
    case apiKey = "api_key"
    case timeoutSeconds = "timeout_seconds"
  }

  public init(
    baseURL: String,
    model: String,
    apiKey: String?,
    timeoutSeconds: Double = 20
  ) {
    self.baseURL = baseURL
    self.model = model
    self.apiKey = apiKey
    self.timeoutSeconds = timeoutSeconds
  }
}

public struct ProviderTestResult: Codable, Sendable {
  public let ok: Bool
  public let durationMs: Int
  public let model: String?
  public let sample: String?
  public let error: String?

  enum CodingKeys: String, CodingKey {
    case ok
    case durationMs = "duration_ms"
    case model, sample, error
  }
}

public struct Workspace: Codable, Identifiable, Hashable, Sendable {
  public let id: String
  public let name: String
  public let description: String
  public let createdAt: String

  enum CodingKeys: String, CodingKey {
    case id, name, description
    case createdAt = "created_at"
  }

  public init(id: String, name: String, description: String, createdAt: String) {
    self.id = id
    self.name = name
    self.description = description
    self.createdAt = createdAt
  }
}

public struct WorkspaceCreateRequest: Codable, Equatable, Sendable {
  public let name: String
  public let description: String

  public init(name: String, description: String) {
    self.name = name
    self.description = description
  }
}

public struct ConversationCreateRequest: Codable, Equatable, Sendable {
  public let title: String

  public init(title: String = "") {
    self.title = title
  }
}

public enum ConversationStatus: String, Codable, Sendable {
  case active
  case archived
}

/// 这个对话选定的模型服务。只有档案 ID 和模型名，**不含密钥**。
public struct ConversationProvider: Codable, Equatable, Sendable {
  public let profileID: String
  public let model: String?

  enum CodingKeys: String, CodingKey {
    case profileID = "profile_id"
    case model
  }

  public init(profileID: String, model: String? = nil) {
    self.profileID = profileID
    self.model = model
  }
}

public struct ConversationRenameRequest: Codable, Sendable {
  public let title: String

  public init(title: String) {
    self.title = title
  }
}

public struct Conversation: Codable, Identifiable, Equatable, Sendable {
  public let id: String
  public let workspaceID: String
  public let title: String
  public let summary: String
  public let summaryThroughOrdinal: Int
  public let messageCount: Int
  public let status: ConversationStatus
  public let provider: ConversationProvider?
  public let createdAt: String
  public let updatedAt: String

  enum CodingKeys: String, CodingKey {
    case id, title, summary, status, provider
    case workspaceID = "workspace_id"
    case summaryThroughOrdinal = "summary_through_ordinal"
    case messageCount = "message_count"
    case createdAt = "created_at"
    case updatedAt = "updated_at"
  }
}

public struct ConversationMessage: Codable, Identifiable, Equatable, Sendable {
  public let id: String
  public let workspaceID: String
  public let conversationID: String
  public let turnID: String
  public let ordinal: Int
  public let role: String
  public let kind: String
  public let content: String
  public let provider: String?
  public let model: String?
  /// 非空表示供应商在正文完成前中断；内容只作现场保留，不是已验证答案。
  public let generationError: String?
  public let attemptID: String?
  public let knowledgeDraftID: String?
  public let verificationStatus: String?
  /// 结论轴。为 nil 表示这条回答里没有可机检的内容——那是「没查」，不是「没通过」。
  public let conclusionConfidence: String?
  /// 过程轴。推导站不站得住，和结论对不对是两件事。
  public let processConfidence: String?
  /// 反例。有它用户才分得清「真错」和「缺前提」。
  public let counterexample: [String: String]
  /// 实际被检查的断言。抽错题的风险始终存在，处理方式是让它可见。
  public let checkedClaims: [String]
  public let methodKeys: [String]
  public let createdAt: String

  enum CodingKeys: String, CodingKey {
    case id, ordinal, role, kind, content, provider, model, counterexample
    case workspaceID = "workspace_id"
    case conversationID = "conversation_id"
    case turnID = "turn_id"
    case generationError = "generation_error"
    case attemptID = "attempt_id"
    case knowledgeDraftID = "knowledge_draft_id"
    case verificationStatus = "verification_status"
    case conclusionConfidence = "conclusion_confidence"
    case processConfidence = "process_confidence"
    case checkedClaims = "checked_claims"
    case methodKeys = "method_keys"
    case createdAt = "created_at"
  }

  /// 手写解码，因为 Swift 合成的 `Decodable` **不会**使用属性默认值：缺字段就抛错。
  ///
  /// v0.15 之前的 helper 不返回这几个字段，而 App 和 helper 是各自升级的——照合成
  /// 版走，用户装了新 App 配旧 helper 就会在解码整条消息时崩掉，连回答都看不到。
  public init(from decoder: Decoder) throws {
    let container = try decoder.container(keyedBy: CodingKeys.self)
    id = try container.decode(String.self, forKey: .id)
    workspaceID = try container.decode(String.self, forKey: .workspaceID)
    conversationID = try container.decode(String.self, forKey: .conversationID)
    turnID = try container.decode(String.self, forKey: .turnID)
    ordinal = try container.decode(Int.self, forKey: .ordinal)
    role = try container.decode(String.self, forKey: .role)
    kind = try container.decode(String.self, forKey: .kind)
    content = try container.decode(String.self, forKey: .content)
    provider = try container.decodeIfPresent(String.self, forKey: .provider)
    model = try container.decodeIfPresent(String.self, forKey: .model)
    generationError = try container.decodeIfPresent(String.self, forKey: .generationError)
    attemptID = try container.decodeIfPresent(String.self, forKey: .attemptID)
    knowledgeDraftID = try container.decodeIfPresent(
      String.self, forKey: .knowledgeDraftID)
    verificationStatus = try container.decodeIfPresent(
      String.self, forKey: .verificationStatus)
    conclusionConfidence = try container.decodeIfPresent(
      String.self, forKey: .conclusionConfidence)
    processConfidence = try container.decodeIfPresent(
      String.self, forKey: .processConfidence)
    counterexample =
      try container.decodeIfPresent([String: String].self, forKey: .counterexample) ?? [:]
    checkedClaims =
      try container.decodeIfPresent([String].self, forKey: .checkedClaims) ?? []
    methodKeys = try container.decodeIfPresent([String].self, forKey: .methodKeys) ?? []
    createdAt = try container.decode(String.self, forKey: .createdAt)
  }
}

extension ConversationMessage {
  /// 这条回复是被用户按停止中断的，而不是断线。
  ///
  /// 两者在数据上完全一样——都是中断，都不检查、不入库。差别只在**怎么说**：
  /// 用户自己按的停止不该弹一个「连接中断」的错误提示。
  public var wasStoppedByUser: Bool {
    generationError?.hasPrefix(ConversationMessage.stoppedByUserPrefix) ?? false
  }

  /// 与后端 `math_harness.conversation.STOPPED_BY_USER` 对应的前缀。
  public static let stoppedByUserPrefix = "StoppedByUser"
}

/// 结论轴的展示形态。后端还会加档位，所以未知值必须能安全落地而不是崩掉。
public enum ConclusionConfidence: String, Sendable {
  case proofVerified = "proof_verified"
  case verified
  case numericallyChecked = "numerically_checked"
  case crossChecked = "cross_checked"
  case peerReviewed = "peer_reviewed"
  case unchecked
  case refuted

  public var title: String {
    switch self {
    case .proofVerified: "形式化证明"
    case .verified: "符号验证"
    case .numericallyChecked: "数值检验"
    case .crossChecked: "独立重算一致"
    case .peerReviewed: "异模型复核"
    case .unchecked: "未检查"
    case .refuted: "已找到反例"
    }
  }

  /// 一句话说明这个档位到底意味着什么。
  ///
  /// 分级只有在用户看得懂的时候才有用。「数值检验」听着像「验过了」，但它是强证据
  /// 不是证明——不写清楚，用户就会把它当成和符号验证一回事。
  public var explanation: String {
    switch self {
    case .proofVerified: "形式化证明通过。"
    case .verified: "SymPy 符号化简判定两边恒等，这是确定性结论。"
    case .numericallyChecked: "在多组随机取值下都成立。这是强证据，不是证明。"
    case .crossChecked: "另一个独立引擎算出了一致的结果。"
    case .peerReviewed: "换一个模型复核通过。模型意见不等于验证。"
    case .unchecked: "这条回答里没有能自动检查的内容。"
    case .refuted: "找到了使等式不成立的取值。"
    }
  }

  public var symbol: String {
    switch self {
    case .proofVerified, .verified: "checkmark.seal.fill"
    case .numericallyChecked: "function"
    case .crossChecked: "arrow.triangle.2.circlepath"
    case .peerReviewed: "person.2"
    case .unchecked: "questionmark.circle"
    case .refuted: "xmark.octagon.fill"
    }
  }
}

/// 过程轴的展示形态。
public enum ProcessConfidence: String, Sendable {
  case stepChecked = "step_checked"
  case stepFailed = "step_failed"
  case stepUnchecked = "step_unchecked"

  public var title: String {
    switch self {
    case .stepChecked: "推导逐步通过"
    case .stepFailed: "推导有步骤不成立"
    case .stepUnchecked: "推导未逐步检查"
    }
  }

  public var explanation: String {
    switch self {
    case .stepChecked: "每一个中间步骤都单独验过。"
    case .stepFailed: "结论可能是对的，但中间某一步不成立——这样的推导不会用来学方法。"
    case .stepUnchecked: "这段解答没有可以逐步检查的步骤。"
    }
  }
}

public struct ConversationTurnRequest: Codable, Equatable, Sendable {
  public let message: String
  public let turnID: String
  public let tags: [String]
  public let topK: Int
  public let mathTarget: SolveMathTargetRequest?
  public let maxOutputTokens: Int

  enum CodingKeys: String, CodingKey {
    case message, tags
    case turnID = "turn_id"
    case topK = "top_k"
    case mathTarget = "math_target"
    case maxOutputTokens = "max_output_tokens"
  }

  public init(
    message: String,
    turnID: String = UUID().uuidString,
    tags: [String] = [],
    topK: Int = 5,
    mathTarget: SolveMathTargetRequest? = nil,
    maxOutputTokens: Int = 3_000
  ) {
    self.message = message
    self.turnID = turnID
    self.tags = tags
    self.topK = topK
    self.mathTarget = mathTarget
    self.maxOutputTokens = maxOutputTokens
  }
}

public enum MemoryKind: String, Codable, CaseIterable, Identifiable, Sendable {
  case profile
  case learningGoal = "learning_goal"
  case explanationPreference = "explanation_preference"
  case topicContext = "topic_context"
  case manualNote = "manual_note"

  public var id: String { rawValue }
}

public enum MemoryStatus: String, Codable, CaseIterable, Identifiable, Sendable {
  case active
  case superseded
  case archived

  public var id: String { rawValue }
}

public struct MemoryItem: Codable, Identifiable, Equatable, Sendable {
  public let id: String
  public let workspaceID: String
  public let kind: MemoryKind
  public let content: String
  public let tags: [String]
  public let status: MemoryStatus
  public let pinned: Bool
  public let source: String
  public let conversationID: String?
  public let sourceMessageID: String?
  public let evidence: String?
  public let supersedesID: String?
  public let createdAt: String
  public let updatedAt: String

  enum CodingKeys: String, CodingKey {
    case id, kind, content, tags, status, pinned, source, evidence
    case workspaceID = "workspace_id"
    case conversationID = "conversation_id"
    case sourceMessageID = "source_message_id"
    case supersedesID = "supersedes_id"
    case createdAt = "created_at"
    case updatedAt = "updated_at"
  }
}

public struct MemoryCreateRequest: Codable, Equatable, Sendable {
  public let content: String
  public let kind: MemoryKind
  public let tags: [String]
  public let pinned: Bool

  public init(
    content: String,
    kind: MemoryKind = .manualNote,
    tags: [String] = [],
    pinned: Bool = false
  ) {
    self.content = content
    self.kind = kind
    self.tags = tags
    self.pinned = pinned
  }
}

public struct MemoryUpdateRequest: Codable, Equatable, Sendable {
  public let content: String?
  public let kind: MemoryKind?
  public let tags: [String]?
  public let pinned: Bool?
  public let status: MemoryStatus?

  public init(
    content: String? = nil,
    kind: MemoryKind? = nil,
    tags: [String]? = nil,
    pinned: Bool? = nil,
    status: MemoryStatus? = nil
  ) {
    self.content = content
    self.kind = kind
    self.tags = tags
    self.pinned = pinned
    self.status = status
  }
}

public struct MemorySettings: Codable, Equatable, Sendable {
  public let workspaceID: String
  public let automaticExtractionEnabled: Bool
  public let updatedAt: String

  enum CodingKeys: String, CodingKey {
    case workspaceID = "workspace_id"
    case automaticExtractionEnabled = "automatic_extraction_enabled"
    case updatedAt = "updated_at"
  }
}

public struct MemorySettingsUpdateRequest: Codable, Equatable, Sendable {
  public let automaticExtractionEnabled: Bool

  enum CodingKeys: String, CodingKey {
    case automaticExtractionEnabled = "automatic_extraction_enabled"
  }

  public init(automaticExtractionEnabled: Bool) {
    self.automaticExtractionEnabled = automaticExtractionEnabled
  }
}

public struct MemoryExtractionJob: Codable, Identifiable, Equatable, Sendable {
  public let id: String
  public let workspaceID: String
  public let conversationID: String
  public let fromOrdinal: Int
  public let throughOrdinal: Int
  public let sourceRevision: String
  public let status: String
  public let attempts: Int
  public let provider: String?
  public let model: String?
  public let extractedCount: Int
  public let inputTokens: Int?
  public let outputTokens: Int?
  public let durationMS: Int
  public let error: String?
  public let createdAt: String
  public let startedAt: String?
  public let completedAt: String?

  enum CodingKeys: String, CodingKey {
    case id, status, attempts, provider, model, error
    case workspaceID = "workspace_id"
    case conversationID = "conversation_id"
    case fromOrdinal = "from_ordinal"
    case throughOrdinal = "through_ordinal"
    case sourceRevision = "source_revision"
    case extractedCount = "extracted_count"
    case inputTokens = "input_tokens"
    case outputTokens = "output_tokens"
    case durationMS = "duration_ms"
    case createdAt = "created_at"
    case startedAt = "started_at"
    case completedAt = "completed_at"
  }
}

public struct MemoryHealth: Codable, Equatable, Sendable {
  public let workspaceID: String
  public let automaticExtractionEnabled: Bool
  public let extractorAvailable: Bool
  public let queuedCount: Int
  public let runningCount: Int
  public let failedCount: Int
  public let lastSuccessAt: String?
  public let lastErrorAt: String?
  public let lastError: String?

  enum CodingKeys: String, CodingKey {
    case workspaceID = "workspace_id"
    case automaticExtractionEnabled = "automatic_extraction_enabled"
    case extractorAvailable = "extractor_available"
    case queuedCount = "queued_count"
    case runningCount = "running_count"
    case failedCount = "failed_count"
    case lastSuccessAt = "last_success_at"
    case lastErrorAt = "last_error_at"
    case lastError = "last_error"
  }
}

public struct MemoryBackfillResult: Codable, Equatable, Sendable {
  public let workspaceID: String
  public let queuedJobs: [MemoryExtractionJob]
  public let skippedConversationCount: Int

  enum CodingKeys: String, CodingKey {
    case workspaceID = "workspace_id"
    case queuedJobs = "queued_jobs"
    case skippedConversationCount = "skipped_conversation_count"
  }
}

public enum ImportReviewPolicy: String, Codable, CaseIterable, Identifiable, Sendable {
  case pending
  case preserve

  public var id: String { rawValue }
}

public enum ImportExtractorPolicy: String, Codable, CaseIterable, Identifiable, Sendable {
  case rules
  case configured

  public var id: String { rawValue }
}

public struct BulkExampleImportRequest: Codable, Equatable, Sendable {
  public let content: String
  public let fileFormat: String
  public let reviewPolicy: ImportReviewPolicy
  public let extractorPolicy: ImportExtractorPolicy
  public let commit: Bool
  public let sourceName: String

  enum CodingKeys: String, CodingKey {
    case content, commit
    case fileFormat = "file_format"
    case reviewPolicy = "review_policy"
    case extractorPolicy = "extractor_policy"
    case sourceName = "source_name"
  }

  public init(
    content: String,
    fileFormat: String = "auto",
    reviewPolicy: ImportReviewPolicy = .pending,
    extractorPolicy: ImportExtractorPolicy = .rules,
    commit: Bool = false,
    sourceName: String
  ) {
    self.content = content
    self.fileFormat = fileFormat
    self.reviewPolicy = reviewPolicy
    self.extractorPolicy = extractorPolicy
    self.commit = commit
    self.sourceName = sourceName
  }
}

public struct BulkImportItemResult: Codable, Equatable, Identifiable, Sendable {
  public var id: Int { index }

  public let index: Int
  public let status: String
  public let problemPreview: String
  public let fingerprint: String?
  public let verification: VerificationReport?
  public let methodKeys: [String]
  public let exampleID: String?
  public let errors: [String]

  enum CodingKeys: String, CodingKey {
    case index, status, fingerprint, verification, errors
    case problemPreview = "problem_preview"
    case methodKeys = "method_keys"
    case exampleID = "example_id"
  }
}

public struct BulkExampleImportResult: Codable, Equatable, Sendable {
  public let sourceName: String
  public let detectedFormat: String
  public let commitRequested: Bool
  public let committed: Bool
  public let canCommit: Bool
  public let totalCount: Int
  public let readyCount: Int
  public let duplicateCount: Int
  public let invalidCount: Int
  public let importedCount: Int
  public let items: [BulkImportItemResult]

  enum CodingKeys: String, CodingKey {
    case items, committed
    case sourceName = "source_name"
    case detectedFormat = "detected_format"
    case commitRequested = "commit_requested"
    case canCommit = "can_commit"
    case totalCount = "total_count"
    case readyCount = "ready_count"
    case duplicateCount = "duplicate_count"
    case invalidCount = "invalid_count"
    case importedCount = "imported_count"
  }
}

public struct WorkspaceRestoreResult: Codable, Equatable, Sendable {
  public let workspace: Workspace
  public let sourceWorkspaceID: String
  public let sourceAppVersion: String
  public let archiveFormatVersion: Int
  public let restoredRecordCounts: [String: Int]

  enum CodingKeys: String, CodingKey {
    case workspace
    case sourceWorkspaceID = "source_workspace_id"
    case sourceAppVersion = "source_app_version"
    case archiveFormatVersion = "archive_format_version"
    case restoredRecordCounts = "restored_record_counts"
  }
}

public enum VerificationMode: String, Codable, CaseIterable, Identifiable, Sendable {
  case asymptoticExpansion = "asymptotic_expansion"
  case asymptoticEquivalence = "asymptotic_equivalence"
  case exactEquivalence = "exact_equivalence"
  case limit

  public var id: String { rawValue }

  public var displayName: String {
    switch self {
    case .asymptoticExpansion: "渐进展开"
    case .asymptoticEquivalence: "渐进等价"
    case .exactEquivalence: "精确等价"
    case .limit: "极限"
    }
  }
}

public struct SolveMathTargetRequest: Codable, Equatable, Sendable {
  public let expression: String
  public let variable: String
  public let parameters: [String]
  public let assumptions: [String: [String]]
  public let point: String
  public let direction: String
  public let mode: VerificationMode
  public let remainderPower: Int?

  enum CodingKeys: String, CodingKey {
    case expression, variable, parameters, assumptions, point, direction, mode
    case remainderPower = "remainder_power"
  }

  public init(
    expression: String,
    variable: String = "x",
    parameters: [String] = [],
    assumptions: [String: [String]] = [:],
    point: String = "oo",
    direction: String = "two_sided",
    mode: VerificationMode = .asymptoticExpansion,
    remainderPower: Int? = nil
  ) {
    self.expression = expression
    self.variable = variable
    self.parameters = parameters
    self.assumptions = assumptions
    self.point = point
    self.direction = direction
    self.mode = mode
    self.remainderPower = remainderPower
  }
}

public struct MathPayloadRequest: Codable, Equatable, Sendable {
  public let expression: String
  public let expected: String
  public let variable: String
  public let parameters: [String]
  public let assumptions: [String: [String]]
  public let point: String
  public let direction: String
  public let mode: VerificationMode
  public let remainderPower: Int?

  enum CodingKeys: String, CodingKey {
    case expression, expected, variable, parameters, assumptions, point, direction, mode
    case remainderPower = "remainder_power"
  }

  public init(
    expression: String,
    expected: String,
    variable: String = "x",
    parameters: [String] = [],
    assumptions: [String: [String]] = [:],
    point: String = "oo",
    direction: String = "two_sided",
    mode: VerificationMode = .asymptoticExpansion,
    remainderPower: Int? = nil
  ) {
    self.expression = expression
    self.expected = expected
    self.variable = variable
    self.parameters = parameters
    self.assumptions = assumptions
    self.point = point
    self.direction = direction
    self.mode = mode
    self.remainderPower = remainderPower
  }
}

public struct MathTargetDraftRequest: Codable, Equatable, Sendable {
  public let problem: String

  public init(problem: String) {
    self.problem = problem
  }
}

public struct MathTargetDraftResult: Codable, Equatable, Sendable {
  public let target: SolveMathTargetRequest?
  public let status: String
  public let provider: String
  public let model: String?
  public let confidence: Double
  public let summary: String
  public let warnings: [String]
  public let fallbackUsed: Bool
  public let error: String?
  public let requiresConfirmation: Bool

  enum CodingKeys: String, CodingKey {
    case target, status, provider, model, confidence, summary, warnings, error
    case fallbackUsed = "fallback_used"
    case requiresConfirmation = "requires_confirmation"
  }
}

public struct SolveRequest: Codable, Equatable, Sendable {
  public let problem: String
  public let tags: [String]
  public let topK: Int
  public let mathTarget: SolveMathTargetRequest?
  public let maxOutputTokens: Int

  enum CodingKeys: String, CodingKey {
    case problem, tags
    case topK = "top_k"
    case mathTarget = "math_target"
    case maxOutputTokens = "max_output_tokens"
  }

  public init(
    problem: String,
    tags: [String] = [],
    topK: Int = 5,
    mathTarget: SolveMathTargetRequest? = nil,
    maxOutputTokens: Int = 3_000
  ) {
    self.problem = problem
    self.tags = tags
    self.topK = topK
    self.mathTarget = mathTarget
    self.maxOutputTokens = maxOutputTokens
  }
}

public struct CandidateStep: Codable, Equatable, Sendable {
  public let explanation: String
  public let expression: String?
}

public struct CandidateSolution: Codable, Equatable, Sendable {
  public let answerKind: String
  public let answerText: String
  public let answerExpression: String?
  public let steps: [CandidateStep]
  public let usedMethodKeys: [String]
  public let assumptions: [String]
  public let confidence: Double

  enum CodingKeys: String, CodingKey {
    case steps, assumptions, confidence
    case answerKind = "answer_kind"
    case answerText = "answer_text"
    case answerExpression = "answer_expression"
    case usedMethodKeys = "used_method_keys"
  }
}

public struct VerificationReport: Codable, Equatable, Sendable {
  public let status: String
  public let summary: String
  public let checks: [String]
  public let computed: [String: String]
  public let error: String?
  /// 结论轴。为 nil 表示这条记录是双轴可信度之前写的。
  public let conclusionConfidence: String?
  /// 过程轴。结论对不对和推导站不站得住是两件事。
  public let processConfidence: String?
  /// 反例。有它用户才分得清「真错」和「缺前提」——这套检查最有用的产物就是它。
  public let counterexample: [String: String]

  enum CodingKeys: String, CodingKey {
    case status, summary, checks, computed, error, counterexample
    case conclusionConfidence = "conclusion_confidence"
    case processConfidence = "process_confidence"
  }

  /// 手写解码，因为 Swift 合成的 `Decodable` **不会**使用属性默认值：缺字段就抛错。
  ///
  /// 和 `ConversationMessage` 同一个理由——App 和 helper 各自升级，新 App 配旧 helper
  /// 必须还能读出例题，不能因为多了几个字段就整条解码失败。
  public init(from decoder: Decoder) throws {
    let container = try decoder.container(keyedBy: CodingKeys.self)
    status = try container.decode(String.self, forKey: .status)
    summary = try container.decode(String.self, forKey: .summary)
    checks = try container.decodeIfPresent([String].self, forKey: .checks) ?? []
    computed =
      try container.decodeIfPresent([String: String].self, forKey: .computed) ?? [:]
    error = try container.decodeIfPresent(String.self, forKey: .error)
    conclusionConfidence = try container.decodeIfPresent(
      String.self, forKey: .conclusionConfidence
    )
    processConfidence = try container.decodeIfPresent(
      String.self, forKey: .processConfidence
    )
    counterexample =
      try container.decodeIfPresent([String: String].self, forKey: .counterexample) ?? [:]
  }

  public init(
    status: String,
    summary: String,
    checks: [String] = [],
    computed: [String: String] = [:],
    error: String? = nil,
    conclusionConfidence: String? = nil,
    processConfidence: String? = nil,
    counterexample: [String: String] = [:]
  ) {
    self.status = status
    self.summary = summary
    self.checks = checks
    self.computed = computed
    self.error = error
    self.conclusionConfidence = conclusionConfidence
    self.processConfidence = processConfidence
    self.counterexample = counterexample
  }
}

public struct MethodExtractionSummary: Codable, Equatable, Sendable {
  public let provider: String
  public let model: String?
  public let status: String
  public let extractedMethodKeys: [String]

  enum CodingKeys: String, CodingKey {
    case provider, model, status
    case extractedMethodKeys = "extracted_method_keys"
  }
}

public struct MethodDraftPreview: Codable, Equatable, Sendable {
  public let key: String
  public let name: String
  public let goal: String
  public let applicableWhen: [String]
  public let procedure: [String]
  public let failureModes: [String]
  public let tags: [String]

  enum CodingKeys: String, CodingKey {
    case key, name, goal, procedure, tags
    case applicableWhen = "applicable_when"
    case failureModes = "failure_modes"
  }
}

public struct ProblemExample: Codable, Identifiable, Equatable, Sendable {
  public let id: String
  public let workspaceID: String
  public let problem: String
  public let solution: String
  public let tags: [String]
  public let methodHint: String?
  public let reviewed: Bool
  public let problemKind: String
  public let mathPayload: MathPayloadRequest?
  public let verification: VerificationReport
  public let extraction: MethodExtractionSummary?
  public let methodDrafts: [MethodDraftPreview]
  public let status: String
  public let origin: String
  public let sourceAttemptID: String?
  public let reviewedAt: String?
  public let reviewerNote: String
  public let revision: Int
  public let createdAt: String
  public let updatedAt: String

  enum CodingKeys: String, CodingKey {
    case id, problem, solution, tags, reviewed, verification, extraction, status, origin
    case revision
    case workspaceID = "workspace_id"
    case methodHint = "method_hint"
    case problemKind = "problem_kind"
    case mathPayload = "math_payload"
    case methodDrafts = "method_drafts"
    case sourceAttemptID = "source_attempt_id"
    case reviewedAt = "reviewed_at"
    case reviewerNote = "reviewer_note"
    case createdAt = "created_at"
    case updatedAt = "updated_at"
  }
}

/// 结构签名的版本健康度。
///
/// `stale` 大于零意味着这些卡片在结构检索里暂时是「关着的」——它们的签名来自旧版本的
/// 特征提取器，和现在算出来的查询特征不在同一个空间里。不比是对的：跨版本比出来的
/// 相似度没有意义，而且不会报错。
public struct SignatureHealth: Codable, Equatable, Sendable {
  public let stale: Int
  public let featureVersion: Int

  enum CodingKeys: String, CodingKey {
    case stale
    case featureVersion = "feature_version"
  }
}

public struct SignatureRebuildResult: Codable, Equatable, Sendable {
  public let rebuilt: Int
}

public struct MethodCard: Codable, Identifiable, Equatable, Sendable {
  public let id: String
  public let workspaceID: String
  public let key: String
  public let name: String
  public let goal: String
  public let applicableWhen: [String]
  public let procedure: [String]
  public let failureModes: [String]
  public let tags: [String]
  public let status: String
  public let version: Int
  public let successCount: Int
  public let failureCount: Int
  /// 支撑这张卡的例题里**最强**的那一档结论可信度。nil 表示这张卡建于双轴之前。
  public let confidence: String?
  /// 结构签名。导出只读其中的 `feature_version`，其余是检索内部用的计数。
  public let signature: [String: JSONValue]

  enum CodingKeys: String, CodingKey {
    case id, key, name, goal, procedure, tags, status, version, confidence, signature
    case workspaceID = "workspace_id"
    case applicableWhen = "applicable_when"
    case failureModes = "failure_modes"
    case successCount = "success_count"
    case failureCount = "failure_count"
  }

  public init(from decoder: Decoder) throws {
    let container = try decoder.container(keyedBy: CodingKeys.self)
    id = try container.decode(String.self, forKey: .id)
    workspaceID = try container.decode(String.self, forKey: .workspaceID)
    key = try container.decode(String.self, forKey: .key)
    name = try container.decode(String.self, forKey: .name)
    goal = try container.decode(String.self, forKey: .goal)
    applicableWhen =
      try container.decodeIfPresent([String].self, forKey: .applicableWhen) ?? []
    procedure = try container.decodeIfPresent([String].self, forKey: .procedure) ?? []
    failureModes =
      try container.decodeIfPresent([String].self, forKey: .failureModes) ?? []
    tags = try container.decodeIfPresent([String].self, forKey: .tags) ?? []
    status = try container.decode(String.self, forKey: .status)
    version = try container.decodeIfPresent(Int.self, forKey: .version) ?? 1
    successCount = try container.decodeIfPresent(Int.self, forKey: .successCount) ?? 0
    failureCount = try container.decodeIfPresent(Int.self, forKey: .failureCount) ?? 0
    confidence = try container.decodeIfPresent(String.self, forKey: .confidence)
    signature =
      try container.decodeIfPresent([String: JSONValue].self, forKey: .signature) ?? [:]
  }
}

/// 只够读一份自由字典的最小 JSON 值。结构签名里除了版本号我们什么都不解释。
public enum JSONValue: Codable, Equatable, Sendable {
  case string(String)
  case number(Double)
  case bool(Bool)
  case object([String: JSONValue])
  case array([JSONValue])
  case null

  public var intValue: Int? {
    if case .number(let value) = self { return Int(value) }
    return nil
  }

  public init(from decoder: Decoder) throws {
    let container = try decoder.singleValueContainer()
    if container.decodeNil() {
      self = .null
    } else if let value = try? container.decode(Bool.self) {
      self = .bool(value)
    } else if let value = try? container.decode(Double.self) {
      self = .number(value)
    } else if let value = try? container.decode(String.self) {
      self = .string(value)
    } else if let value = try? container.decode([String: JSONValue].self) {
      self = .object(value)
    } else if let value = try? container.decode([JSONValue].self) {
      self = .array(value)
    } else {
      self = .null
    }
  }

  public func encode(to encoder: Encoder) throws {
    var container = encoder.singleValueContainer()
    switch self {
    case .string(let value): try container.encode(value)
    case .number(let value): try container.encode(value)
    case .bool(let value): try container.encode(value)
    case .object(let value): try container.encode(value)
    case .array(let value): try container.encode(value)
    case .null: try container.encodeNil()
    }
  }
}

public struct MethodMatch: Codable, Equatable, Sendable {
  public let method: MethodCard
  public let score: Double
  public let reasons: [String]
}

public struct SolutionGenerationTrace: Codable, Equatable, Sendable {
  public let provider: String
  public let model: String?
  public let fallbackUsed: Bool
  public let verificationFallbackUsed: Bool
  public let correctionAttempted: Bool
  public let correctionSucceeded: Bool
  public let durationMS: Int
  public let error: String?

  enum CodingKeys: String, CodingKey {
    case provider, model, error
    case fallbackUsed = "fallback_used"
    case verificationFallbackUsed = "verification_fallback_used"
    case correctionAttempted = "correction_attempted"
    case correctionSucceeded = "correction_succeeded"
    case durationMS = "duration_ms"
  }
}

public struct SolutionAttempt: Codable, Identifiable, Equatable, Sendable {
  public let id: String
  public let workspaceID: String
  public let problem: String
  public let tags: [String]
  public let problemKind: String
  public let recommendedMethods: [MethodMatch]
  public let candidate: CandidateSolution?
  public let generation: SolutionGenerationTrace
  public let verification: VerificationReport
  public let status: String
  public let feedbackMethodKeys: [String]
  public let correctionOf: String?
  public let createdAt: String

  enum CodingKeys: String, CodingKey {
    case id, problem, tags, candidate, generation, verification, status
    case workspaceID = "workspace_id"
    case problemKind = "problem_kind"
    case recommendedMethods = "recommended_methods"
    case feedbackMethodKeys = "feedback_method_keys"
    case correctionOf = "correction_of"
    case createdAt = "created_at"
  }
}

public struct ConversationTurnResult: Codable, Equatable, Sendable {
  public let conversation: Conversation
  public let userMessage: ConversationMessage
  public let assistantMessage: ConversationMessage
  public let attempt: SolutionAttempt?
  public let knowledgeDraft: ProblemExample?
  public let summaryUpdated: Bool
  public let memoryJob: MemoryExtractionJob?

  enum CodingKeys: String, CodingKey {
    case conversation, attempt
    case userMessage = "user_message"
    case assistantMessage = "assistant_message"
    case knowledgeDraft = "knowledge_draft"
    case summaryUpdated = "summary_updated"
    case memoryJob = "memory_job"
  }
}

public struct MethodStatusRequest: Codable, Equatable, Sendable {
  public let status: String

  public init(status: String) {
    self.status = status
  }
}

public enum ExampleReviewDecision: String, Codable, Sendable {
  case approve
  case reject
}

public struct ExampleReviewRequest: Codable, Equatable, Sendable {
  public let decision: ExampleReviewDecision
  public let expectedRevision: Int
  public let reviewerNote: String

  enum CodingKeys: String, CodingKey {
    case decision
    case expectedRevision = "expected_revision"
    case reviewerNote = "reviewer_note"
  }

  public init(
    decision: ExampleReviewDecision,
    expectedRevision: Int,
    reviewerNote: String = ""
  ) {
    self.decision = decision
    self.expectedRevision = expectedRevision
    self.reviewerNote = reviewerNote
  }
}

public struct ExampleReviewResult: Codable, Equatable, Sendable {
  public let example: ProblemExample
  public let learnedMethods: [MethodCard]

  enum CodingKeys: String, CodingKey {
    case example
    case learnedMethods = "learned_methods"
  }
}

public struct ExampleDraftUpdateRequest: Codable, Equatable, Sendable {
  public let expectedRevision: Int
  public let problem: String
  public let solution: String
  public let tags: [String]
  public let methodHint: String?
  public let mathPayload: MathPayloadRequest?

  enum CodingKeys: String, CodingKey {
    case problem, solution, tags
    case expectedRevision = "expected_revision"
    case methodHint = "method_hint"
    case mathPayload = "math_payload"
  }

  public init(
    expectedRevision: Int,
    problem: String,
    solution: String,
    tags: [String],
    methodHint: String?,
    mathPayload: MathPayloadRequest?
  ) {
    self.expectedRevision = expectedRevision
    self.problem = problem
    self.solution = solution
    self.tags = tags
    self.methodHint = methodHint
    self.mathPayload = mathPayload
  }
}

public struct ExampleDraftUpdateResult: Codable, Equatable, Sendable {
  public let example: ProblemExample
  public let learnedMethods: [MethodCard]

  enum CodingKeys: String, CodingKey {
    case example
    case learnedMethods = "learned_methods"
  }
}

public struct BackendReady: Codable, Equatable, Sendable {
  public let baseURL: String
  public let pid: Int
  public let port: Int
  public let version: String

  enum CodingKeys: String, CodingKey {
    case pid, port, version
    case baseURL = "base_url"
  }
}

struct APIErrorEnvelope: Codable, Sendable {
  let detail: String
}
